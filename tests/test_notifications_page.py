from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

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
    assert "Details" in response.text
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
