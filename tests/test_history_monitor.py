import asyncio
from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from absulli.core.time import utcnow
from absulli.database.models import AbsUser, Base, Library, ListeningHistory, MediaItem, Setting
from absulli.monitors.history import HistoryMonitor, history_session_hash


class FakeClient:
    def __init__(
        self,
        *,
        users=None,
        libraries=None,
        collections=None,
        series=None,
        library_items=None,
        items=None,
        sessions=None,
        fail_sessions_for=None,
        fail_collections=False,
        fail_series_for=None,
    ):
        self.users = users if users is not None else {"users": []}
        self.libraries = libraries if libraries is not None else {"libraries": []}
        self.collections = collections if collections is not None else {"collections": []}
        self.series = series if series is not None else {}
        self.library_items = library_items if library_items is not None else {}
        self.items = items if items is not None else {}
        self.sessions = sessions if sessions is not None else {}
        self.fail_sessions_for = set(fail_sessions_for or [])
        self.fail_collections = fail_collections
        self.fail_series_for = set(fail_series_for or [])
        self.calls = {
            "get_users": 0,
            "get_libraries": 0,
            "get_collections": 0,
            "get_library_series": [],
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

    async def get_library_series(self, library_id, limit=1000, page=0):
        self.calls["get_library_series"].append((library_id, limit, page))
        if library_id in self.fail_series_for:
            raise RuntimeError("ABS series unavailable")
        library_series = self.series.get(library_id, {"results": [], "total": 0})
        if isinstance(library_series, dict) and page in library_series:
            return library_series[page]
        return library_series

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


def series_payload(*rows, total=None, limit=None, page=None):
    payload = {"results": list(rows)}
    if total is not None:
        payload["total"] = total
    if limit is not None:
        payload["limit"] = limit
    if page is not None:
        payload["page"] = page
    return payload


def series_row(series_id="series-1", name="Mitch Rapp", books=None):
    return {
        "id": series_id,
        "name": name,
        "books": list(books if books is not None else [{"id": "item-1"}]),
    }


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
                "seriesName": "Mitch Rapp",
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


def test_poll_preserves_archived_library_on_existing_history_without_cached_item():
    db = make_db()
    db.add_all(
        [
            Library(
                abs_library_id="lib-youtube",
                name="YouTube Podcasts",
                media_type="book",
                item_count=0,
                is_active=False,
                archived_at=utcnow(),
            ),
            ListeningHistory(
                abs_session_id="session-1",
                abs_user_id="user-1",
                username="Old User",
                abs_item_id="item-1",
                title="Old Title",
                media_type="book",
                library_id="lib-youtube",
                library_name="YouTube Podcasts",
            ),
        ]
    )
    db.commit()

    client = FakeClient(
        users=users_payload(user_row()),
        libraries=libraries_payload(),
        sessions={"user-1": sessions_payload(history_row(displayTitle="Updated Title"))},
    )
    monitor = HistoryMonitor(client)

    imported = asyncio.run(monitor.poll(db))

    history = db.query(ListeningHistory).filter_by(abs_session_id="session-1").one()
    assert imported == 0
    assert db.query(MediaItem).count() == 0
    assert history.title == "Updated Title"
    assert history.library_id == "lib-youtube"
    assert history.library_name == "YouTube Podcasts"
    db.close()


def test_poll_skips_and_removes_permanently_deleted_history_sessions():
    db = make_db()
    db.add_all(
        [
            ListeningHistory(
                abs_session_id="session-1",
                abs_user_id="user-1",
                username="Old User",
                abs_item_id="item-1",
                title="Deleted Title",
            ),
            Setting(
                key="deleted_history_session_hashes",
                value=f'["{history_session_hash("session-1")}"]',
                updated_at=utcnow(),
            ),
        ]
    )
    db.commit()

    client = FakeClient(
        users=users_payload(user_row()),
        libraries=libraries_payload(),
        sessions={"user-1": sessions_payload(history_row())},
    )
    monitor = HistoryMonitor(client)

    imported = asyncio.run(monitor.poll(db))

    assert imported == 0
    assert db.query(ListeningHistory).count() == 0
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
    assert db.query(Library).filter_by(abs_library_id="lib-1").one().name == "Updated Library"
    assert db.query(Library).filter_by(abs_library_id="lib-2").one().media_type == "podcast"
    db.close()


def test_sync_libraries_archives_missing_library_and_reactivates_it():
    db = make_db()
    library = Library(abs_library_id="lib-1", name="YouTube Podcasts", media_type="book")
    db.add(library)
    db.commit()

    client = FakeClient(libraries=libraries_payload())
    monitor = HistoryMonitor(client)

    saved = asyncio.run(monitor.sync_libraries(db))

    db.refresh(library)
    assert saved == []
    assert library.is_active is False
    assert library.archived_at is not None
    last_seen_at = library.updated_at

    client.libraries = libraries_payload(
        library_row(id="lib-1", name="YouTube Podcasts", numItems=53)
    )
    restored = asyncio.run(monitor.sync_libraries(db))

    db.refresh(library)
    assert restored == [library]
    assert library.is_active is True
    assert library.archived_at is None
    assert library.updated_at >= last_seen_at
    db.close()


def test_sync_libraries_does_not_archive_on_failure_or_invalid_payload():
    db = make_db()
    library = Library(abs_library_id="lib-1", name="Audiobooks", media_type="book")
    db.add(library)
    db.commit()

    class FailingClient(FakeClient):
        async def get_libraries(self):
            raise RuntimeError("ABS unavailable")

    assert asyncio.run(HistoryMonitor(FailingClient()).sync_libraries(db)) == []
    db.refresh(library)
    assert library.is_active is True

    client = FakeClient(libraries={"unexpected": []})
    assert asyncio.run(HistoryMonitor(client).sync_libraries(db)) == []
    db.refresh(library)
    assert library.is_active is True
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


def test_new_series_notifications_start_after_silent_library_baseline():
    db = make_db()
    library = Library(
        abs_library_id="lib-1",
        name="Audiobooks",
        media_type="book",
        item_count=1,
    )
    db.add(library)
    db.commit()

    first_book = media_item_row()
    client = FakeClient(
        library_items={"lib-1": items_payload(first_book, total=1)},
        series={"lib-1": series_payload(series_row(), total=1)},
    )
    notifier = FakeNotifier()
    monitor = HistoryMonitor(client, notifier)

    asyncio.run(monitor.sync_recent_items(db, [library]))
    baseline_key = monitor._new_series_baseline_key("lib-1")
    assert db.query(Setting).filter_by(key=baseline_key).first() is None
    asyncio.run(monitor.sync_series(db, [library]))

    assert notifier.calls == []
    baseline = db.query(Setting).filter_by(key=baseline_key).one()
    assert baseline.value == '["id:series-1"]'

    second_book = media_item_row(
        id="item-2",
        media={
            "metadata": {
                "title": "The Bourne Identity",
                "authors": [{"id": "author-2", "name": "Robert Ludlum"}],
                "seriesName": "Jason Bourne",
            },
            "duration": 3600,
        },
    )
    client.library_items["lib-1"] = items_payload(first_book, second_book, total=2)
    client.series["lib-1"] = series_payload(
        series_row(),
        series_row("series-2", "Jason Bourne", [{"id": "item-2"}]),
        total=2,
    )

    asyncio.run(monitor.sync_recent_items(db, [library]))
    asyncio.run(monitor.sync_series(db, [library]))

    assert notifier.calls == [
        (
            "new_book",
            "New book added",
            "The Bourne Identity by Robert Ludlum was added to Audiobooks.",
            "lib-1",
        ),
        (
            "new_series",
            "New series added",
            "Jason Bourne was added to Audiobooks.",
            "lib-1",
        ),
    ]
    assert notifier.contexts[1]["series"] == "Jason Bourne"
    assert notifier.contexts[1]["item_id"] == "item-2"
    assert notifier.contexts[1]["book_count"] == 1
    db.close()


def test_new_series_notification_detects_series_added_to_existing_book():
    db = make_db()
    library = Library(
        abs_library_id="lib-1",
        name="Audiobooks",
        media_type="book",
        item_count=1,
    )
    db.add(library)
    db.commit()

    book = media_item_row(
        media={
            "metadata": {
                "title": "The Bourne Identity",
                "authors": [{"id": "author-2", "name": "Robert Ludlum"}],
            },
            "duration": 3600,
        },
    )
    client = FakeClient(
        library_items={"lib-1": items_payload(book, total=1)},
        series={"lib-1": series_payload(total=0)},
    )
    notifier = FakeNotifier()
    monitor = HistoryMonitor(client, notifier)

    asyncio.run(monitor.sync_recent_items(db, [library]))
    asyncio.run(monitor.sync_series(db, [library]))
    book["media"]["metadata"]["seriesName"] = "Jason Bourne"
    client.series["lib-1"] = series_payload(
        series_row("series-2", "Jason Bourne", [{"id": "item-1"}]),
        total=1,
    )
    asyncio.run(monitor.sync_recent_items(db, [library]))
    asyncio.run(monitor.sync_series(db, [library]))

    assert notifier.calls == [
        (
            "new_series",
            "New series added",
            "Jason Bourne was added to Audiobooks.",
            "lib-1",
        )
    ]
    assert db.query(MediaItem).filter_by(abs_item_id="item-1").one().series == "Jason Bourne"
    db.close()


def test_new_series_notification_deduplicates_series_names():
    db = make_db()
    library = Library(
        abs_library_id="lib-1",
        name="Audiobooks",
        media_type="book",
        item_count=0,
    )
    db.add(library)
    db.commit()

    client = FakeClient(
        library_items={"lib-1": items_payload(total=0)},
        series={"lib-1": series_payload(total=0)},
    )
    notifier = FakeNotifier()
    monitor = HistoryMonitor(client, notifier)
    asyncio.run(monitor.sync_recent_items(db, [library]))
    asyncio.run(monitor.sync_series(db, [library]))

    first_book = media_item_row()
    second_book = media_item_row(id="item-2")
    client.library_items["lib-1"] = items_payload(first_book, second_book, total=2)
    client.series["lib-1"] = series_payload(
        series_row(books=[{"id": "item-1"}, {"id": "item-2"}]),
        total=1,
    )
    asyncio.run(monitor.sync_recent_items(db, [library]))
    asyncio.run(monitor.sync_series(db, [library]))

    assert [call[0] for call in notifier.calls].count("new_series") == 1
    db.close()


def test_book_added_to_series_notifies_after_silent_membership_baseline():
    db = make_db()
    library = Library(
        abs_library_id="lib-1",
        name="Audiobooks",
        media_type="book",
        item_count=1,
    )
    db.add(library)
    db.commit()

    first_book = media_item_row()
    client = FakeClient(
        library_items={"lib-1": items_payload(first_book, total=1)},
        series={"lib-1": series_payload(series_row(), total=1)},
    )
    notifier = FakeNotifier()
    monitor = HistoryMonitor(client, notifier)

    asyncio.run(monitor.sync_recent_items(db, [library]))
    asyncio.run(monitor.sync_series(db, [library]))

    assert notifier.calls == []
    membership_key = monitor._series_membership_baseline_key("lib-1")
    membership = db.query(Setting).filter_by(key=membership_key).one()
    assert membership.value == '{"id:series-1": ["item-1"]}'

    second_book = media_item_row(
        id="item-2",
        media={
            "metadata": {
                "title": "American Assassin",
                "authors": [{"id": "author-1", "name": "Vince Flynn"}],
                "seriesName": "Mitch Rapp",
            },
            "duration": 4200,
        },
    )
    client.library_items["lib-1"] = items_payload(first_book, second_book, total=2)
    client.series["lib-1"] = series_payload(
        series_row(books=[{"id": "item-1"}, {"id": "item-2"}]),
        total=1,
    )

    asyncio.run(monitor.sync_recent_items(db, [library]))
    asyncio.run(monitor.sync_series(db, [library]))

    assert notifier.calls == [
        (
            "new_book",
            "New book added",
            "American Assassin by Vince Flynn was added to Audiobooks.",
            "lib-1",
        ),
        (
            "book_added_to_series",
            "Book added to series",
            "American Assassin by Vince Flynn was added to Mitch Rapp in Audiobooks.",
            "lib-1",
        ),
    ]
    assert notifier.contexts[1]["series"] == "Mitch Rapp"
    assert notifier.contexts[1]["item_id"] == "item-2"
    assert notifier.contexts[1]["book_count"] == 2
    db.close()


def test_existing_series_gets_silent_membership_baseline_after_upgrade():
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
        series={"lib-1": series_payload(series_row(), total=1)},
    )
    notifier = FakeNotifier()
    monitor = HistoryMonitor(client, notifier)
    db.add(
        Setting(
            key=monitor._new_series_baseline_key("lib-1"),
            value='["mitch rapp"]',
        )
    )
    db.commit()

    asyncio.run(monitor.sync_recent_items(db, [library]))
    asyncio.run(monitor.sync_series(db, [library]))

    assert notifier.calls == []
    membership_key = monitor._series_membership_baseline_key("lib-1")
    membership = db.query(Setting).filter_by(key=membership_key).one()
    assert membership.value == '{"id:series-1": ["item-1"]}'
    baseline_key = monitor._new_series_baseline_key("lib-1")
    baseline = db.query(Setting).filter_by(key=baseline_key).one()
    assert baseline.value == '["id:series-1"]'
    db.close()


