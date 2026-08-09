import asyncio
from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from absulli.core.time import utcnow
from absulli.database.models import AbsUser, Base, Library, ListeningHistory, MediaItem
from absulli.monitors.history import HistoryMonitor


class FakeClient:
    def __init__(
        self,
        *,
        users=None,
        libraries=None,
        library_items=None,
        sessions=None,
        fail_sessions_for=None,
    ):
        self.users = users if users is not None else {"users": []}
        self.libraries = libraries if libraries is not None else {"libraries": []}
        self.library_items = library_items if library_items is not None else {}
        self.sessions = sessions if sessions is not None else {}
        self.fail_sessions_for = set(fail_sessions_for or [])
        self.calls = {
            "get_users": 0,
            "get_libraries": 0,
            "get_library_items": [],
            "get_user_listening_sessions": [],
        }

    async def get_users(self):
        self.calls["get_users"] += 1
        return self.users

    async def get_libraries(self):
        self.calls["get_libraries"] += 1
        return self.libraries

    async def get_library_items(self, library_id, limit=5000):
        self.calls["get_library_items"].append((library_id, limit))
        return self.library_items.get(library_id, {"results": [], "total": 0})

    async def get_user_listening_sessions(self, user_id, items_per_page=50, page=0):
        self.calls["get_user_listening_sessions"].append((user_id, items_per_page, page))
        if user_id in self.fail_sessions_for:
            raise RuntimeError("ABS history unavailable")
        user_sessions = self.sessions.get(user_id, {"sessions": []})
        if isinstance(user_sessions, dict) and page in user_sessions:
            return user_sessions[page]
        return user_sessions


def make_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal()


def users_payload(*rows):
    return {"users": list(rows)}


def libraries_payload(*rows):
    return {"libraries": list(rows)}


def items_payload(*rows, total=None):
    payload = {"results": list(rows)}
    if total is not None:
        payload["total"] = total
    return payload


def sessions_payload(*rows):
    return {"sessions": list(rows)}


def user_row(**overrides):
    row = {
        "id": "user-1",
        "username": "raw-user",
        "displayName": "Friendly User",
        "isDisabled": False,
    }
    row.update(overrides)
    return row


def library_row(**overrides):
    row = {
        "id": "lib-1",
        "name": "Audiobooks",
        "mediaType": "book",
        "numItems": 1,
        "displayOrder": 2,
    }
    row.update(overrides)
    return row


def media_item_row(**overrides):
    row = {
        "id": "item-1",
        "libraryId": "lib-1",
        "mediaType": "book",
        "media": {
            "metadata": {
                "title": "Transfer of Power",
                "authors": [{"id": "author-1", "name": "Vince Flynn"}],
                "narratorName": "Nick Sullivan",
                "series": [{"name": "Mitch Rapp"}],
                "publishedYear": "1999",
            },
            "duration": 3600,
            "coverPath": "/cover/item-1",
        },
        "sizeBytes": 123456,
        "addedAt": "2026-06-01T12:00:00Z",
    }
    row.update(overrides)
    return row


def history_row(**overrides):
    row = {
        "id": "session-1",
        "userId": "user-1",
        "username": "raw-user",
        "libraryItemId": "item-1",
        "displayTitle": "Unknown",
        "mediaType": "",
        "timeListening": 120,
        "currentTime": 300,
        "duration": 600,
        "deviceInfo": {"deviceName": "Pixel", "model": "Pixel 9"},
        "client": {"name": "Audiobookshelf"},
        "startedAt": "2026-06-09T20:00:00Z",
        "updatedAt": "2026-06-09T20:10:00Z",
    }
    row.update(overrides)
    return row


