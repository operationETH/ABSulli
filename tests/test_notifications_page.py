from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import absulli.web.routes as web_routes
from absulli.database.models import Base, NotificationDelivery, NotificationEvent
from absulli.database.session import get_db
from absulli.web.routes import router as web_router


def make_client():
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

    app = FastAPI()
    app.dependency_overrides[get_db] = override_get_db
    app.include_router(web_router)
    return TestClient(app), db


def test_notification_log_shows_agent_delivery_status_and_error():
    client, db = make_client()
    event = NotificationEvent(event_type="new_book", title="New book added", body="Example", delivered=True)
    db.add(event)
    db.commit()
    db.add(NotificationDelivery(event_id=event.id, agent="gotify", delivered=True, error=""))
    db.add(
        NotificationDelivery(
            event_id=event.id,
            agent="discord",
            delivered=False,
            error="Client error '401 Unauthorized'",
        )
    )
    db.commit()

    response = client.get("/notifications")

    assert response.status_code == 200
    assert "Gotify" in response.text
    assert "Discord" in response.text
    assert "Error details" in response.text
    assert "Discord" in response.text
    assert "401 Unauthorized" in response.text


def test_notification_log_keeps_legacy_status_without_delivery_rows():
    client, db = make_client()
    db.add(NotificationEvent(event_type="test", title="Legacy", body="Example", delivered=True))
    db.commit()

    response = client.get("/notifications")

    assert response.status_code == 200
    assert "Legacy" in response.text
    assert "Delivered" in response.text


def test_notification_log_redacts_urls_from_delivery_errors():
    client, db = make_client()
    event = NotificationEvent(event_type="new_book", title="New book added", body="Example", delivered=False)
    db.add(event)
    db.commit()
    db.add(
        NotificationDelivery(
            event_id=event.id,
            agent="discord",
            delivered=False,
            error="Client error '401 Unauthorized' for url 'https://discord.com/api/webhooks/123/secret-token'\nFor more information check: https://developer.mozilla.org/",
        )
    )
    db.commit()

    response = client.get("/notifications")

    assert response.status_code == 200
    assert "401 Unauthorized" in response.text
    assert "secret-token" not in response.text
    assert "developer.mozilla.org" not in response.text


def test_notification_log_paginates_like_history():
    client, db = make_client()
    for index in range(26):
        db.add(
            NotificationEvent(
                event_type="new_book",
                title=f"Event {index + 1}",
                body="Example",
                delivered=True,
            )
        )
    db.commit()

    response = client.get("/notifications?limit=10&page=2")

    assert response.status_code == 200
    assert "Showing 11 to 20 of 26 notification entries" in response.text
    assert 'aria-current="page">2</span>' in response.text
    assert "Prev" in response.text
    assert "Next" in response.text
    assert 'name="limit"' in response.text
    for option in (10, 25, 50, 100):
        assert f'value="{option}"' in response.text


def test_notification_log_filters_by_type_and_agent():
    client, db = make_client()
    book = NotificationEvent(event_type="new_book", title="Book event", body="Example", delivered=True)
    playback = NotificationEvent(event_type="playback_start", title="Playback event", body="Example", delivered=True)
    db.add_all([book, playback])
    db.commit()
    db.add_all(
        [
            NotificationDelivery(event_id=book.id, agent="discord", delivered=True, error=""),
            NotificationDelivery(event_id=playback.id, agent="gotify", delivered=True, error=""),
        ]
    )
    db.commit()

    by_type = client.get("/notifications?type=new_book&limit=10")
    by_agent = client.get("/notifications?agent=discord&limit=10")

    assert by_type.status_code == 200
    assert "Book event" in by_type.text
    assert "Playback event" not in by_type.text
    assert 'option value="new_book" selected' in by_type.text
    assert by_agent.status_code == 200
    assert "Book event" in by_agent.text
    assert "Playback event" not in by_agent.text
    assert 'option value="discord" selected' in by_agent.text