def test_legacy_empty_series_baselines_are_ignored_on_endpoint_upgrade():
    db = make_db()
    library = Library(
        abs_library_id="lib-1",
        name="Audiobooks",
        media_type="book",
        item_count=2,
    )
    db.add_all(
        [
            library,
            Setting(
                key="notify_new_series_baseline_b8cc5e5f9fb66a2df366a336",
                value="[]",
            ),
            Setting(
                key="notify_series_membership_baseline_b8cc5e5f9fb66a2df366a336",
                value="{}",
            ),
        ]
    )
    db.commit()

    client = FakeClient(
        series={
            "lib-1": series_payload(
                series_row(),
                series_row("series-2", "Jason Bourne", [{"id": "item-2"}]),
                total=2,
            )
        }
    )
    notifier = FakeNotifier()
    monitor = HistoryMonitor(client, notifier)

    detected = asyncio.run(monitor.sync_series(db, [library]))

    assert detected == 0
    assert notifier.calls == []
    assert monitor._new_series_baseline(db, "lib-1") == {
        "id:series-1",
        "id:series-2",
    }
    assert monitor._series_membership_baseline(db, "lib-1") == {
        "id:series-1": {"item-1"},
        "id:series-2": {"item-2"},
    }
    db.close()


def test_existing_name_membership_baseline_migrates_to_series_id():
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
        series={"lib-1": series_payload(series_row(), total=1)},
    )
    notifier = FakeNotifier()
    monitor = HistoryMonitor(client, notifier)
    db.add_all(
        [
            Setting(
                key=monitor._new_series_baseline_key("lib-1"),
                value='["mitch rapp"]',
            ),
            Setting(
                key=monitor._series_membership_baseline_key("lib-1"),
                value='{"mitch rapp": ["item-1"]}',
            ),
        ]
    )
    db.commit()

    asyncio.run(monitor.sync_recent_items(db, [library]))
    asyncio.run(monitor.sync_series(db, [library]))

    assert notifier.calls == []
    assert monitor._new_series_baseline(db, "lib-1") == {"id:series-1"}
    assert monitor._series_membership_baseline(db, "lib-1") == {
        "id:series-1": {"item-1"}
    }
    db.close()


