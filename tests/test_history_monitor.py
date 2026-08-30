import asyncio
from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from absulli.core.time import utcnow
from absulli.database.models import AbsUser, Base, Library, ListeningHistory, MediaItem, Setting
from absulli.monitors.history import HistoryMonitor


class FakeClient:
    def __init__(
        self,
        *,
        users=None,
        libraries=None,
        collections=None,
        library_items=None,
        items=None,
        sessions=None,
        fail_sessions_for=None,
        fail_collections=False,
    ):
        self.users = users if users is not None else {"users": []}
        self.libraries = libraries if libraries is not None else {"libraries": []}
        self.collections = collections if collections is not None else {"collections": []}
        self.library_items = library_items if library_items is not None else {}
        self.items = items if items is not None else {}
        self.sessions = sessions if sessions is not None else {}
        self.fail_sessions_for = set(fail_sessions_for or [])
        self.fail_collections = fail_collections
        self.calls = {
            "get_users": 0,
            "get_libraries": 0,
            "get_collections": 0,
            "get_library_items": [],
            "get_item": [],
            "get_user_listening_sessions": [],
        }

    async def get_users(self):
        self.calls["get_users"] += 1
        return self.users

    async def get_libraries(self):
        self.calls["get_libraries"] += 1
        return self.libraries

    async def get_collections(self):
        self.calls["get_collections"] += 1
        if self.fail_collections:
            raise RuntimeError("ABS collections unavailable")
        return self.collections

    async def get_library_items(self, library_id, limit=5000):
        self.calls["get_library_items"].append((library_id, limit))
        return self.library_items.get(library_id, {"results": [], "total": 0})

    async def get_item(self, item_id, expanded=False):
        self.calls["get_item"].append((item_id, expanded))
        return self.items.get(item_id, {})

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
        self.contexts = []

    async def notify(self, db, event_type, title, body, library_id="", context=None):
        self.calls.append((event_type, title, body, library_id))
        self.contexts.append(context or {})


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
            "lib-1",
        )
    ]
    assert notifier.contexts[0]["description"] == ""
    db.close()


def test_new_collection_notifications_start_after_silent_baseline():
    db = make_db()
    library = Library(
        abs_library_id="lib-1",
        name="Audiobooks",
        media_type="book",
        item_count=1,
    )
    db.add(library)
    db.commit()

    existing_collection = {
        "id": "collection-1",
        "libraryId": "lib-1",
        "name": "Existing Collection",
        "description": "Existing description",
        "books": [{"id": "item-1"}],
    }
    client = FakeClient(collections={"collections": [existing_collection]})
    notifier = FakeNotifier()
    monitor = HistoryMonitor(client, notifier)

    first_detected = asyncio.run(monitor.sync_collections(db, [library]))

    assert first_detected == 0
    assert notifier.calls == []
    baseline = db.query(Setting).filter_by(key="notify_new_collection_baseline").one()
    assert baseline.value == '["collection-1"]'
    membership = db.query(Setting).filter_by(key="notify_collection_membership_baseline").one()
    assert membership.value == '{"collection-1": ["item-1"]}'

    new_collection = {
        "id": "collection-2",
        "libraryId": "lib-1",
        "name": "Mitch Rapp",
        "description": "<b>Reading order</b>",
        "books": [{"id": "item-1"}, {"id": "item-2"}],
    }
    client.collections = {"collections": [existing_collection, new_collection]}

    second_detected = asyncio.run(monitor.sync_collections(db, [library]))

    assert second_detected == 1
    assert notifier.calls == [
        (
            "new_collection",
            "New collection added",
            "Mitch Rapp was added to Audiobooks.",
            "lib-1",
        )
    ]
    assert notifier.contexts == [
        {
            "collection_id": "collection-2",
            "collection": "Mitch Rapp",
            "collection_description": "Reading order",
            "book_count": 2,
            "library_name": "Audiobooks",
            "media_type": "collection",
        }
    ]
    db.close()


