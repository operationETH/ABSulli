from fastapi import FastAPI
from fastapi.testclient import TestClient

from absulli.core.config import get_settings
from absulli.database.models import AbsUser, Library, ListeningHistory, MediaItem, NotificationEvent
from absulli.database.session import SessionLocal
from absulli.web.routes import router as web_router
from absulli.web.settings import about_settings_context, compact_bytes


def make_client(monkeypatch):
    monkeypatch.setenv("ABSULLI_SECRET_KEY", "test-secret-key-that-is-long-enough-32")
    get_settings.cache_clear()
    app = FastAPI()
    app.include_router(web_router)
    return TestClient(app)


def test_compact_bytes_formats_common_sizes():
    assert compact_bytes(0) == "0 B"
    assert compact_bytes(999) == "999 B"
    assert compact_bytes(1024) == "1.0 KB"
    assert compact_bytes(1024 * 1024) == "1.0 MB"


def test_about_settings_tab_renders_storage_and_data_summary(monkeypatch):
    client = make_client(monkeypatch)

    with SessionLocal() as db:
        suffix = "about-test"
        db.add(AbsUser(abs_user_id=f"user-{suffix}", username=f"user-{suffix}", display_name="About User"))
        db.add(Library(abs_library_id=f"lib-{suffix}", name="About Library", media_type="book"))
        db.add(MediaItem(abs_item_id=f"item-{suffix}", title="About Book", media_type="book"))
        db.add(ListeningHistory(abs_session_id=f"session-{suffix}", abs_user_id=f"user-{suffix}", username=f"user-{suffix}"))
        db.add(NotificationEvent(event_type="test", title="About", body="Test"))
        db.commit()

    response = client.get("/settings?tab=about")

    assert response.status_code == 200
    assert "Application version, storage, and local database details." in response.text
    assert "Data Summary" in response.text
    assert "Database Size" in response.text
    assert "Migration Version" in response.text
    assert "Python Version" in response.text
    assert "Time Zone" in response.text
    assert "Listening History Rows" in response.text
    assert "Notification Events" in response.text
    assert "Python Package" not in response.text
    assert "Migrations" not in response.text


def test_about_version_context_includes_status_badge(monkeypatch):
    settings = get_settings()
    with SessionLocal() as db:
        rows = about_settings_context(
            settings,
            db,
            {
                "channel": "stable",
                "current_version": "v0.2.11",
                "badge_label": "Out of Date",
                "badge_class": "outdated",
                "release_url": "https://github.com/operationETH/ABSulli/releases",
                "update_available": True,
            },
        )

    assert rows[0]["label"] == "ABSulli Version"
    assert rows[0]["value"] == "v0.2.11"
    assert rows[0]["status_label"] == "Out of Date"
    assert rows[0]["status_class"] == "outdated"
    assert rows[0]["status_url"] == "https://github.com/operationETH/ABSulli/releases"


def test_about_current_version_badges_remain_clickable():
    settings = get_settings()
    with SessionLocal() as db:
        stable_rows = about_settings_context(
            settings,
            db,
            {
                "channel": "stable",
                "current_version": "v0.2.10",
                "badge_label": "Up to Date",
                "badge_class": "current",
                "release_url": "https://github.com/operationETH/ABSulli/releases",
                "update_available": False,
            },
        )
        nightly_rows = about_settings_context(
            settings,
            db,
            {
                "channel": "nightly",
                "current_version": "sha-3442c21",
                "badge_label": "Nightly",
                "badge_class": "nightly",
                "release_url": "https://github.com/operationETH/ABSulli/commit/3442c21",
                "update_available": False,
            },
        )

    assert stable_rows[0]["status_url"] == "https://github.com/operationETH/ABSulli/releases"
    assert nightly_rows[0]["status_url"] == "https://github.com/operationETH/ABSulli/commit/3442c21"


def test_about_time_zone_uses_configured_tz(monkeypatch):
    monkeypatch.setenv("TZ", "America/Phoenix")
    settings = get_settings()

    with SessionLocal() as db:
        rows = about_settings_context(settings, db)

    time_zone = next(row for row in rows if row["label"] == "Time Zone")
    assert time_zone["value"] == "America/Phoenix"


def test_about_time_zone_shows_not_configured(monkeypatch):
    monkeypatch.delenv("TZ", raising=False)
    settings = get_settings()

    with SessionLocal() as db:
        rows = about_settings_context(settings, db)

    time_zone = next(row for row in rows if row["label"] == "Time Zone")
    assert time_zone["value"] == "Not configured"