def test_series_rename_with_stable_id_does_not_notify():
    db = make_db()
    library = Library(
        abs_library_id="lib-1",
        name="Audiobooks",
        media_type="book",
        item_count=1,
    )
    db.add(library)
    db.commit()

    book = media_item_row()
    client = FakeClient(
        library_items={"lib-1": items_payload(book, total=1)},
        series={"lib-1": series_payload(series_row(), total=1)},
    )
    notifier = FakeNotifier()
    monitor = HistoryMonitor(client, notifier)

    asyncio.run(monitor.sync_recent_items(db, [library]))
    asyncio.run(monitor.sync_series(db, [library]))
    book["media"]["metadata"]["seriesName"] = "Mitch Rapp Universe"
    client.series["lib-1"] = series_payload(
        series_row("series-1", "Mitch Rapp Universe"),
        total=1,
    )
    asyncio.run(monitor.sync_recent_items(db, [library]))
    asyncio.run(monitor.sync_series(db, [library]))

    assert notifier.calls == []
    assert monitor._new_series_baseline(db, "lib-1") == {"id:series-1"}
    db.close()


def test_series_name_fallback_is_used_without_series_id():
    db = make_db()
    library = Library(
        abs_library_id="lib-1",
        name="Audiobooks",
        media_type="book",
        item_count=1,
    )
    db.add(library)
    db.commit()

    book = media_item_row()
    client = FakeClient(
        library_items={"lib-1": items_payload(book, total=1)},
        series={"lib-1": series_payload(series_row("", "Mitch Rapp"), total=1)},
    )
    notifier = FakeNotifier()
    monitor = HistoryMonitor(client, notifier)

    asyncio.run(monitor.sync_recent_items(db, [library]))
    asyncio.run(monitor.sync_series(db, [library]))

    assert notifier.calls == []
    assert monitor._new_series_baseline(db, "lib-1") == {"name:mitch rapp"}
    assert monitor._series_membership_baseline(db, "lib-1") == {
        "name:mitch rapp": {"item-1"}
    }
    db.close()


