from datetime import datetime
import hashlib
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import absulli.web.routes as web_routes
from absulli.core.time import utcnow
from absulli.database.models import ActivitySession, Base, Library, ListeningHistory, MediaItem, Setting
from absulli.database.session import get_db
from absulli.monitors.history import DELETED_HISTORY_SESSION_HASHES_KEY, history_session_hash
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

    def override_get_db():
        yield db

    app = FastAPI()
    app.dependency_overrides[get_db] = override_get_db
    app.include_router(web_router)
    return TestClient(app), db


def add_archived_library(db):
    library = Library(
        abs_library_id="lib-youtube",
        name="YouTube Podcasts",
        media_type="book",
        item_count=53,
        is_active=False,
        archived_at=utcnow(),
    )
    item = MediaItem(
        abs_item_id="item-youtube",
        library_id="lib-youtube",
        library_name="YouTube Podcasts",
        media_type="book",
        title="Archived Item",
    )
    history = ListeningHistory(
        abs_session_id="history-youtube",
        abs_user_id="user-1",
        username="User",
        abs_item_id="item-youtube",
        title="Archived Item",
        media_type="book",
        library_id="lib-youtube",
        library_name="YouTube Podcasts",
    )
    activity = ActivitySession(
        session_key="activity-youtube",
        abs_user_id="user-1",
        abs_item_id="item-youtube",
        library_id="lib-youtube",
        library_name="YouTube Podcasts",
    )
    digest = hashlib.sha256(b"lib-youtube").hexdigest()[:24]
    baseline = Setting(
        key=f"notify_new_book_baseline_{digest}",
        value="true",
        updated_at=utcnow(),
    )
    db.add_all([library, item, history, activity, baseline])
    db.commit()
    return library, item, history, activity, baseline