def test_book_added_to_collection_notifies_after_silent_membership_baseline():
    db = make_db()
    library = Library(
        abs_library_id="lib-1",
        name="Audiobooks",
        media_type="book",
        item_count=2,
    )
    db.add(library)
    db.commit()

    first_book = media_item_row(id="item-1")
    collection = {
        "id": "collection-1",
        "libraryId": "lib-1",
        "name": "Mitch Rapp",
        "description": "<b>Reading order</b>",
        "books": [first_book],
    }
    client = FakeClient(collections={"collections": [collection]})
    notifier = FakeNotifier()
    monitor = HistoryMonitor(client, notifier)

    first_detected = asyncio.run(monitor.sync_collections(db, [library]))

    assert first_detected == 0
    assert notifier.calls == []

    second_book = media_item_row(
        id="item-2",
        media={
            "metadata": {
                "title": "American Assassin",
                "authors": [{"id": "author-1", "name": "Vince Flynn"}],
                "series": [{"name": "Mitch Rapp"}],
                "description": "<p>The beginning.</p>",
                "asin": "B002V5D2J6",
            },
            "duration": 4200,
        },
    )
    collection["books"] = [first_book, second_book]

    second_detected = asyncio.run(monitor.sync_collections(db, [library]))

    assert second_detected == 1
    assert notifier.calls == [
        (
            "book_added_to_collection",
            "Book added to collection",
            "American Assassin by Vince Flynn was added to Mitch Rapp in Audiobooks.",
            "lib-1",
        )
    ]
    assert notifier.contexts == [
        {
            "item_id": "item-2",
            "title": "American Assassin",
            "author": "Vince Flynn",
            "series": "Mitch Rapp",
            "narrator": "",
            "subtitle": "",
            "publisher": "",
            "description": "The beginning.",
            "isbn": "",
            "asin": "B002V5D2J6",
            "language": "",
            "year": "",
            "collection_id": "collection-1",
            "collection": "Mitch Rapp",
            "collection_description": "Reading order",
            "book_count": 2,
            "library_name": "Audiobooks",
            "media_type": "book",
        }
    ]
    membership = db.query(Setting).filter_by(key="notify_collection_membership_baseline").one()
    assert membership.value == '{"collection-1": ["item-1", "item-2"]}'
    db.close()


def test_existing_collections_get_silent_membership_baseline_after_upgrade():
    db = make_db()
    library = Library(abs_library_id="lib-1", name="Audiobooks", media_type="book")
    db.add_all(
        [
            library,
            Setting(
                key="notify_new_collection_baseline",
                value='["collection-1"]',
                updated_at=utcnow(),
            ),
        ]
    )
    db.commit()

    collection = {
        "id": "collection-1",
        "libraryId": "lib-1",
        "name": "Mitch Rapp",
        "books": [media_item_row(id="item-1")],
    }
    client = FakeClient(collections={"collections": [collection]})
    notifier = FakeNotifier()
    monitor = HistoryMonitor(client, notifier)

    first_detected = asyncio.run(monitor.sync_collections(db, [library]))

    assert first_detected == 0
    assert notifier.calls == []
    membership = db.query(Setting).filter_by(key="notify_collection_membership_baseline").one()
    assert membership.value == '{"collection-1": ["item-1"]}'

    collection["books"].append(media_item_row(id="item-2"))

    second_detected = asyncio.run(monitor.sync_collections(db, [library]))

    assert second_detected == 1
    assert notifier.calls[0][0] == "book_added_to_collection"
    assert notifier.contexts[0]["item_id"] == "item-2"
    db.close()


