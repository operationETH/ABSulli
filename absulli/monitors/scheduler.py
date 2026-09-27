import asyncio
import logging
import time
from datetime import timedelta
from typing import Awaitable, Callable

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

JOB_SCHEDULES = {
    "activity_poll": {
        "field": "abs_poll_interval",
        "recommended": 15,
        "options": [5, 10, 15, 30, 60],
    },
    "history_poll": {
        "field": "abs_history_poll_interval",
        "recommended": 300,
        "options": [60, 120, 300, 600, 900, 1800],
    },
    "prune_login_logs": {
        "field": "login_log_cleanup_interval",
        "recommended": 86400,
        "options": [43200, 86400, 172800, 604800],
    },
    "refresh_update_status": {
        "field": "update_check_interval",
        "recommended": CACHE_TTL_SECONDS,
        "options": [21600, 43200, 86400],
    },
}


def interval_label(seconds: int) -> str:
    if seconds % 3600 == 0:
        hours = seconds // 3600
        return f"Every {hours} hour" if hours == 1 else f"Every {hours} hours"
    if seconds % 60 == 0:
        minutes = seconds // 60
        return f"Every {minutes} minute" if minutes == 1 else f"Every {minutes} minutes"
    return f"Every {seconds} second" if seconds == 1 else f"Every {seconds} seconds"