def test_poll_syncs_reference_data_imports_history_and_enriches_from_media_items():
    db = make_db()
    client = FakeClient(
        users=users_payload(user_row()),
        libraries=libraries_payload(library_row()),
        library_items={"lib-1": items_payload(media_item_row(), total=1)},
        sessions={"user-1": sessions_payload(history_row())},
    )
    monitor = HistoryMonitor(client)

    imported = asyncio.run(monitor.poll(db))

    user = db.query(AbsUser).filter_by(abs_user_id="user-1").one()
    library = db.query(Library).filter_by(abs_library_id="lib-1").one()
    media_item = db.query(MediaItem).filter_by(abs_item_id="item-1").one()
    history = db.query(ListeningHistory).filter_by(abs_session_id="session-1").one()

    assert imported == 1
    assert user.username == "raw-user"
    assert user.display_name == "Friendly User"
    assert library.name == "Audiobooks"
    assert media_item.title == "Transfer of Power"
    assert media_item.author == "Vince Flynn"
    assert history.username == "Friendly User"
    assert history.title == "Transfer of Power"
    assert history.author == "Vince Flynn"
    assert history.media_type == "book"
    assert history.library_id == "lib-1"
    assert history.library_name == "Audiobooks"
    assert history.duration_seconds == 120
    assert history.current_time == 300
    assert history.progress == 50
    assert client.calls["get_library_items"] == [("lib-1", 5000)]
    assert client.calls["get_user_listening_sessions"] == [("user-1", 50, 0)]
    db.close()


def test_poll_updates_existing_history_row_without_incrementing_import_count():
    db = make_db()
    db.add(
        ListeningHistory(
            abs_session_id="session-1",
            abs_user_id="user-1",
            username="Old User",
            abs_item_id="item-1",
            title="Old Title",
            duration_seconds=60,
            current_time=60,
            progress=10,
        )
    )
    db.commit()

    client = FakeClient(
        users=users_payload(user_row()),
        libraries=libraries_payload(),
        sessions={
            "user-1": sessions_payload(
                history_row(
                    displayTitle="Updated Title",
                    timeListening=240,
                    currentTime=480,
                    duration=960,
                )
            )
        },
    )
    monitor = HistoryMonitor(client)

    imported = asyncio.run(monitor.poll(db))

    rows = db.query(ListeningHistory).filter_by(abs_session_id="session-1").all()
    assert imported == 0
    assert len(rows) == 1
    assert rows[0].username == "Friendly User"
    assert rows[0].title == "Updated Title"
    assert rows[0].duration_seconds == 240
    assert rows[0].progress == 50
    db.close()


def test_poll_skips_unknown_users_and_continues_when_one_user_history_fails():
    db = make_db()
    client = FakeClient(
        users=users_payload(
            user_row(id="unknown", username="unknown", displayName=""),
            user_row(id="user-1", username="raw-user", displayName="Friendly User"),
            user_row(id="user-2", username="broken-user", displayName="Broken User"),
        ),
        libraries=libraries_payload(),
        sessions={"user-1": sessions_payload(history_row())},
        fail_sessions_for={"user-2"},
    )
    monitor = HistoryMonitor(client)

    imported = asyncio.run(monitor.poll(db))

    assert imported == 1
    assert db.query(ListeningHistory).count() == 1
    assert db.query(ListeningHistory).one().abs_user_id == "user-1"
    assert client.calls["get_user_listening_sessions"] == [("user-1", 50, 0), ("user-2", 50, 0)]
    db.close()


def test_sync_recent_items_updates_existing_items_and_prunes_deleted_items_when_full_library_was_loaded():
    db = make_db()
    library = Library(abs_library_id="lib-1", name="Audiobooks", media_type="book")
    db.add_all(
        [
            library,
            MediaItem(
                abs_item_id="item-1",
                library_id="lib-1",
                library_name="Old Library Name",
                media_type="book",
                title="Old Title",
                author="Old Author",
            ),
            MediaItem(
                abs_item_id="deleted-item",
                library_id="lib-1",
                library_name="Audiobooks",
                media_type="book",
                title="Deleted Title",
                author="Deleted Author",
            ),
        ]
    )
    db.commit()

    client = FakeClient(
        library_items={
            "lib-1": items_payload(
                media_item_row(
                    media={
                        "metadata": {"title": "Updated Title", "authors": [{"name": "Updated Author"}]},
                        "duration": 7200,
                    },
                ),
                media_item_row(id="item-2", media={"metadata": {"title": "New Title", "author": "New Author"}}),
                total=2,
            )
        }
    )
    monitor = HistoryMonitor(client)

    imported = asyncio.run(monitor.sync_recent_items(db, [library]))

    assert imported == 1
    assert db.query(MediaItem).filter_by(abs_item_id="deleted-item").first() is None
    updated = db.query(MediaItem).filter_by(abs_item_id="item-1").one()
    created = db.query(MediaItem).filter_by(abs_item_id="item-2").one()
    assert updated.title == "Updated Title"
    assert updated.author == "Updated Author"
    assert updated.library_name == "Audiobooks"
    assert created.title == "New Title"
    assert created.author == "New Author"
    db.close()