def test_series_name_fallback_migrates_when_id_appears():
    db = make_db()
    library = Library(
        abs_library_id="lib-1",
        name="Audiobooks",
        media_type="book",
        item_count=1,
    )
    db.add(library)
    db.commit()

    book = media_item_row()
    client = FakeClient(
        library_items={"lib-1": items_payload(book, total=1)},
        series={"lib-1": series_payload(series_row("", "Mitch Rapp"), total=1)},
    )
    notifier = FakeNotifier()
    monitor = HistoryMonitor(client, notifier)

    asyncio.run(monitor.sync_recent_items(db, [library]))
    asyncio.run(monitor.sync_series(db, [library]))
    client.series["lib-1"] = series_payload(series_row(), total=1)
    asyncio.run(monitor.sync_series(db, [library]))

    assert notifier.calls == []
    assert monitor._new_series_baseline(db, "lib-1") == {"id:series-1"}
    assert monitor._series_membership_baseline(db, "lib-1") == {
        "id:series-1": {"item-1"}
    }
    db.close()


def test_book_added_to_series_detects_metadata_added_to_existing_book():
    db = make_db()
    library = Library(
        abs_library_id="lib-1",
        name="Audiobooks",
        media_type="book",
        item_count=2,
    )
    db.add(library)
    db.commit()

    first_book = media_item_row()
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
    client = FakeClient(
        library_items={"lib-1": items_payload(first_book, second_book, total=2)},
        series={"lib-1": series_payload(series_row(), total=1)},
    )
    notifier = FakeNotifier()
    monitor = HistoryMonitor(client, notifier)

    asyncio.run(monitor.sync_recent_items(db, [library]))
    asyncio.run(monitor.sync_series(db, [library]))
    second_book["media"]["metadata"]["seriesName"] = "Mitch Rapp"
    client.series["lib-1"] = series_payload(
        series_row(books=[{"id": "item-1"}, {"id": "item-2"}]),
        total=1,
    )
    asyncio.run(monitor.sync_recent_items(db, [library]))
    asyncio.run(monitor.sync_series(db, [library]))

    assert notifier.calls == [
        (
            "book_added_to_series",
            "Book added to series",
            "American Assassin by Vince Flynn was added to Mitch Rapp in Audiobooks.",
            "lib-1",
        )
    ]
    assert notifier.contexts[0]["book_count"] == 2
    db.close()


