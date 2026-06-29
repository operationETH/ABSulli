
from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from absulli.core.time import utcnow
from absulli.database.models import ActivitySession, Base, Library, ListeningHistory, MediaItem
from absulli.web.queries import active_sessions_query, accumulate_history_stats, build_home_cards, window_stats_for_history


def make_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal()


def history_row(
    *,
    session_id: str,
    user_id: str = "user-1",
    username: str = "Kenny",
    item_id: str = "book-1",
    title: str = "Book One",
    library_id: str = "lib-1",
    library_name: str = "Audiobooks",
    client: str = "Web",
    duration_seconds: float = 60,
    started_delta: timedelta = timedelta(hours=1),
) -> ListeningHistory:
    started_at = utcnow() - started_delta
    return ListeningHistory(
        abs_session_id=session_id,
        abs_user_id=user_id,
        username=username,
        abs_item_id=item_id,
        title=title,
        media_type="book",
        library_id=library_id,
        library_name=library_name,
        started_at=started_at,
        updated_at=started_at,
        imported_at=started_at,
        duration_seconds=duration_seconds,
        client=client,
    )



def test_active_sessions_query_filters_incomplete_online_user_rows():
    db = make_db()
    now = utcnow()
    db.add_all(
        [
            ActivitySession(
                session_key="ghost-user",
                abs_user_id="user-1",
                username="admin",
                title="Unknown",
                media_type="unknown",
                current_time=0,
                duration=0,
                time_listening=0,
                is_active=True,
                last_seen_at=now,
            ),
            ActivitySession(
                session_key="real-session",
                abs_user_id="user-1",
                username="admin",
                abs_item_id="item-1",
                title="Real Book",
                media_type="book",
                current_time=30,
                duration=300,
                is_active=True,
                last_seen_at=now,
            ),
        ]
    )
    db.commit()

    rows = active_sessions_query(db).all()

    assert [row.session_key for row in rows] == ["real-session"]
    db.close()

def test_accumulate_history_stats_groups_sorts_and_limits_rows():
    rows = [
        history_row(session_id="s1", username="Alice", duration_seconds=30),
        history_row(session_id="s2", username="Bob", duration_seconds=600),
        history_row(session_id="s3", username="Alice", duration_seconds=45),
        history_row(session_id="s4", username="", duration_seconds=10),
    ]

    result = accumulate_history_stats(
        rows,
        key_fn=lambda row: row.username,
        seed_fn=lambda row, key: {"username": key, "plays": 0, "seconds": 0, "last_title": ""},
        limit=2,
        update_fn=lambda stats, row: stats.update({"last_title": row.title}),
    )

    assert result == [
        {"username": "Alice", "plays": 2, "seconds": 75.0, "last_title": "Book One"},
        {"username": "Bob", "plays": 1, "seconds": 600.0, "last_title": "Book One"},
    ]


def test_window_stats_for_history_counts_rows_inside_each_window():
    rows = [
        history_row(session_id="recent", duration_seconds=60, started_delta=timedelta(hours=1)),
        history_row(session_id="week", duration_seconds=120, started_delta=timedelta(days=3)),
        history_row(session_id="month", duration_seconds=180, started_delta=timedelta(days=20)),
        history_row(session_id="old", duration_seconds=240, started_delta=timedelta(days=45)),
    ]

    stats = {row["label"]: row for row in window_stats_for_history(rows)}

    assert stats["Last 24 hours"] == {"label": "Last 24 hours", "plays": 1, "duration": "1m"}
    assert stats["Last 7 days"] == {"label": "Last 7 days", "plays": 2, "duration": "3m"}
    assert stats["Last 30 days"] == {"label": "Last 30 days", "plays": 3, "duration": "6m"}
    assert stats["All Time"] == {"label": "All Time", "plays": 4, "duration": "10m"}


def test_build_home_cards_returns_expected_count_cards_from_history():
    db = make_db()
    db.add_all(
        [
            Library(abs_library_id="lib-1", name="Audiobooks", media_type="book"),
            MediaItem(
                abs_item_id="book-1",
                library_id="lib-1",
                library_name="Audiobooks",
                media_type="book",
                title="Book One",
                author="Author One",
            ),
            MediaItem(
                abs_item_id="book-2",
                library_id="lib-1",
                library_name="Audiobooks",
                media_type="book",
                title="Book Two",
                author="Author Two",
            ),
            history_row(
                session_id="s1",
                user_id="user-1",
                username="Alice",
                item_id="book-1",
                title="Book One",
                client="Web",
                duration_seconds=60,
            ),
            history_row(
                session_id="s2",
                user_id="user-2",
                username="Bob",
                item_id="book-1",
                title="Book One",
                client="Mobile",
                duration_seconds=120,
            ),
            history_row(
                session_id="s3",
                user_id="user-1",
                username="Alice",
                item_id="book-2",
                title="Book Two",
                client="Web",
                duration_seconds=30,
            ),
        ]
    )
    db.commit()

    cards = build_home_cards(db, metric="count", days=30)
    cards_by_title = {card["title"]: card for card in cards}

    assert cards_by_title["Most Played Books"]["subtitle"] == "plays"
    assert cards_by_title["Most Played Books"]["cover_item_id"] == "book-1"
    assert cards_by_title["Most Played Books"]["items"][0] == {
        "item_id": "book-1",
        "name": "Book One",
        "value": "2",
    }

    assert cards_by_title["Most Popular Books"]["items"][0] == {
        "item_id": "book-1",
        "name": "Book One",
        "value": "2",
    }

    assert cards_by_title["Most Active Users"]["items"][0] == {
        "name": "Alice",
        "value": "2",
        "url": "/users/Alice",
    }

    assert cards_by_title["Most Active Libraries"]["items"][0] == {
        "name": "Audiobooks",
        "value": "3",
        "url": "/libraries/lib-1",
    }

    assert cards_by_title["Most Active Platforms"]["items"][0] == {"name": "Web", "value": "2"}
    db.close()


def test_build_home_cards_duration_metric_formats_seconds():
    db = make_db()
    db.add_all(
        [
            Library(abs_library_id="lib-1", name="Audiobooks", media_type="book"),
            MediaItem(
                abs_item_id="book-1",
                library_id="lib-1",
                library_name="Audiobooks",
                media_type="book",
                title="Book One",
                author="Author One",
            ),
            history_row(
                session_id="s1",
                item_id="book-1",
                title="Book One",
                duration_seconds=3660,
            ),
            history_row(
                session_id="s2",
                item_id="book-1",
                title="Book One",
                duration_seconds=60,
            ),
        ]
    )
    db.commit()

    cards = build_home_cards(db, metric="duration", days=30)
    most_listened = next(card for card in cards if card["title"] == "Most Listened Books")

    assert most_listened["subtitle"] == "duration"
    assert most_listened["items"][0] == {"item_id": "book-1", "name": "Book One", "value": "1h 2m"}
    db.close()
