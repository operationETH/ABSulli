import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

import absulli.core.setup_state as setup_state
from absulli.core.config import get_settings
import absulli.monitors.scheduler as scheduler_module
from absulli.monitors.scheduler import AbsulliScheduler, JOB_SCHEDULES, interval_label
from absulli.web.routes import router as web_router


class StubScheduler:
    def __init__(self):
        self.started = []
        self.updated = []

    def jobs(self):
        return [
            {
                "id": "history_poll",
                "name": "History Sync",
                "description": "Syncs history, users, libraries, and newly added media.",
                "schedule": "Every 300 seconds",
                "running": False,
                "last_started_at": None,
                "last_finished_at": None,
                "last_status": "idle",
                "last_result": "Not run yet",
                "last_error": None,
                "duration_seconds": None,
                "next_run_at": None,
                "interval_seconds": 300,
                "recommended_interval_seconds": 300,
                "schedule_source": "default",
                "schedule_editable": True,
                "schedule_options": [
                    {"seconds": 300, "label": "5 minutes", "recommended": True, "current": True}
                ],
            }
        ]

    def run_now(self, job_id):
        if job_id != "history_poll":
            raise KeyError(job_id)
        self.started.append(job_id)
        return True

    def update_schedule(self, job_id, interval_seconds):
        if job_id != "history_poll":
            raise KeyError(job_id)
        if interval_seconds != 300:
            raise ValueError("Select one of the available schedule options.")
        self.updated.append((job_id, interval_seconds))
        return self.jobs()[0]


def make_client(monkeypatch):
    monkeypatch.setenv("ABSULLI_SECRET_KEY", "test-secret-key-that-is-long-enough-32")
    get_settings.cache_clear()
    app = FastAPI()
    app.state.scheduler = StubScheduler()
    app.include_router(web_router)
    return TestClient(app), app.state.scheduler


def test_jobs_tab_renders(monkeypatch):
    client, _scheduler = make_client(monkeypatch)

    response = client.get("/settings?tab=jobs")

    assert response.status_code == 200
    assert 'href="/settings?tab=jobs"' in response.text
    assert 'data-jobs-panel' in response.text
    assert "View and manage ABSulli background tasks." in response.text
    assert "<th>Duration</th>" not in response.text
    assert "<th>Next Run</th>" in response.text


def test_jobs_api_lists_scheduler_jobs(monkeypatch):
    client, _scheduler = make_client(monkeypatch)

    response = client.get("/api/v1/jobs")

    assert response.status_code == 200
    assert response.json()["jobs"][0]["id"] == "history_poll"


def test_jobs_run_requires_csrf_and_starts_job(monkeypatch):
    client, scheduler = make_client(monkeypatch)
    page = client.get("/settings?tab=jobs")
    csrf_token = client.cookies.get("absulli_csrf")

    rejected = client.post("/api/v1/jobs/history_poll/run")
    started = client.post(
        "/api/v1/jobs/history_poll/run",
        headers={"X-CSRF-Token": csrf_token},
    )

    assert page.status_code == 200
    assert rejected.status_code == 403
    assert started.status_code == 200
    assert scheduler.started == ["history_poll"]


def test_jobs_run_rejects_unknown_job(monkeypatch):
    client, _scheduler = make_client(monkeypatch)
    client.get("/settings?tab=jobs")
    csrf_token = client.cookies.get("absulli_csrf")

    response = client.post(
        "/api/v1/jobs/unknown/run",
        headers={"X-CSRF-Token": csrf_token},
    )

    assert response.status_code == 404


def test_jobs_schedule_requires_csrf_and_updates_job(monkeypatch):
    client, scheduler = make_client(monkeypatch)
    client.get("/settings?tab=jobs")
    csrf_token = client.cookies.get("absulli_csrf")

    rejected = client.post("/api/v1/jobs/history_poll/schedule", json={"interval_seconds": 300})
    updated = client.post(
        "/api/v1/jobs/history_poll/schedule",
        json={"interval_seconds": 300},
        headers={"X-CSRF-Token": csrf_token},
    )

    assert rejected.status_code == 403
    assert updated.status_code == 200
    assert scheduler.updated == [("history_poll", 300)]


