import asyncio
from datetime import UTC, datetime
import json
from xml.etree import ElementTree

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import absulli.core.setup_state as setup_state
import absulli.web.routes as web_routes
from absulli.core.config import get_settings
from absulli.core.security import SecurityHeadersMiddleware
from absulli.database.models import Base, NotificationDelivery, NotificationEvent
from absulli.database.session import get_db
from absulli.notifiers.manager import NotificationManager
from absulli.web.routes import router as web_router


def make_client(monkeypatch, store=None, authenticated=False):
    store = store if store is not None else {}
    monkeypatch.setenv("ABSULLI_SECRET_KEY", "test-secret-key-that-is-long-enough-32")
    monkeypatch.setenv("ABSULLI_AUTH_ENABLED", "false" if authenticated else "true")
    get_settings.cache_clear()

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = session_factory()

    def override_get_db():
        try:
            yield db
        finally:
            pass

    def fake_get(key, default=""):
        return store.get(key, default)

    def fake_set(key, value):
        store[key] = value

    def fake_set_many(values):
        store.update(values)

    monkeypatch.setattr(web_routes, "get_setup_setting", fake_get)
    monkeypatch.setattr(web_routes, "set_setup_setting", fake_set)
    monkeypatch.setattr(web_routes, "set_setup_settings", fake_set_many)
    monkeypatch.setattr(setup_state, "get_setup_setting", fake_get)
    monkeypatch.setattr(setup_state, "set_setup_setting", fake_set)
    monkeypatch.setattr(setup_state, "set_setup_settings", fake_set_many)
    monkeypatch.setattr(web_routes, "validate_csrf_token", lambda request, token: True)

    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)
    app.dependency_overrides[get_db] = override_get_db
    app.include_router(web_router)
    return TestClient(app), db, store


def add_rss_event(db, title="A & B", subtitle="A Novel"):
    event = NotificationEvent(
        event_type="new_book",
        title="New book added",
        body=f"{title} was added.",
        library_id="books",
        context_json=json.dumps(
            {
                "item_id": "item-1",
                "title": title,
                "subtitle": subtitle,
                "author": "Example Author",
                "narrator": "Example Narrator",
                "series": "Example Series",
                "library_name": "Audiobooks",
                "year": "2026",
                "description": "An example description.",
            }
        ),
        delivered=True,
        created_at=datetime(2026, 9, 18, 3, 0, tzinfo=UTC),
    )
    db.add(event)
    db.commit()
    db.add(NotificationDelivery(event_id=event.id, agent="rss", delivered=True, error=""))
    db.commit()
    return event


def add_podcast_rss_events(db):
    podcast = NotificationEvent(
        event_type="new_podcast",
        title="New podcast added",
        body="Example Podcast by Example Host was added.",
        library_id="podcasts",
        context_json=json.dumps(
            {
                "item_id": "podcast-1",
                "title": "Example Podcast",
                "author": "Example Host",
                "description": "An example podcast.",
                "library_name": "Podcasts",
                "media_type": "podcast",
            }
        ),
        delivered=True,
        created_at=datetime(2026, 9, 19, 3, 0, tzinfo=UTC),
    )
    episode = NotificationEvent(
        event_type="new_podcast_episode",
        title="New podcast episode added",
        body="Example Podcast - Episode One was added.",
        library_id="podcasts",
        context_json=json.dumps(
            {
                "item_id": "podcast-1",
                "podcast_title": "Example Podcast",
                "episode_title": "Episode One",
                "episode_description": "The first episode.",
                "library_name": "Podcasts",
                "media_type": "podcast",
            }
        ),
        delivered=True,
        created_at=datetime(2026, 9, 19, 4, 0, tzinfo=UTC),
    )
    db.add_all([podcast, episode])
    db.commit()
    db.add_all(
        [
            NotificationDelivery(event_id=podcast.id, agent="rss", delivered=True, error=""),
            NotificationDelivery(event_id=episode.id, agent="rss", delivered=True, error=""),
        ]
    )
    db.commit()


