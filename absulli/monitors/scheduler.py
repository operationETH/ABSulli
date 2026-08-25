import asyncio
import logging
from datetime import timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from absulli import __version__
from absulli.core.config import Settings
from absulli.core.setup_state import get_setup_setting, is_setup_complete, set_setup_setting
from absulli.core.time import utcnow, utcnow_iso
from absulli.database.models import LoginLog
from absulli.database.session import SessionLocal
from absulli.http.abs_client import AudiobookshelfClient
from absulli.monitors.activity import ActivityMonitor
from absulli.monitors.history import HistoryMonitor
from absulli.notifiers.manager import NotificationManager
from absulli.web.update_check import CACHE_TTL_SECONDS, refresh_update_status

log = logging.getLogger(__name__)


class AbsulliScheduler:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.scheduler = AsyncIOScheduler()
        self.client = AudiobookshelfClient(settings)
        self.notifier = NotificationManager(settings)
        self.activity = ActivityMonitor(self.client, self.notifier)
        self.history = HistoryMonitor(self.client, self.notifier)

    def _ready_to_poll(self) -> bool:
        if not self.settings.auth_enabled:
            return True
        try:
            return is_setup_complete()
        except Exception:
            return False

    async def _record_abs_reachable(self, db, reachable: bool) -> None:
        try:
            previous = get_setup_setting("abs_reachable", "")
            current = "true" if reachable else "false"
            set_setup_setting("abs_reachable", current)
            if reachable:
                set_setup_setting("abs_last_success_at", utcnow_iso())
            else:
                set_setup_setting("abs_last_failure_at", utcnow_iso())
            if previous == "true" and not reachable:
                await self.notifier.notify(
                    db,
                    "abs_connection_failed",
                    "Audiobookshelf connection failed",
                    "ABSulli cannot reach Audiobookshelf.",
                )
            elif previous == "false" and reachable:
                await self.notifier.notify(
                    db,
                    "abs_connection_restored",
                    "Audiobookshelf connection restored",
                    "ABSulli can reach Audiobookshelf again.",
                )
        except Exception as exc:
            log.debug("Failed to update ABS reachability state: %s", exc)

    async def prune_login_logs(self) -> None:
        cutoff = utcnow() - timedelta(days=90)
        db = SessionLocal()
        try:
            deleted = db.query(LoginLog).filter(LoginLog.created_at < cutoff).delete()
            db.commit()
            log.debug("Login log pruning complete: %s deleted", deleted)
        except Exception as exc:
            db.rollback()
            log.warning("Login log pruning failed: %s", exc)
        finally:
            db.close()

    async def refresh_update_status(self) -> None:
        await asyncio.to_thread(refresh_update_status, self.settings, __version__)

    async def poll_activity(self) -> None:
        if not self._ready_to_poll():
            log.debug("Skipping activity poll until first-run setup is complete")
            return

        db = SessionLocal()
        try:
            count = await self.activity.poll(db)
            await self._record_abs_reachable(db, True)
            log.debug("Activity poll complete: %s active", count)
        except Exception as exc:
            await self._record_abs_reachable(db, False)
            log.warning("Activity poll failed: %s", exc)
        finally:
            db.close()

    async def poll_history(self) -> None:
        if not self._ready_to_poll():
            log.debug("Skipping history poll until first-run setup is complete")
            return

        db = SessionLocal()
        try:
            imported = await self.history.poll(db)
            await self._record_abs_reachable(db, True)
            log.debug("History poll complete: %s imported", imported)
        except Exception as exc:
            await self._record_abs_reachable(db, False)
            log.warning("History poll failed: %s", exc)
        finally:
            db.close()

    def start(self) -> None:
        self.scheduler.add_job(
            self.poll_activity,
            "interval",
            seconds=max(3, self.settings.effective_abs_poll_interval),
            id="activity_poll",
            replace_existing=True,
            max_instances=1,
        )
        self.scheduler.add_job(
            self.poll_history,
            "interval",
            seconds=max(60, self.settings.effective_abs_history_poll_interval),
            id="history_poll",
            replace_existing=True,
            max_instances=1,
        )
        self.scheduler.add_job(
            self.prune_login_logs,
            "interval",
            hours=24,
            id="prune_login_logs",
            replace_existing=True,
            max_instances=1,
        )
        self.scheduler.add_job(
            self.refresh_update_status,
            "interval",
            seconds=CACHE_TTL_SECONDS,
            id="refresh_update_status",
            replace_existing=True,
            max_instances=1,
        )
        self.scheduler.start()
        asyncio.create_task(self.poll_activity())
        asyncio.create_task(self.poll_history())
        asyncio.create_task(self.refresh_update_status())
        log.info("ABSulli scheduler started")

    async def shutdown(self) -> None:
        self.scheduler.shutdown(wait=False)
        await self.client.aclose()