def test_jobs_schedule_rejects_unknown_option(monkeypatch):
    client, scheduler = make_client(monkeypatch)
    client.get("/settings?tab=jobs")
    csrf_token = client.cookies.get("absulli_csrf")

    response = client.post(
        "/api/v1/jobs/history_poll/schedule",
        json={"interval_seconds": 5},
        headers={"X-CSRF-Token": csrf_token},
    )

    assert response.status_code == 422
    assert scheduler.updated == []


def test_scheduler_tracks_successful_job(monkeypatch):
    scheduler = AbsulliScheduler(get_settings())

    async def successful_action():
        return "Done"

    scheduler.job_actions["history_poll"] = successful_action
    asyncio.run(scheduler._run_tracked("history_poll"))
    state = scheduler.job_status["history_poll"]

    assert state["running"] is False
    assert state["last_status"] == "successful"
    assert state["last_result"] == "Done"
    assert state["last_finished_at"]
    assert state["duration_seconds"] is not None
    asyncio.run(scheduler.client.aclose())


def test_scheduler_tracks_failed_job(monkeypatch):
    scheduler = AbsulliScheduler(get_settings())

    async def failed_action():
        raise RuntimeError("Job failed")

    scheduler.job_actions["history_poll"] = failed_action
    asyncio.run(scheduler._run_tracked("history_poll"))
    state = scheduler.job_status["history_poll"]

    assert state["running"] is False
    assert state["last_status"] == "failed"
    assert state["last_error"] == "Job failed"
    asyncio.run(scheduler.client.aclose())


def test_job_interval_labels_are_readable():
    assert interval_label(15) == "Every 15 seconds"
    assert interval_label(300) == "Every 5 minutes"
    assert interval_label(3600) == "Every 1 hour"


def test_job_schedule_defaults_and_options_are_job_specific():
    assert JOB_SCHEDULES["activity_poll"]["recommended"] == 15
    assert JOB_SCHEDULES["activity_poll"]["options"] == [5, 10, 15, 30, 60]
    assert JOB_SCHEDULES["history_poll"]["recommended"] == 300
    assert JOB_SCHEDULES["history_poll"]["options"] == [60, 120, 300, 600, 900, 1800]
    assert JOB_SCHEDULES["prune_login_logs"]["recommended"] == 86400
    assert JOB_SCHEDULES["refresh_update_status"]["recommended"] == 21600


def test_scheduler_updates_saved_schedule_and_reschedules(monkeypatch):
    saved = {}
    monkeypatch.setattr(scheduler_module, "set_setup_setting", lambda key, value: saved.update({key: value}))
    monkeypatch.setattr(scheduler_module, "get_setup_setting", lambda key, default="": saved.get(key, default))
    monkeypatch.setattr(setup_state, "get_setup_setting", lambda key, default="": saved.get(key, default))
    scheduler = AbsulliScheduler(get_settings())
    scheduler.scheduler.add_job(
        scheduler._run_history_poll,
        "interval",
        seconds=300,
        id="history_poll",
    )

    row = scheduler.update_schedule("history_poll", 600)

    assert saved["abs_history_poll_interval"] == "600"
    assert row["interval_seconds"] == 600
    assert row["schedule"] == "Every 10 minutes"
    asyncio.run(scheduler.client.aclose())


def test_scheduler_rejects_environment_managed_schedule(monkeypatch):
    monkeypatch.setenv("ABS_HISTORY_POLL_INTERVAL", "900")
    get_settings.cache_clear()
    scheduler = AbsulliScheduler(get_settings())
    scheduler.scheduler.add_job(
        scheduler._run_history_poll,
        "interval",
        seconds=900,
        id="history_poll",
    )

    with pytest.raises(PermissionError):
        scheduler.update_schedule("history_poll", 600)

    asyncio.run(scheduler.client.aclose())
    get_settings.cache_clear()