def test_notification_log_status_filter_uses_per_agent_results():
    client, db = make_client()
    success = NotificationEvent(event_type="new_book", title="All delivered", body="Example", delivered=True)
    mixed = NotificationEvent(event_type="new_book", title="Partial failure", body="Example", delivered=True)
    legacy_failure = NotificationEvent(event_type="test", title="Legacy failure", body="Example", delivered=False)
    db.add_all([success, mixed, legacy_failure])
    db.commit()
    db.add_all(
        [
            NotificationDelivery(event_id=success.id, agent="gotify", delivered=True, error=""),
            NotificationDelivery(event_id=success.id, agent="discord", delivered=True, error=""),
            NotificationDelivery(event_id=mixed.id, agent="gotify", delivered=True, error=""),
            NotificationDelivery(event_id=mixed.id, agent="discord", delivered=False, error="Failed"),
        ]
    )
    db.commit()

    failed = client.get("/notifications?status=failed&limit=10")
    delivered = client.get("/notifications?status=delivered&limit=10")
    discord_failed = client.get("/notifications?agent=discord&status=failed&limit=10")

    assert failed.status_code == 200
    assert "Partial failure" in failed.text
    assert "Legacy failure" in failed.text
    assert "All delivered" not in failed.text
    assert delivered.status_code == 200
    assert "All delivered" in delivered.text
    assert "Partial failure" not in delivered.text
    assert "Legacy failure" not in delivered.text
    assert discord_failed.status_code == 200
    assert "Partial failure" in discord_failed.text
    assert "All delivered" not in discord_failed.text


def test_notification_log_clear_deletes_events_and_deliveries(monkeypatch):
    client, db = make_client()
    event = NotificationEvent(event_type="new_book", title="Book event", body="Example", delivered=True)
    db.add(event)
    db.commit()
    db.add(NotificationDelivery(event_id=event.id, agent="gotify", delivered=True, error=""))
    db.commit()

    monkeypatch.setattr(web_routes, "validate_csrf_token", lambda request, token: True)
    response = client.post(
        "/notifications/clear",
        data={"csrf_token": "valid-token"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/notifications"
    assert db.query(NotificationDelivery).count() == 0
    assert db.query(NotificationEvent).count() == 0


def test_notification_log_clear_rejects_invalid_csrf(monkeypatch):
    client, db = make_client()
    db.add(NotificationEvent(event_type="new_book", title="Book event", body="Example", delivered=True))
    db.commit()

    monkeypatch.setattr(web_routes, "validate_csrf_token", lambda request, token: False)
    response = client.post(
        "/notifications/clear",
        data={"csrf_token": "invalid-token"},
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert db.query(NotificationEvent).count() == 1


def test_notification_log_clear_form_uses_javascript_confirmation_hook():
    client, db = make_client()
    db.add(NotificationEvent(event_type="new_book", title="Book event", body="Example", delivered=True))
    db.commit()

    response = client.get("/notifications")

    assert response.status_code == 200
    assert 'data-notification-clear' in response.text
    assert 'onsubmit=' not in response.text


def test_notification_log_clear_button_stays_enabled_when_filters_match_nothing():
    client, db = make_client()
    db.add(NotificationEvent(event_type="new_book", title="Book event", body="Example", delivered=True))
    db.commit()

    response = client.get("/notifications?type=playback_start")

    assert response.status_code == 200
    assert "No notification events found." in response.text
    assert 'class="notification-clear-button" type="submit" disabled' not in response.text


def test_notification_delivery_error_is_sanitized_before_storage(monkeypatch):
    from absulli.core.config import get_settings
    from absulli.notifiers.manager import NotificationManager

    client, db = make_client()
    assert client

    class FakeAgent:
        async def send(self, title, message, extra=None):
            raise RuntimeError(
                "Client error '401 Unauthorized' for url "
                "'https://discord.com/api/webhooks/123/secret-token'"
            )

    manager = NotificationManager(get_settings())
    monkeypatch.setattr(manager, "named_agents", lambda: [("discord", FakeAgent())])
    monkeypatch.setattr(
        web_routes,
        "get_setup_setting",
        lambda key, default="": "true" if key == "discord_notify_new_book" else default,
    )

    import absulli.notifiers.manager as notification_manager
    monkeypatch.setattr(
        notification_manager.setup_state,
        "get_setup_setting",
        lambda key, default="": "true" if key == "discord_notify_new_book" else default,
    )

    import asyncio
    asyncio.run(manager.notify(db, "new_book", "New book added", "Example"))

    delivery = db.query(NotificationDelivery).one()
    assert delivery.delivered is False
    assert delivery.error == "Client error '401 Unauthorized'"
    assert "secret-token" not in delivery.error
