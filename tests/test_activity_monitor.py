import asyncio
from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from absulli.core.time import utcnow
from absulli.database.models import AbsUser, ActivitySession, Base, Library, MediaItem
from absulli.monitors.activity import ActivityMonitor


class FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    async def get_online_users(self):
        self.calls += 1
        return self.payload


class FakeNotifier:
    def __init__(self):
        self.events = []

    async def notify(self, db, event_type, title, body, library_id=""):
        event = {"event_type": event_type, "title": title, "body": body}
        if library_id:
            event["library_id"] = library_id
        self.events.append(event)


def make_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal()


def online_payload(*rows):
    return {"openSessions": list(rows)}


def online_session(**overrides):
    row = {
        "id": "session-1",
        "userId": "user-1",
        "username": "raw-username",
        "libraryItemId": "item-1",
        "displayTitle": "Unknown",
        "currentTime": 30,
        "duration": 300,
        "mediaType": "",
        "deviceInfo": {"deviceName": "Pixel", "model": "Pixel 9"},
        "client": {"name": "Audiobookshelf"},
        "ipAddress": "192.0.2.10",
        "startedAt": "2026-06-09T21:00:00Z",
        "updatedAt": "2026-06-09T21:05:00Z",
    }
    row.update(overrides)
    return row


def test_poll_creates_active_session_enriches_from_db_and_notifies_start():
    db = make_db()
    db.add_all(
        [
            AbsUser(abs_user_id="user-1", username="raw-username", display_name="Friendly Name"),
            Library(abs_library_id="lib-1", name="Audiobooks", media_type="book"),
            MediaItem(
                abs_item_id="item-1",
                library_id="lib-1",
                library_name="",
                media_type="book",
                title="Transfer of Power",
                author="Vince Flynn",
            ),
        ]
    )
    db.commit()

    notifier = FakeNotifier()
    monitor = ActivityMonitor(FakeClient(online_payload(online_session())), notifier)

    count = asyncio.run(monitor.poll(db))

    row = db.query(ActivitySession).filter_by(session_key="session-1").one()
    assert count == 1
    assert row.is_active is True
    assert row.username == "Friendly Name"
    assert row.title == "Transfer of Power"
    assert row.author == "Vince Flynn"
    assert row.media_type == "book"
    assert row.library_id == "lib-1"
    assert row.library_name == "Audiobooks"
    assert row.progress == 10
    assert notifier.events == [
        {
            "event_type": "playback_start",
            "title": "Friendly Name started listening",
            "body": "Transfer of Power",
            "library_id": "lib-1",
        }
    ]
    db.close()


def test_poll_updates_existing_session_without_duplicate_start_notification():
    db = make_db()
    db.add(
        ActivitySession(
            session_key="session-1",
            abs_user_id="user-1",
            username="Old User",
            abs_item_id="item-1",
            title="Old Title",
            current_time=10,
            duration=100,
            progress=10,
            is_active=True,
            last_seen_at=utcnow() - timedelta(seconds=10),
        )
    )
    db.commit()

    notifier = FakeNotifier()
    monitor = ActivityMonitor(
        FakeClient(
            online_payload(
                online_session(
                    username="Updated User",
                    displayTitle="Updated Title",
                    currentTime=75,
                    duration=100,
                )
            )
        ),
        notifier,
    )

    count = asyncio.run(monitor.poll(db))

    row = db.query(ActivitySession).filter_by(session_key="session-1").one()
    assert count == 1
    assert row.is_active is True
    assert row.username == "Updated User"
    assert row.title == "Updated Title"
    assert row.current_time == 75
    assert row.progress == 75
    assert notifier.events == []
    db.close()



def test_poll_reactivated_existing_session_notifies_start():
    db = make_db()
    db.add(
        ActivitySession(
            session_key="session-1",
            abs_user_id="user-1",
            username="Old User",
            abs_item_id="item-1",
            title="Old Title",
            current_time=10,
            duration=100,
            progress=10,
            is_active=False,
            last_seen_at=utcnow() - timedelta(seconds=90),
        )
    )
    db.commit()

    notifier = FakeNotifier()
    monitor = ActivityMonitor(
        FakeClient(
            online_payload(
                online_session(
                    username="Updated User",
                    displayTitle="Updated Title",
                    currentTime=75,
                    duration=100,
                )
            )
        ),
        notifier,
    )

    count = asyncio.run(monitor.poll(db))

    row = db.query(ActivitySession).filter_by(session_key="session-1").one()
    assert count == 1
    assert row.is_active is True
    assert row.username == "Updated User"
    assert row.title == "Updated Title"
    assert notifier.events == [
        {
            "event_type": "playback_start",
            "title": "Updated User started listening",
            "body": "Updated Title",
        }
    ]
    db.close()

