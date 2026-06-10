from datetime import datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import absulli.web.routes as web_routes
import absulli.web.settings as web_settings
from absulli.database.models import AbsUser, Base, Library, ListeningHistory, MediaItem
from absulli.database.session import get_db
from absulli.web.routes import router as web_router


def make_client(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = session_factory()
    store: dict[str, str] = {}

    def override_get_db():
        try:
            yield db
        finally:
            pass

    monkeypatch.setattr(web_routes, "get_setup_setting", lambda key, default="": store.get(key, default))
    monkeypatch.setattr(web_routes, "set_setup_setting", lambda key, value: store.__setitem__(key, value))
    monkeypatch.setattr(web_settings.setup_state, "get_setup_setting", lambda key, default="": store.get(key, default))
    monkeypatch.setattr(web_settings.setup_state, "set_setup_setting", lambda key, value: store.__setitem__(key, value))

    app = FastAPI()
    app.dependency_overrides[get_db] = override_get_db
    app.include_router(web_router)
    return TestClient(app), db, store


def add_history_rows(db, count=60, *, username="admin", user_id="user-1", item_id="item-1"):
    db.add(AbsUser(abs_user_id=user_id, username=username, display_name=username.title()))
    db.add(Library(abs_library_id="lib-1", name="Audiobooks", media_type="book"))
    db.add(
        MediaItem(
            abs_item_id=item_id,
            library_id="lib-1",
            library_name="Audiobooks",
            media_type="book",
            title="Stored Book",
            author="Author",
        )
    )
    base = datetime(2026, 1, 1, 12, 0, 0)
    for index in range(count):
        db.add(
            ListeningHistory(
                abs_session_id=f"session-{index}",
                abs_user_id=user_id,
                username=username,
                abs_item_id=item_id,
                title=f"Book {index:03d}",
                media_type="book",
                library_id="lib-1",
                library_name="Audiobooks",
                imported_at=base + timedelta(minutes=index),
                started_at=base + timedelta(minutes=index),
                duration_seconds=60,
            )
        )
    db.commit()


def test_history_limit_query_is_saved_and_makes_users_clickable(monkeypatch):
    client, db, store = make_client(monkeypatch)
    add_history_rows(db, count=60)

    response = client.get("/history?limit=50")

    assert response.status_code == 200
    assert store["history_page_size"] == "50"
    assert 'value="50" selected' in response.text
    assert 'href="/users/user-1"' in response.text
    assert "Book 059" in response.text
    assert "Book 010" in response.text
    assert "Book 009" not in response.text


def test_saved_history_page_size_is_used_on_user_detail_without_query(monkeypatch):
    client, db, store = make_client(monkeypatch)
    store["history_page_size"] = "10"
    add_history_rows(db, count=12)

    response = client.get("/users/admin")

    assert response.status_code == 200
    assert 'value="10" selected' in response.text
    assert "Book 011" in response.text
    assert "Book 002" in response.text
    assert "Book 001" not in response.text


def test_invalid_history_limit_does_not_overwrite_saved_preference(monkeypatch):
    client, db, store = make_client(monkeypatch)
    store["history_page_size"] = "100"
    add_history_rows(db, count=12)

    response = client.get("/history?limit=999")

    assert response.status_code == 200
    assert store["history_page_size"] == "100"
    assert 'value="100" selected' in response.text
    assert "Book 000" in response.text


def test_history_page_size_sanitizer_allows_only_supported_values():
    assert web_settings.clean_history_page_size("10") == 10
    assert web_settings.clean_history_page_size("25") == 25
    assert web_settings.clean_history_page_size("50") == 50
    assert web_settings.clean_history_page_size("100") == 100
    assert web_settings.clean_history_page_size("999") == 25
    assert web_settings.clean_history_page_size("abc", default=50) == 50