def test_sync_recent_items_saves_library_total_even_when_only_one_result_page_is_imported():
    db = make_db()
    library = Library(abs_library_id="lib-1", name="Audiobooks", media_type="book")
    db.add(library)
    db.commit()

    client = FakeClient(
        library_items={
            "lib-1": items_payload(
                media_item_row(id="item-1"),
                media_item_row(id="item-2"),
                total=886,
            )
        }
    )
    monitor = HistoryMonitor(client)

    imported = asyncio.run(monitor.sync_recent_items(db, [library]))

    assert imported == 2
    assert db.query(Library).filter_by(abs_library_id="lib-1").one().item_count == 886
    assert db.query(MediaItem).filter_by(library_id="lib-1").count() == 2
    db.close()


def test_enrich_history_row_uses_library_name_when_media_item_has_only_library_id():
    db = make_db()
    db.add_all(
        [
            Library(abs_library_id="lib-1", name="Podcasts", media_type="podcast"),
            MediaItem(
                abs_item_id="item-1",
                library_id="lib-1",
                library_name="",
                media_type="podcast",
                title="The Extra Point",
                author="",
            ),
        ]
    )
    db.commit()

    row = {
        "abs_item_id": "item-1",
        "title": "Unknown",
        "author": "",
        "media_type": "unknown",
        "library_id": "",
        "library_name": "",
    }
    monitor = HistoryMonitor(FakeClient())
    media_map = monitor._media_items_by_abs_id(db, ["item-1"])
    library_map = monitor._libraries_by_abs_id(db)
    monitor._enrich_history_row_from_media_maps(row, media_map, library_map)

    assert row == {
        "abs_item_id": "item-1",
        "title": "The Extra Point",
        "author": "",
        "media_type": "podcast",
        "library_id": "lib-1",
        "library_name": "Podcasts",
    }
    db.close()

def test_fetch_user_history_rows_fetches_and_merges_multiple_pages():
    client = FakeClient(
        sessions={
            "user-1": {
                0: {"sessions": [history_row(id="session-1")], "hasNextPage": True},
                1: {"sessions": [history_row(id="session-2", libraryItemId="item-2")], "hasNextPage": False},
            }
        }
    )
    monitor = HistoryMonitor(client)

    rows = asyncio.run(monitor._fetch_user_history_rows("user-1", items_per_page=1))

    assert [row["abs_session_id"] for row in rows] == ["session-1", "session-2"]
    assert client.calls["get_user_listening_sessions"] == [("user-1", 1, 0), ("user-1", 1, 1)]



def test_payload_has_next_page_supports_total_pages_total_count_and_page_size_fallback():
    monitor = HistoryMonitor(FakeClient())

    assert monitor._payload_has_next_page(
        {"sessions": [history_row(id="s1")], "totalPages": 2},
        page=0,
        row_count=1,
        items_per_page=50,
        loaded_count=1,
    ) is True
    assert monitor._payload_has_next_page(
        {"sessions": [history_row(id="s2")], "totalPages": 2},
        page=1,
        row_count=1,
        items_per_page=50,
        loaded_count=2,
    ) is False

    assert monitor._payload_has_next_page(
        {"sessions": [history_row(id="s3")], "total": 3},
        page=0,
        row_count=1,
        items_per_page=50,
        loaded_count=1,
    ) is True
    assert monitor._payload_has_next_page(
        {"sessions": [history_row(id="s4")], "total": 2},
        page=1,
        row_count=1,
        items_per_page=50,
        loaded_count=2,
    ) is False

    assert monitor._payload_has_next_page(
        {"sessions": [history_row(id="s5"), history_row(id="s6")]},
        page=0,
        row_count=2,
        items_per_page=2,
        loaded_count=2,
    ) is True
    assert monitor._payload_has_next_page(
        {"sessions": [history_row(id="s7")]},
        page=1,
        row_count=1,
        items_per_page=2,
        loaded_count=3,
    ) is False


def count_sql_for(db, table_name):
    statements = []

    def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        if table_name in statement.lower():
            statements.append(statement.lower())

    return statements, before_cursor_execute