def test_book_removed_then_readded_to_collection_notifies_again():
    db = make_db()
    library = Library(abs_library_id="lib-1", name="Audiobooks", media_type="book")
    db.add(library)
    db.commit()

    book = media_item_row(id="item-1")
    collection = {
        "id": "collection-1",
        "libraryId": "lib-1",
        "name": "Favorites",
        "books": [book],
    }
    client = FakeClient(collections={"collections": [collection]})
    notifier = FakeNotifier()
    monitor = HistoryMonitor(client, notifier)

    asyncio.run(monitor.sync_collections(db, [library]))
    collection["books"] = []
    removed_detected = asyncio.run(monitor.sync_collections(db, [library]))
    collection["books"] = [book]
    readded_detected = asyncio.run(monitor.sync_collections(db, [library]))

    assert removed_detected == 0
    assert readded_detected == 1
    assert len(notifier.calls) == 1
    assert notifier.calls[0][0] == "book_added_to_collection"
    assert notifier.contexts[0]["item_id"] == "item-1"
    db.close()


def test_collection_book_ids_use_stored_media_metadata():
    db = make_db()
    library = Library(abs_library_id="lib-1", name="Audiobooks", media_type="book")
    db.add_all(
        [
            library,
            MediaItem(
                abs_item_id="item-2",
                library_id="lib-1",
                library_name="Audiobooks",
                media_type="book",
                title="American Assassin",
                author="Vince Flynn",
            ),
            Setting(
                key="notify_new_collection_baseline",
                value='["collection-1"]',
                updated_at=utcnow(),
            ),
            Setting(
                key="notify_collection_membership_baseline",
                value='{"collection-1": ["item-1"]}',
                updated_at=utcnow(),
            ),
        ]
    )
    db.commit()

    client = FakeClient(
        collections={
            "collections": [
                {
                    "id": "collection-1",
                    "libraryId": "lib-1",
                    "name": "Mitch Rapp",
                    "books": ["item-1", "item-2"],
                }
            ]
        }
    )
    notifier = FakeNotifier()
    monitor = HistoryMonitor(client, notifier)

    detected = asyncio.run(monitor.sync_collections(db, [library]))

    assert detected == 1
    assert notifier.calls == [
        (
            "book_added_to_collection",
            "Book added to collection",
            "American Assassin by Vince Flynn was added to Mitch Rapp in Audiobooks.",
            "lib-1",
        )
    ]
    assert notifier.contexts[0]["item_id"] == "item-2"
    db.close()


def test_collection_baseline_keeps_deleted_collection_ids():
    db = make_db()
    library = Library(abs_library_id="lib-1", name="Audiobooks", media_type="book")
    db.add_all(
        [
            library,
            Setting(
                key="notify_new_collection_baseline",
                value='["collection-1", "collection-2"]',
                updated_at=utcnow(),
            ),
        ]
    )
    db.commit()

    client = FakeClient(
        collections={
            "collections": [
                {
                    "id": "collection-2",
                    "libraryId": "lib-1",
                    "name": "Still Present",
                    "books": [],
                }
            ]
        }
    )
    notifier = FakeNotifier()
    monitor = HistoryMonitor(client, notifier)

    detected = asyncio.run(monitor.sync_collections(db, [library]))

    assert detected == 0
    assert notifier.calls == []
    baseline = db.query(Setting).filter_by(key="notify_new_collection_baseline").one()
    assert baseline.value == '["collection-1", "collection-2"]'
    db.close()


def test_collection_failure_does_not_stop_history_poll():
    db = make_db()
    client = FakeClient(
        users=users_payload(user_row()),
        libraries=libraries_payload(),
        sessions={"user-1": sessions_payload(history_row())},
        fail_collections=True,
    )
    monitor = HistoryMonitor(client)

    imported = asyncio.run(monitor.poll(db))

    assert imported == 1
    assert db.query(ListeningHistory).count() == 1
    assert client.calls["get_collections"] == 1
    db.close()