def test_book_removed_then_readded_to_series_notifies_again():
    db = make_db()
    library = Library(
        abs_library_id="lib-1",
        name="Audiobooks",
        media_type="book",
        item_count=2,
    )
    db.add(library)
    db.commit()

    first_book = media_item_row()
    second_book = media_item_row(id="item-2")
    client = FakeClient(
        library_items={"lib-1": items_payload(first_book, second_book, total=2)},
        series={
            "lib-1": series_payload(
                series_row(books=[{"id": "item-1"}, {"id": "item-2"}]),
                total=1,
            )
        },
    )
    notifier = FakeNotifier()
    monitor = HistoryMonitor(client, notifier)

    asyncio.run(monitor.sync_recent_items(db, [library]))
    asyncio.run(monitor.sync_series(db, [library]))
    client.series["lib-1"] = series_payload(
        series_row(books=[{"id": "item-1"}]),
        total=1,
    )
    asyncio.run(monitor.sync_series(db, [library]))
    client.series["lib-1"] = series_payload(
        series_row(books=[{"id": "item-1"}, {"id": "item-2"}]),
        total=1,
    )
    asyncio.run(monitor.sync_series(db, [library]))

    assert [call[0] for call in notifier.calls] == ["book_added_to_series"]
    db.close()


def test_series_sync_loads_all_endpoint_pages():
    db = make_db()
    library = Library(
        abs_library_id="lib-1",
        name="Audiobooks",
        media_type="book",
        item_count=2,
    )
    db.add(library)
    db.commit()

    client = FakeClient(
        series={
            "lib-1": {
                0: series_payload(
                    series_row(),
                    total=2,
                    limit=1,
                    page=0,
                ),
                1: series_payload(
                    series_row("series-2", "Jason Bourne", [{"id": "item-2"}]),
                    total=2,
                    limit=1,
                    page=1,
                ),
            }
        }
    )
    monitor = HistoryMonitor(client)

    detected = asyncio.run(monitor.sync_series(db, [library]))

    assert detected == 0
    assert client.calls["get_library_series"] == [
        ("lib-1", 1000, 0),
        ("lib-1", 1000, 1),
    ]
    assert monitor._new_series_baseline(db, "lib-1") == {
        "id:series-1",
        "id:series-2",
    }
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


def test_series_failure_does_not_stop_history_poll():
    db = make_db()
    client = FakeClient(
        users=users_payload(user_row()),
        libraries=libraries_payload(library_row()),
        library_items={"lib-1": items_payload(media_item_row(), total=1)},
        sessions={"user-1": sessions_payload(history_row())},
        fail_series_for={"lib-1"},
    )
    monitor = HistoryMonitor(client)

    imported = asyncio.run(monitor.poll(db))

    assert imported == 1
    assert db.query(ListeningHistory).count() == 1
    assert client.calls["get_library_series"] == [("lib-1", 1000, 0)]
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
