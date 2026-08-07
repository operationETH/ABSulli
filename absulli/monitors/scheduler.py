import asyncio
import logging
from datetime import timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from absulli.core.config import Settings
from absulli.core.setup_state import is_setup_complete, set_setup_setting
from absulli.core.time import utcnow, utcnow_iso
from absulli.database.models import LoginLog
from absulli.database.session import SessionLocal
from absulli.http.abs_client import AudiobookshelfClient
from absulli.monitors.activity import ActivityMonitor
from absulli.monitors.history import HistoryMonitor
from absulli.notifiers.manager import NotificationManager

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

    def _record_abs_reachable(self, reachable: bool) -> None:
        try:
            set_setup_setting("abs_reachable", "true" if reachable else "false")
            if reachable:
                set_setup_setting("abs_last_success_at", utcnow_iso())
            else:
                set_setup_setting("abs_last_failure_at", utcnow_iso())
        except Exception as exc:
            log.debug("Failed to update ABS reachability cache: %s", exc)

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

    async def poll_activity(self) -> None:
        if not self._ready_to_poll():
            log.debug("Skipping activity poll until first-run setup is complete")
            return

        db = SessionLocal()
        try:
            count = await self.activity.poll(db)
            self._record_abs_reachable(True)
            log.debug("Activity poll complete: %s active", count)
        except Exception as exc:
            self._record_abs_reachable(False)
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
            self._record_abs_reachable(True)
            log.debug("History poll complete: %s imported", imported)
        except Exception as exc:
            self._record_abs_reachable(False)
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
        self.scheduler.start()
        asyncio.create_task(self.poll_activity())
        asyncio.create_task(self.poll_history())
        log.info("ABSulli scheduler started")

    async def shutdown(self) -> None:
        self.scheduler.shutdown(wait=False)
        await self.client.aclose()