def test_new_book_notification_context_strips_description_html():
    db = make_db()
    notifier = FakeNotifier()
    monitor = HistoryMonitor(FakeClient(), notifier)

    asyncio.run(
        monitor._notify_new_books(
            db,
            [
                {
                    "abs_item_id": "item-1",
                    "title": "I Must Betray You",
                    "author": "Ruta Sepetys",
                    "description": "<b>#1 <i>New York Times</i></b><br /><br />A historical thriller.",
                    "library_id": "lib-1",
                    "library_name": "Audiobooks",
                    "media_type": "book",
                }
            ],
        )
    )

    assert notifier.contexts[0]["description"] == "#1 New York Times\n\nA historical thriller."
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
            "lib-podcast",
        )
    ]
    assert notifier.contexts[-1]["podcast"] == "Darknet Diaries"
    assert notifier.contexts[-1]["podcast_title"] == "Darknet Diaries"
    db.close()


def test_new_podcast_episode_notifications_start_after_silent_episode_baseline(monkeypatch):
    monkeypatch.setattr("absulli.monitors.history.event_enabled", lambda event_type: event_type == "new_podcast_episode")
    db = make_db()
    library = Library(
        abs_library_id="lib-podcast",
        name="Podcasts",
        media_type="podcast",
        item_count=1,
    )
    db.add(library)
    db.commit()

    podcast = media_item_row(
        id="podcast-1",
        media={
            "metadata": {
                "title": "The History of English Podcast",
                "author": "Kevin Stroud",
            },
            "duration": 0,
        },
    )
    client = FakeClient(
        library_items={"lib-podcast": items_payload(podcast, total=1)},
        items={
            "podcast-1": {
                "id": "podcast-1",
                "media": {
                    "metadata": {"title": "The History of English Podcast"},
                    "episodes": [
                        {"id": "episode-11", "title": "Episode 11: Germanic Ancestors"},
                    ],
                },
            }
        },
    )
    notifier = FakeNotifier()
    monitor = HistoryMonitor(client, notifier)

    first_imported = asyncio.run(monitor.sync_recent_items(db, [library]))

    assert first_imported == 1
    assert notifier.calls == []
    assert client.calls["get_item"] == [("podcast-1", True)]

    client.items["podcast-1"]["media"]["episodes"].append(
        {"id": "episode-12", "title": "Episode 12: Early Greek, Hittite and the Trojan War (Extended Version)"}
    )

    second_imported = asyncio.run(monitor.sync_recent_items(db, [library]))

    assert second_imported == 0
    assert notifier.calls == [
        (
            "new_podcast_episode",
            "New podcast episode added",
            "The History of English Podcast - Episode 12: Early Greek, Hittite and the Trojan War (Extended Version) was added to Podcasts.",
            "lib-podcast",
        )
    ]
    db.close()


def test_new_podcast_episode_notification_context_strips_description_html(monkeypatch):
    monkeypatch.setattr("absulli.monitors.history.event_enabled", lambda event_type: event_type == "new_podcast_episode")
    db = make_db()
    library = Library(abs_library_id="lib-podcast", name="Podcasts", media_type="podcast", item_count=1)
    db.add(library)
    db.commit()
    podcast = media_item_row(id="podcast-html", media={"metadata": {"title": "Example Show"}, "duration": 0})
    client = FakeClient(
        library_items={"lib-podcast": items_payload(podcast, total=1)},
        items={
            "podcast-html": {
                "media": {
                    "metadata": {"title": "Example Show", "description": "<p>Show<br />description</p>"},
                    "episodes": [{"id": "episode-1", "title": "First"}],
                }
            }
        },
    )
    notifier = FakeNotifier()
    monitor = HistoryMonitor(client, notifier)
    asyncio.run(monitor.sync_recent_items(db, [library]))
    client.items["podcast-html"]["media"]["episodes"].append(
        {"id": "episode-2", "title": "Second", "description": "Episode one<br /><br />Episode two"}
    )
    asyncio.run(monitor.sync_recent_items(db, [library]))
    context = notifier.contexts[-1]
    assert context["podcast_description"] == "Show\ndescription"
    assert context["episode_description"] == "Episode one\n\nEpisode two"
    db.close()