def test_sync_users_batches_existing_user_lookup():
    from sqlalchemy import event

    db = make_db()
    db.add(AbsUser(abs_user_id="user-1", username="old-user", display_name="Old User"))
    db.commit()

    client = FakeClient(
        users=users_payload(
            user_row(id="user-1", username="updated-user", displayName="Updated User"),
            user_row(id="user-2", username="new-user", displayName="New User"),
        )
    )
    monitor = HistoryMonitor(client)
    statements, listener = count_sql_for(db, "abs_users")

    event.listen(db.bind, "before_cursor_execute", listener)
    try:
        saved = asyncio.run(monitor.sync_users(db))
    finally:
        event.remove(db.bind, "before_cursor_execute", listener)

    select_statements = [statement for statement in statements if statement.lstrip().startswith("select")]
    assert len(saved) == 2
    assert len(select_statements) == 1
    assert " in " in select_statements[0]
    assert db.query(AbsUser).filter_by(abs_user_id="user-1").one().display_name == "Updated User"
    assert db.query(AbsUser).filter_by(abs_user_id="user-2").one().username == "new-user"
    db.close()


def test_sync_libraries_batches_existing_library_lookup():
    from sqlalchemy import event

    db = make_db()
    db.add(Library(abs_library_id="lib-1", name="Old Library", media_type="book"))
    db.commit()

    client = FakeClient(
        libraries=libraries_payload(
            library_row(id="lib-1", name="Updated Library", numItems=10),
            library_row(id="lib-2", name="New Library", mediaType="podcast", numItems=2),
        )
    )
    monitor = HistoryMonitor(client)
    statements, listener = count_sql_for(db, "libraries")

    event.listen(db.bind, "before_cursor_execute", listener)
    try:
        saved = asyncio.run(monitor.sync_libraries(db))
    finally:
        event.remove(db.bind, "before_cursor_execute", listener)

    select_statements = [statement for statement in statements if statement.lstrip().startswith("select")]
    assert len(saved) == 2
    assert len(select_statements) == 1
    assert " in " in select_statements[0]
    assert db.query(Library).filter_by(abs_library_id="lib-1").one().name == "Updated Library"
    assert db.query(Library).filter_by(abs_library_id="lib-2").one().media_type == "podcast"
    db.close()


def test_sync_recent_items_batches_existing_media_lookup():
    from sqlalchemy import event

    db = make_db()
    library = Library(abs_library_id="lib-1", name="Audiobooks", media_type="book")
    db.add_all(
        [
            library,
            MediaItem(abs_item_id="item-1", library_id="lib-1", title="Old Title", media_type="book"),
            MediaItem(abs_item_id="deleted-item", library_id="lib-1", title="Deleted", media_type="book"),
        ]
    )
    db.commit()

    client = FakeClient(
        library_items={
            "lib-1": items_payload(
                media_item_row(id="item-1", media={"metadata": {"title": "Updated Title"}}),
                media_item_row(id="item-2", media={"metadata": {"title": "New Title"}}),
                total=2,
            )
        }
    )
    monitor = HistoryMonitor(client)
    statements, listener = count_sql_for(db, "media_items")

    event.listen(db.bind, "before_cursor_execute", listener)
    try:
        imported = asyncio.run(monitor.sync_recent_items(db, [library]))
    finally:
        event.remove(db.bind, "before_cursor_execute", listener)

    select_statements = [statement for statement in statements if statement.lstrip().startswith("select")]
    assert imported == 1
    assert len(select_statements) == 1
    assert " in " in select_statements[0]
    assert db.query(MediaItem).filter_by(abs_item_id="item-1").one().title == "Updated Title"
    assert db.query(MediaItem).filter_by(abs_item_id="item-2").one().title == "New Title"
    assert db.query(MediaItem).filter_by(abs_item_id="deleted-item").first() is None
    db.close()


def test_sync_users_empty_payload_returns_empty_without_selecting_users():
    from sqlalchemy import event

    db = make_db()
    client = FakeClient(users=users_payload())
    monitor = HistoryMonitor(client)
    statements, listener = count_sql_for(db, "abs_users")

    event.listen(db.bind, "before_cursor_execute", listener)
    try:
        saved = asyncio.run(monitor.sync_users(db))
    finally:
        event.remove(db.bind, "before_cursor_execute", listener)

    select_statements = [statement for statement in statements if statement.lstrip().startswith("select")]
    assert saved == []
    assert select_statements == []
    assert db.query(AbsUser).count() == 0
    db.close()