def test_libraries_page_separates_active_and_archived_libraries(monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    client, db = make_client(monkeypatch)
    db.add(
        Library(
            abs_library_id="lib-active",
            name="Audiobooks",
            media_type="book",
            item_count=902,
            is_active=True,
        )
    )
    db.add(
        ListeningHistory(
            abs_session_id="history-active",
            abs_user_id="user-1",
            username="User",
            abs_item_id="item-active",
            title="Active Item",
            media_type="book",
            library_id="lib-active",
            library_name="Audiobooks",
            started_at=datetime(2026, 8, 31, 5, 8, 26, 894000),
        )
    )
    add_archived_library(db)

    response = client.get("/libraries")

    assert response.status_code == 200
    assert "Library Overview" in response.text
    assert "Archived Libraries" in response.text
    assert "Audiobooks" in response.text
    assert "YouTube Podcasts" in response.text
    assert "Remove cached data" in response.text
    assert "Delete everything" in response.text
    assert "data-library-remove-cache" in response.text
    assert 'data-library-manage-open="library-manage-1"' in response.text
    assert 'data-library-manage-dialog' in response.text
    assert "<details" not in response.text
    assert "2026-08-31 5:08 AM" in response.text
    assert "05:08:26.894000" not in response.text
    assert "absulli_csrf" in response.cookies
    db.close()


def test_remove_archived_library_cache_keeps_history(monkeypatch):
    client, db = make_client(monkeypatch)
    library, item, history, activity, baseline = add_archived_library(db)
    library_id = library.id
    item_id = item.id
    history_id = history.id
    activity_id = activity.id
    baseline_key = baseline.key
    monkeypatch.setattr(web_routes, "validate_csrf_token", lambda request, token: True)

    response = client.post(
        "/libraries/lib-youtube/remove",
        data={"csrf_token": "valid-token", "mode": "cache"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/libraries?removed=cache"
    retained_library = db.query(Library).filter_by(id=library_id).one()
    assert retained_library.is_active is False
    assert retained_library.item_count == 0
    assert db.query(MediaItem).filter_by(id=item_id).first() is None
    assert db.query(ActivitySession).filter_by(id=activity_id).first() is None
    assert db.query(Setting).filter_by(key=baseline_key).first() is None
    assert db.query(ListeningHistory).filter_by(id=history_id).one().title == "Archived Item"

    page = client.get(response.headers["location"])

    assert page.status_code == 200
    assert "YouTube Podcasts" in page.text
    assert "Cached library data removed. Listening history was kept." in page.text
    assert "Delete everything" in page.text
    db.close()


def test_delete_everything_after_cached_data_was_removed(monkeypatch):
    client, db = make_client(monkeypatch)
    library, _, history, _, _ = add_archived_library(db)
    library_id = library.id
    history_id = history.id
    monkeypatch.setattr(web_routes, "validate_csrf_token", lambda request, token: True)

    cache_response = client.post(
        "/libraries/lib-youtube/remove",
        data={"csrf_token": "valid-token", "mode": "cache"},
        follow_redirects=False,
    )
    all_response = client.post(
        "/libraries/lib-youtube/remove",
        data={
            "csrf_token": "valid-token",
            "mode": "all",
            "confirm_name": "YouTube Podcasts",
        },
        follow_redirects=False,
    )

    assert cache_response.status_code == 303
    assert all_response.status_code == 303
    assert all_response.headers["location"] == "/libraries?removed=all"
    assert db.query(Library).filter_by(id=library_id).first() is None
    assert db.query(ListeningHistory).filter_by(id=history_id).first() is None
    db.close()


def test_remove_archived_library_all_requires_name_and_deletes_history(monkeypatch):
    client, db = make_client(monkeypatch)
    library, item, history, activity, baseline = add_archived_library(db)
    monkeypatch.setattr(web_routes, "validate_csrf_token", lambda request, token: True)

    mismatch = client.post(
        "/libraries/lib-youtube/remove",
        data={"csrf_token": "valid-token", "mode": "all", "confirm_name": "Wrong"},
    )

    assert mismatch.status_code == 200
    assert "Library name confirmation did not match." in mismatch.text
    assert db.query(Library).filter_by(id=library.id).one().name == "YouTube Podcasts"

    response = client.post(
        "/libraries/lib-youtube/remove",
        data={
            "csrf_token": "valid-token",
            "mode": "all",
            "confirm_name": "YouTube Podcasts",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/libraries?removed=all"
    assert db.query(Library).count() == 0
    assert db.query(MediaItem).count() == 0
    assert db.query(ActivitySession).count() == 0
    assert db.query(ListeningHistory).count() == 0
    suppression = db.query(Setting).filter_by(key=DELETED_HISTORY_SESSION_HASHES_KEY).one()
    hashes = json.loads(suppression.value)
    assert hashes == [history_session_hash("history-youtube")]
    assert "history-youtube" not in suppression.value
    db.close()


def test_active_library_cannot_be_removed(monkeypatch):
    client, db = make_client(monkeypatch)
    db.add(
        Library(
            abs_library_id="lib-active",
            name="Audiobooks",
            media_type="book",
            is_active=True,
        )
    )
    db.commit()
    monkeypatch.setattr(web_routes, "validate_csrf_token", lambda request, token: True)

    response = client.post(
        "/libraries/lib-active/remove",
        data={"csrf_token": "valid-token", "mode": "cache"},
    )

    assert response.status_code == 409
    assert db.query(Library).filter_by(abs_library_id="lib-active").one().is_active is True
    db.close()


def test_remove_archived_library_rejects_invalid_csrf(monkeypatch):
    client, db = make_client(monkeypatch)
    add_archived_library(db)
    monkeypatch.setattr(web_routes, "validate_csrf_token", lambda request, token: False)

    response = client.post(
        "/libraries/lib-youtube/remove",
        data={"csrf_token": "invalid-token", "mode": "cache"},
    )

    assert response.status_code == 403
    assert db.query(Library).filter_by(abs_library_id="lib-youtube").one().is_active is False
    db.close()