def test_new_podcast_episode_notifications_use_exact_episode_title(monkeypatch):
    monkeypatch.setattr("absulli.monitors.history.event_enabled", lambda event_type: event_type == "new_podcast_episode")
    db = make_db()
    library = Library(
        abs_library_id="lib-podcast",
        name="podcasts test",
        media_type="podcast",
        item_count=1,
    )
    db.add(library)
    db.commit()

    podcast = media_item_row(
        id="crime-junkie",
        media={"metadata": {"title": "Crime Junkie", "author": "Audiochuck"}, "duration": 0},
    )
    client = FakeClient(
        library_items={"lib-podcast": items_payload(podcast, total=1)},
        items={
            "crime-junkie": {
                "media": {
                    "metadata": {"title": "Crime Junkie"},
                    "episodes": [{"id": "episode-1", "title": "MURDERED: Joyce LePage Part 1"}],
                }
            }
        },
    )
    notifier = FakeNotifier()
    monitor = HistoryMonitor(client, notifier)

    asyncio.run(monitor.sync_recent_items(db, [library]))
    client.items["crime-junkie"]["media"]["episodes"].append(
        {"id": "episode-2", "title": "MURDERED: Joyce LePage Part 2"}
    )
    asyncio.run(monitor.sync_recent_items(db, [library]))

    assert notifier.calls == [
        (
            "new_podcast_episode",
            "New podcast episode added",
            "Crime Junkie - MURDERED: Joyce LePage Part 2 was added to podcasts test.",
            "lib-podcast",
        )
    ]
    db.close()


def test_podcast_episode_details_are_not_fetched_when_notification_is_disabled(monkeypatch):
    monkeypatch.setattr("absulli.monitors.history.event_enabled", lambda event_type: False)
    db = make_db()
    library = Library(
        abs_library_id="lib-podcast",
        name="Podcasts",
        media_type="podcast",
        item_count=1,
    )
    db.add(library)
    db.commit()

    podcast = media_item_row(
        id="podcast-1",
        media={"metadata": {"title": "Podcast", "author": "Author"}, "duration": 0},
    )
    client = FakeClient(library_items={"lib-podcast": items_payload(podcast, total=1)})
    monitor = HistoryMonitor(client, FakeNotifier())

    asyncio.run(monitor.sync_recent_items(db, [library]))

    assert client.calls["get_item"] == []
    db.close()


def test_disabling_podcast_episode_notifications_clears_existing_baseline(monkeypatch):
    state = {"enabled": True}
    monkeypatch.setattr("absulli.monitors.history.event_enabled", lambda event_type: state["enabled"])
    db = make_db()
    library = Library(
        abs_library_id="lib-podcast",
        name="Podcasts",
        media_type="podcast",
        item_count=1,
    )
    db.add(library)
    db.commit()

    podcast = media_item_row(
        id="podcast-1",
        media={"metadata": {"title": "Podcast", "author": "Author"}, "duration": 0},
    )
    client = FakeClient(
        library_items={"lib-podcast": items_payload(podcast, total=1)},
        items={
            "podcast-1": {
                "media": {
                    "metadata": {"title": "Podcast"},
                    "episodes": [{"id": "episode-1", "title": "Episode 1"}],
                }
            }
        },
    )
    monitor = HistoryMonitor(client, FakeNotifier())

    asyncio.run(monitor.sync_recent_items(db, [library]))
    assert monitor._podcast_episode_baseline(db, "podcast-1") == {"episode-1"}

    state["enabled"] = False
    asyncio.run(monitor.sync_recent_items(db, [library]))

    assert monitor._podcast_episode_baseline(db, "podcast-1") is None
    db.close()