class AbsulliScheduler:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.scheduler = AsyncIOScheduler()
        self.client = AudiobookshelfClient(settings)
        self.notifier = NotificationManager(settings)
        self.activity = ActivityMonitor(self.client, self.notifier)
        self.history = HistoryMonitor(self.client, self.notifier)
        self.job_details = {
            "activity_poll": {
                "name": "Activity Poll",
                "description": "Updates active Audiobookshelf listening sessions.",
                "schedule": "Pending startup",
            },
            "history_poll": {
                "name": "History Sync",
                "description": "Syncs history, users, libraries, and newly added media.",
                "schedule": "Pending startup",
            },
            "prune_login_logs": {
                "name": "Login Log Cleanup",
                "description": "Removes login records older than 90 days.",
                "schedule": "Every 24 hours",
            },
            "refresh_update_status": {
                "name": "Update Check",
                "description": "Checks whether a newer ABSulli release is available.",
                "schedule": interval_label(CACHE_TTL_SECONDS),
            },
        }
        self.job_status = {
            job_id: {
                "running": False,
                "last_started_at": None,
                "last_finished_at": None,
                "last_status": "idle",
                "last_result": "Not run yet",
                "last_error": None,
                "duration_seconds": None,
            }
            for job_id in self.job_details
        }
        self.job_actions: dict[str, Callable[[], Awaitable[str]]] = {
            "activity_poll": self.poll_activity,
            "history_poll": self.poll_history,
            "prune_login_logs": self.prune_login_logs,
            "refresh_update_status": self.refresh_update_status,
        }

    async def _run_tracked(self, job_id: str, reserved: bool = False) -> bool:
        state = self.job_status[job_id]
        if state["running"] and not reserved:
            return False
        state.update(
            {
                "running": True,
                "last_started_at": utcnow_iso(),
                "last_status": "running",
                "last_error": None,
                "duration_seconds": None,
            }
        )
        started = time.monotonic()
        try:
            state["last_result"] = await self.job_actions[job_id]()
            state["last_status"] = "successful"
        except Exception as exc:
            state["last_status"] = "failed"
            state["last_result"] = "Failed"
            state["last_error"] = str(exc)
            log.warning("%s failed: %s", self.job_details[job_id]["name"], exc)
        finally:
            state["running"] = False
            state["last_finished_at"] = utcnow_iso()
            state["duration_seconds"] = round(time.monotonic() - started, 3)
        return True

    def jobs(self) -> list[dict[str, object]]:
        rows = []
        for job_id, details in self.job_details.items():
            job = self.scheduler.get_job(job_id)
            next_run = getattr(job, "next_run_time", None) if job is not None else None
            schedule = JOB_SCHEDULES[job_id]
            field_name = str(schedule["field"])
            interval_seconds = self._effective_interval(job_id)
            option_values = sorted({*schedule["options"], interval_seconds})
            source = "environment" if self.settings.field_configured(field_name) else "default"
            if source == "default" and get_setup_setting(field_name, "").strip():
                source = "saved"
            rows.append(
                {
                    "id": job_id,
                    **details,
                    **self.job_status[job_id],
                    "next_run_at": next_run.isoformat() if next_run else None,
                    "interval_seconds": interval_seconds,
                    "recommended_interval_seconds": schedule["recommended"],
                    "schedule_source": source,
                    "schedule_editable": source != "environment",
                    "schedule_options": [
                        {
                            "seconds": value,
                            "label": interval_label(value).removeprefix("Every "),
                            "recommended": value == schedule["recommended"],
                            "current": value == interval_seconds,
                        }
                        for value in option_values
                    ],
                }
            )
        return rows

    def _effective_interval(self, job_id: str) -> int:
        schedule = JOB_SCHEDULES[job_id]
        return int(getattr(self.settings, f"effective_{schedule['field']}"))

    def update_schedule(self, job_id: str, interval_seconds: int) -> dict[str, object]:
        if job_id not in JOB_SCHEDULES:
            raise KeyError(job_id)
        schedule = JOB_SCHEDULES[job_id]
        field_name = str(schedule["field"])
        if self.settings.field_configured(field_name):
            raise PermissionError(field_name)
        allowed_intervals = {*schedule["options"], self._effective_interval(job_id)}
        if interval_seconds not in allowed_intervals:
            raise ValueError("Select one of the available schedule options.")
        job = self.scheduler.get_job(job_id)
        if job is None:
            raise RuntimeError("Job is not scheduled.")
        set_setup_setting(field_name, str(interval_seconds))
        self.job_details[job_id]["schedule"] = interval_label(interval_seconds)
        self.scheduler.reschedule_job(job_id, trigger="interval", seconds=interval_seconds)
        return next(row for row in self.jobs() if row["id"] == job_id)

    def run_now(self, job_id: str) -> bool:
        if job_id not in self.job_actions:
            raise KeyError(job_id)
        if self.job_status[job_id]["running"]:
            return False
        self.job_status[job_id]["running"] = True
        self.job_status[job_id]["last_status"] = "running"
        asyncio.create_task(self._run_tracked(job_id, reserved=True))
        return True

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

    async def prune_login_logs(self) -> str:
        cutoff = utcnow() - timedelta(days=90)
        db = SessionLocal()
        try:
            deleted = db.query(LoginLog).filter(LoginLog.created_at < cutoff).delete()
            db.commit()
            log.debug("Login log pruning complete: %s deleted", deleted)
            return f"{deleted} login records removed"
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    async def refresh_update_status(self) -> str:
        await asyncio.to_thread(refresh_update_status, self.settings, __version__)
        return "Update status refreshed"

    async def poll_activity(self) -> str:
        if not self._ready_to_poll():
            log.debug("Skipping activity poll until first-run setup is complete")
            return "Skipped until setup is complete"

        db = SessionLocal()
        try:
            count = await self.activity.poll(db)
            await self._record_abs_reachable(db, True)
            log.debug("Activity poll complete: %s active", count)
            return f"{count} active session" if count == 1 else f"{count} active sessions"
        except Exception:
            await self._record_abs_reachable(db, False)
            raise
        finally:
            db.close()

    async def poll_history(self) -> str:
        if not self._ready_to_poll():
            log.debug("Skipping history poll until first-run setup is complete")
            return "Skipped until setup is complete"

        db = SessionLocal()
        try:
            imported = await self.history.poll(db)
            await self._record_abs_reachable(db, True)
            log.debug("History poll complete: %s imported", imported)
            if imported == 1:
                return "1 history entry imported"
            return f"{imported} history entries imported"
        except Exception:
            await self._record_abs_reachable(db, False)
            raise
        finally:
            db.close()

    async def _run_prune_login_logs(self) -> None:
        await self._run_tracked("prune_login_logs")

    async def _run_refresh_update_status(self) -> None:
        await self._run_tracked("refresh_update_status")

    async def _run_activity_poll(self) -> None:
        await self._run_tracked("activity_poll")

    async def _run_history_poll(self) -> None:
        await self._run_tracked("history_poll")

    def start(self) -> None:
        activity_interval = self._effective_interval("activity_poll")
        history_interval = self._effective_interval("history_poll")
        login_cleanup_interval = self._effective_interval("prune_login_logs")
        update_interval = self._effective_interval("refresh_update_status")
        self.job_details["activity_poll"]["schedule"] = interval_label(activity_interval)
        self.job_details["history_poll"]["schedule"] = interval_label(history_interval)
        self.job_details["prune_login_logs"]["schedule"] = interval_label(login_cleanup_interval)
        self.job_details["refresh_update_status"]["schedule"] = interval_label(update_interval)
        self.scheduler.add_job(
            self._run_activity_poll,
            "interval",
            seconds=activity_interval,
            id="activity_poll",
            replace_existing=True,
            max_instances=1,
        )
        self.scheduler.add_job(
            self._run_history_poll,
            "interval",
            seconds=history_interval,
            id="history_poll",
            replace_existing=True,
            max_instances=1,
        )
        self.scheduler.add_job(
            self._run_prune_login_logs,
            "interval",
            seconds=login_cleanup_interval,
            id="prune_login_logs",
            replace_existing=True,
            max_instances=1,
        )
        self.scheduler.add_job(
            self._run_refresh_update_status,
            "interval",
            seconds=update_interval,
            id="refresh_update_status",
            replace_existing=True,
            max_instances=1,
        )
        self.scheduler.start()
        asyncio.create_task(self._run_activity_poll())
        asyncio.create_task(self._run_history_poll())
        asyncio.create_task(self._run_refresh_update_status())
        log.info("ABSulli scheduler started")

    async def shutdown(self) -> None:
        self.scheduler.shutdown(wait=False)
        await self.client.aclose()
