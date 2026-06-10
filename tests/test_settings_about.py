from fastapi import FastAPI
from fastapi.testclient import TestClient

from absulli.core.config import get_settings
from absulli.database.models import AbsUser, Library, ListeningHistory, MediaItem, NotificationEvent
from absulli.database.session import SessionLocal
from absulli.web.routes import router as web_router
from absulli.web.settings import compact_bytes


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
    assert "Listening History Rows" in response.text
    assert "Notification Events" in response.text
    assert "Python Package" not in response.text
    assert "Migrations" not in response.text