def test_sync_users_duplicate_payload_ids_update_one_row():
    db = make_db()
    client = FakeClient(
        users=users_payload(
            user_row(id="user-1", username="first-name", displayName="First Name"),
            user_row(id="user-1", username="second-name", displayName="Second Name"),
        )
    )
    monitor = HistoryMonitor(client)

    saved = asyncio.run(monitor.sync_users(db))

    rows = db.query(AbsUser).filter_by(abs_user_id="user-1").all()
    assert len(saved) == 2
    assert len(rows) == 1
    assert rows[0].username == "second-name"
    assert rows[0].display_name == "Second Name"
    db.close()


def test_enrich_history_row_without_prefetched_lookups_uses_database_fallbacks():
    db = make_db()
    db.add_all(
        [
            Library(abs_library_id="lib-1", name="Fallback Library", media_type="book"),
            MediaItem(
                abs_item_id="item-1",
                library_id="lib-1",
                library_name="",
                media_type="book",
                title="Fallback Title",
                author="Fallback Author",
            ),
        ]
    )
    db.commit()

    row = {
        "abs_item_id": "item-1",
        "title": "Unknown",
        "author": "",
        "media_type": "unknown",
        "library_id": "",
        "library_name": "",
    }

    monitor = HistoryMonitor(FakeClient())
    media_map = monitor._media_items_by_abs_id(db, ["item-1"])
    library_map = monitor._libraries_by_abs_id(db)
    monitor._enrich_history_row_from_media_maps(row, media_map, library_map)

    assert row["title"] == "Fallback Title"
    assert row["author"] == "Fallback Author"
    assert row["media_type"] == "book"
    assert row["library_id"] == "lib-1"
    assert row["library_name"] == "Fallback Library"
    db.close()

class FakeNotifier:
    def __init__(self):
        self.calls = []

    async def notify(self, db, event_type, title, body):
        self.calls.append((event_type, title, body))


def test_new_book_notifications_start_after_silent_library_baseline():
    db = make_db()
    library = Library(
        abs_library_id="lib-1",
        name="Audiobooks",
        media_type="book",
        item_count=1,
    )
    db.add(library)
    db.commit()

    client = FakeClient(
        library_items={"lib-1": items_payload(media_item_row(), total=1)},
    )
    notifier = FakeNotifier()
    monitor = HistoryMonitor(client, notifier)

    first_imported = asyncio.run(monitor.sync_recent_items(db, [library]))

    assert first_imported == 1
    assert notifier.calls == []

    second_book = media_item_row(
        id="item-2",
        media={
            "metadata": {
                "title": "American Assassin",
                "authors": [{"id": "author-1", "name": "Vince Flynn"}],
            },
            "duration": 4200,
        },
    )
    client.library_items["lib-1"] = items_payload(media_item_row(), second_book, total=2)

    second_imported = asyncio.run(monitor.sync_recent_items(db, [library]))

    assert second_imported == 1
    assert notifier.calls == [
        (
            "new_book",
            "New book added",
            "American Assassin by Vince Flynn was added to Audiobooks.",
        )
    ]
    db.close()


def test_new_podcast_notifications_start_after_silent_library_baseline():
    db = make_db()
    library = Library(
        abs_library_id="lib-podcast",
        name="Podcasts",
        media_type="podcast",
        item_count=1,
    )
    db.add(library)
    db.commit()

    first_podcast = media_item_row(
        id="podcast-1",
        media={
            "metadata": {
                "title": "Existing Podcast",
                "author": "Existing Author",
            },
            "duration": 0,
        },
    )
    client = FakeClient(
        library_items={"lib-podcast": items_payload(first_podcast, total=1)},
    )
    notifier = FakeNotifier()
    monitor = HistoryMonitor(client, notifier)

    first_imported = asyncio.run(monitor.sync_recent_items(db, [library]))

    assert first_imported == 1
    assert notifier.calls == []

    second_podcast = media_item_row(
        id="podcast-2",
        media={
            "metadata": {
                "title": "Darknet Diaries",
                "author": "Jack Rhysider",
            },
            "duration": 0,
        },
    )
    client.library_items["lib-podcast"] = items_payload(first_podcast, second_podcast, total=2)

    second_imported = asyncio.run(monitor.sync_recent_items(db, [library]))

    assert second_imported == 1
    assert notifier.calls == [
        (
            "new_podcast",
            "New podcast added",
            "Darknet Diaries by Jack Rhysider was added to Podcasts.",
        )
    ]
    db.close()