def test_rss_feed_is_public_with_valid_private_url(monkeypatch):
    client, db, _store = make_client(
        monkeypatch,
        {"rss_feed_enabled": "true", "rss_feed_token": "feed-token"},
    )
    add_rss_event(db)

    response = client.get("/feeds/new-media/feed-token.xml")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/rss+xml")
    assert response.headers["cache-control"] == "private, no-store"
    root = ElementTree.fromstring(response.content)
    assert root.tag == "rss"
    assert root.findtext("channel/title") == "ABSulli New Media"
    assert root.findtext("channel/image/url") == "http://testserver/static/img/logo-mark.png?v=1"
    assert root.findtext("channel/image/title") == "ABSulli New Media"
    assert root.findtext("channel/image/link") == "http://testserver"
    assert root.findtext("channel/item/title") == "Example Author - A & B (2026)"
    assert root.findtext("channel/item/guid") == "urn:absulli:new-book:1"
    item_link = root.findtext("channel/item/link") or ""
    assert item_link.endswith("/item/item-1")
    description = root.findtext("channel/item/description") or ""
    assert "Example Author" in description
    assert "An example description." in description
    assert "/notification-covers/items/item-1" in description
    assert f'<a href="{item_link}">Open in Audiobookshelf</a>' in description

    monkeypatch.setenv("ABSULLI_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    log_page = client.get("/notifications")
    assert "RSS" in log_page.text


def test_rss_feed_includes_podcasts_and_episodes(monkeypatch):
    client, db, _store = make_client(
        monkeypatch,
        {"rss_feed_enabled": "true", "rss_feed_token": "feed-token"},
    )
    add_podcast_rss_events(db)

    response = client.get("/feeds/new-media/feed-token.xml")

    assert response.status_code == 200
    root = ElementTree.fromstring(response.content)
    items = root.findall("channel/item")
    assert [item.findtext("title") for item in items] == [
        "Example Podcast - Episode One",
        "Example Host - Example Podcast",
    ]
    assert [item.findtext("guid") for item in items] == [
        "urn:absulli:new-podcast-episode:2",
        "urn:absulli:new-podcast:1",
    ]
    episode_description = items[0].findtext("description") or ""
    assert "The first episode." in episode_description
    assert "<strong>Podcast:</strong> Example Podcast" in episode_description
    assert "Open in Audiobookshelf" in episode_description
    podcast_description = items[1].findtext("description") or ""
    assert "An example podcast." in podcast_description
    assert "<strong>Author:</strong> Example Host" in podcast_description


def test_rss_feed_rejects_disabled_or_invalid_urls(monkeypatch):
    client, _db, store = make_client(
        monkeypatch,
        {"rss_feed_enabled": "true", "rss_feed_token": "feed-token"},
    )

    assert client.get("/feeds/new-media/wrong-token.xml").status_code == 404
    store["rss_feed_enabled"] = "false"
    assert client.get("/feeds/new-media/feed-token.xml").status_code == 404


def test_rss_feed_rejects_non_ascii_token_without_error(monkeypatch):
    client, _db, store = make_client(
        monkeypatch,
        {"rss_feed_enabled": "true", "rss_feed_token": "feed-token"},
    )

    response = client.get("/feeds/new-media/\u00e9\u00e9\u00e9.xml")
    assert response.status_code == 404


def test_rss_settings_generate_and_regenerate_global_url(monkeypatch):
    client, _db, store = make_client(monkeypatch, authenticated=True)

    enabled = client.post(
        "/settings/rss",
        data={"csrf_token": "valid-token", "rss_feed_enabled": "on"},
        follow_redirects=False,
    )

    assert enabled.status_code == 303
    assert enabled.headers["location"] == "/settings?tab=notifications&saved=rss"
    assert store["rss_feed_enabled"] == "true"
    original_token = store["rss_feed_token"]
    assert len(original_token) >= 32

    page = client.get("/settings?tab=notifications")
    assert page.status_code == 200
    assert "<h2>RSS Feed</h2>" in page.text
    assert page.text.index("Message Templates") < page.text.index("<h2>RSS Feed</h2>")
    assert f"/feeds/new-media/{original_token}.xml" in page.text

    general_page = client.get("/settings?tab=general")
    assert "rss-feed-settings-panel" not in general_page.text

    regenerated = client.post(
        "/settings/rss/regenerate",
        data={"csrf_token": "valid-token"},
        follow_redirects=False,
    )
    assert regenerated.status_code == 303
    assert regenerated.headers["location"] == "/settings?tab=notifications&saved=rss"
    assert store["rss_feed_token"] != original_token
    assert client.get(f"/feeds/new-media/{original_token}.xml").status_code == 404
    assert client.get(f"/feeds/new-media/{store['rss_feed_token']}.xml").status_code == 200


def test_notification_manager_records_rss_entry_without_push_agents(monkeypatch):
    store = {"rss_feed_enabled": "true"}

    def fake_get(key, default=""):
        return store.get(key, default)

    monkeypatch.setattr(setup_state, "get_setup_setting", fake_get)
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = session_factory()
    manager = NotificationManager(get_settings())
    monkeypatch.setattr(manager, "named_agents", lambda: [])

    asyncio.run(
        manager.notify(
            db,
            "new_book",
            "New book added",
            "Example by Author was added.",
            library_id="books",
            context={"item_id": "item-1", "title": "Example", "author": "Author"},
        )
    )

    event = db.query(NotificationEvent).one()
    delivery = db.query(NotificationDelivery).one()
    assert event.library_id == "books"
    assert json.loads(event.context_json)["title"] == "Example"
    assert event.delivered is True
    assert delivery.agent == "rss"
    assert delivery.delivered is True


def test_notification_manager_records_podcast_rss_entries_without_push_agents(monkeypatch):
    store = {"rss_feed_enabled": "true"}

    monkeypatch.setattr(
        setup_state,
        "get_setup_setting",
        lambda key, default="": store.get(key, default),
    )
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = session_factory()
    manager = NotificationManager(get_settings())
    monkeypatch.setattr(manager, "named_agents", lambda: [])

    for event_type in ("new_podcast", "new_podcast_episode"):
        asyncio.run(
            manager.notify(
                db,
                event_type,
                "New podcast media added",
                "Podcast media was added.",
                library_id="podcasts",
                context={"item_id": "podcast-1", "podcast_title": "Example Podcast"},
            )
        )

    events = db.query(NotificationEvent).order_by(NotificationEvent.id).all()
    deliveries = db.query(NotificationDelivery).order_by(NotificationDelivery.id).all()
    assert [event.event_type for event in events] == ["new_podcast", "new_podcast_episode"]
    assert all(event.delivered for event in events)
    assert [delivery.agent for delivery in deliveries] == ["rss", "rss"]
    assert all(delivery.delivered for delivery in deliveries)


def test_notification_manager_does_not_record_disabled_rss_without_agents(monkeypatch):
    monkeypatch.setattr(setup_state, "get_setup_setting", lambda key, default="": "false")
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = session_factory()
    manager = NotificationManager(get_settings())
    monkeypatch.setattr(manager, "named_agents", lambda: [])

    asyncio.run(manager.notify(db, "new_book", "New book added", "Example"))

    assert db.query(NotificationEvent).count() == 0