def test_poll_marks_stale_missing_sessions_inactive_and_notifies_stop():
    db = make_db()
    db.add(
        ActivitySession(
            session_key="stale-session",
            abs_user_id="user-1",
            username="Kenny",
            abs_item_id="item-1",
            title="Book That Stopped",
            is_active=True,
            last_seen_at=utcnow() - timedelta(seconds=90),
        )
    )
    db.commit()

    notifier = FakeNotifier()
    monitor = ActivityMonitor(FakeClient(online_payload()), notifier)

    count = asyncio.run(monitor.poll(db))

    row = db.query(ActivitySession).filter_by(session_key="stale-session").one()
    assert count == 0
    assert row.is_active is False
    assert notifier.events == [
        {
            "event_type": "playback_stop",
            "title": "Kenny stopped listening",
            "body": "Book That Stopped",
        }
    ]
    db.close()


def test_poll_keeps_recent_missing_sessions_active_until_stale_cutoff():
    db = make_db()
    db.add(
        ActivitySession(
            session_key="recent-session",
            abs_user_id="user-1",
            username="Kenny",
            abs_item_id="item-1",
            title="Still Within Grace Period",
            is_active=True,
            last_seen_at=utcnow() - timedelta(seconds=10),
        )
    )
    db.commit()

    notifier = FakeNotifier()
    monitor = ActivityMonitor(FakeClient(online_payload()), notifier)

    count = asyncio.run(monitor.poll(db))

    row = db.query(ActivitySession).filter_by(session_key="recent-session").one()
    assert count == 0
    assert row.is_active is True
    assert notifier.events == []
    db.close()


def test_poll_batches_existing_session_lookup_and_filters_stale_query():
    from sqlalchemy import event

    db = make_db()
    db.add_all(
        [
            ActivitySession(
                session_key="session-1",
                abs_user_id="user-1",
                username="Existing One",
                abs_item_id="item-1",
                title="Old One",
                is_active=True,
                last_seen_at=utcnow() - timedelta(seconds=10),
            ),
            ActivitySession(
                session_key="session-2",
                abs_user_id="user-2",
                username="Existing Two",
                abs_item_id="item-2",
                title="Old Two",
                is_active=True,
                last_seen_at=utcnow() - timedelta(seconds=10),
            ),
            ActivitySession(
                session_key="session-3",
                abs_user_id="user-3",
                username="Existing Three",
                abs_item_id="item-3",
                title="Old Three",
                is_active=True,
                last_seen_at=utcnow() - timedelta(seconds=10),
            ),
            ActivitySession(
                session_key="stale-session",
                abs_user_id="user-3",
                username="Stale User",
                abs_item_id="item-3",
                title="Stopped Book",
                is_active=True,
                last_seen_at=utcnow() - timedelta(seconds=90),
            ),
            ActivitySession(
                session_key="recent-missing-session",
                abs_user_id="user-4",
                username="Recent Missing User",
                abs_item_id="item-4",
                title="Still Grace Period",
                is_active=True,
                last_seen_at=utcnow() - timedelta(seconds=10),
            ),
        ]
    )
    db.commit()

    notifier = FakeNotifier()
    monitor = ActivityMonitor(
        FakeClient(
            online_payload(
                online_session(id="session-1", userId="user-1", libraryItemId="item-1"),
                online_session(id="session-2", userId="user-2", libraryItemId="item-2"),
                online_session(id="session-3", userId="user-3", libraryItemId="item-3"),
            )
        ),
        notifier,
    )

    activity_selects = []

    def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        if statement.strip().lower().startswith("select") and "from activity_sessions" in statement.lower():
            activity_selects.append(statement)

    event.listen(db.bind, "before_cursor_execute", before_cursor_execute)
    try:
        count = asyncio.run(monitor.poll(db))
    finally:
        event.remove(db.bind, "before_cursor_execute", before_cursor_execute)

    assert count == 3
    assert len(activity_selects) == 2
    assert any("activity_sessions.session_key in" in statement.lower() for statement in activity_selects)
    assert any("activity_sessions.last_seen_at <" in statement.lower() for statement in activity_selects)

    stale = db.query(ActivitySession).filter_by(session_key="stale-session").one()
    recent = db.query(ActivitySession).filter_by(session_key="recent-missing-session").one()
    assert stale.is_active is False
    assert recent.is_active is True
    assert notifier.events == [
        {
            "event_type": "playback_stop",
            "title": "Stale User stopped listening",
            "body": "Stopped Book",
        }
    ]
    db.close()
