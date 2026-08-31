import hashlib
import json
import logging
import time
from collections.abc import Iterable

from sqlalchemy.orm import Session

from absulli.database.models import AbsUser, Library, ListeningHistory, MediaItem, Setting
from absulli.http.abs_client import AudiobookshelfClient
from absulli.monitors.utils import friendly_names
from absulli.notifiers.manager import event_enabled
from absulli.http.normalizers import (
    _plain_text,
    normalize_history_payload,
    normalize_library_payload,
    normalize_media_item_payload,
    normalize_user_payload,
    utcnow,
)

log = logging.getLogger(__name__)


class HistoryMonitor:
    def __init__(self, client: AudiobookshelfClient, notifier=None):
        self.client = client
        self.notifier = notifier

    def _new_book_baseline_key(self, library_id: str) -> str:
        digest = hashlib.sha256(library_id.encode("utf-8")).hexdigest()[:24]
        return f"notify_new_book_baseline_{digest}"

    def _new_book_baseline_complete(self, db: Session, library_id: str) -> bool:
        key = self._new_book_baseline_key(library_id)
        row = db.query(Setting).filter(Setting.key == key).first()
        return bool(row and str(row.value).strip().lower() == "true")

    def _mark_new_book_baseline_complete(self, db: Session, library_id: str) -> None:
        key = self._new_book_baseline_key(library_id)
        row = db.query(Setting).filter(Setting.key == key).first()
        if row:
            row.value = "true"
            row.updated_at = utcnow()
        else:
            db.add(Setting(key=key, value="true", updated_at=utcnow()))

    def _new_series_baseline_key(self, library_id: str) -> str:
        digest = hashlib.sha256(library_id.encode("utf-8")).hexdigest()[:24]
        return f"notify_new_series_baseline_v2_{digest}"

    def _new_series_baseline(self, db: Session, library_id: str) -> set[str] | None:
        key = self._new_series_baseline_key(library_id)
        row = db.query(Setting).filter(Setting.key == key).first()
        if not row:
            return None
        try:
            values = json.loads(row.value or "[]")
        except (TypeError, ValueError):
            return set()
        if not isinstance(values, list):
            return set()
        return {str(value) for value in values if value}

    def _store_new_series_baseline(self, db: Session, library_id: str, series_keys: set[str]) -> None:
        key = self._new_series_baseline_key(library_id)
        value = json.dumps(sorted(series_keys))
        row = db.query(Setting).filter(Setting.key == key).first()
        if row:
            row.value = value
            row.updated_at = utcnow()
        else:
            db.add(Setting(key=key, value=value, updated_at=utcnow()))

    def _series_membership_baseline_key(self, library_id: str) -> str:
        digest = hashlib.sha256(library_id.encode("utf-8")).hexdigest()[:24]
        return f"notify_series_membership_baseline_v2_{digest}"

    def _series_membership_baseline(
        self,
        db: Session,
        library_id: str,
    ) -> dict[str, set[str]] | None:
        key = self._series_membership_baseline_key(library_id)
        row = db.query(Setting).filter(Setting.key == key).first()
        if not row:
            return None
        try:
            values = json.loads(row.value or "{}")
        except (TypeError, ValueError):
            return {}
        if not isinstance(values, dict):
            return {}
        return {
            str(series_key): {str(item_id) for item_id in item_ids if item_id}
            for series_key, item_ids in values.items()
            if series_key and isinstance(item_ids, list)
        }

    def _store_series_membership_baseline(
        self,
        db: Session,
        library_id: str,
        membership: dict[str, set[str]],
    ) -> None:
        key = self._series_membership_baseline_key(library_id)
        value = json.dumps(
            {
                series_key: sorted(item_ids)
                for series_key, item_ids in sorted(membership.items())
            }
        )
        row = db.query(Setting).filter(Setting.key == key).first()
        if row:
            row.value = value
            row.updated_at = utcnow()
        else:
            db.add(Setting(key=key, value=value, updated_at=utcnow()))

    def _series_key(self, item: dict) -> str:
        series_id = str(item.get("series_id") or "").strip()
        if series_id:
            return f"id:{series_id}"
        series_name = str(item.get("series") or "").strip()
        return f"name:{series_name.casefold()}" if series_name else ""

    def _legacy_series_key(self, item: dict) -> str:
        return str(item.get("series") or "").strip().casefold()

    def _migrate_series_baselines(
        self,
        current_series: dict[str, dict],
        baseline: set[str] | None,
        membership: dict[str, set[str]] | None,
    ) -> tuple[set[str] | None, dict[str, set[str]] | None]:
        if baseline is None:
            return None, membership
        migrated_baseline = set(baseline)
        migrated_membership = dict(membership) if membership is not None else None
        for series_key, item in current_series.items():
            legacy_key = self._legacy_series_key(item)
            legacy_keys = {legacy_key, f"name:{legacy_key}"} - {series_key}
            matched_keys = legacy_keys & migrated_baseline
            if not legacy_key or not matched_keys:
                continue
            migrated_baseline.difference_update(matched_keys)
            migrated_baseline.add(series_key)
            if migrated_membership is not None:
                migrated_items = migrated_membership.get(series_key, set())
                for matched_key in matched_keys:
                    migrated_items |= migrated_membership.pop(matched_key, set())
                if migrated_items:
                    migrated_membership[series_key] = migrated_items
        return migrated_baseline, migrated_membership

    def _series_books(self, db: Session, series: dict) -> dict[str, dict]:
        raw_books = series.get("books") or series.get("items") or series.get("libraryItems")
        raw_books = raw_books if isinstance(raw_books, list) else []
        library_id = str(series.get("library_id") or "")
        library_name = str(series.get("library_name") or "")
        normalized = normalize_media_item_payload(raw_books, library_id, library_name)
        books = {
            str(book["abs_item_id"]): book
            for book in normalized
            if book.get("abs_item_id")
        }
        item_ids = {
            str(book.get("id") or book.get("libraryItemId") or "").strip()
            if isinstance(book, dict)
            else str(book or "").strip()
            for book in raw_books
        }
        item_ids.discard("")
        if item_ids:
            stored_books = db.query(MediaItem).filter(MediaItem.abs_item_id.in_(item_ids)).all()
            for stored in stored_books:
                stored_values = {
                    "abs_item_id": stored.abs_item_id,
                    "library_id": stored.library_id,
                    "library_name": stored.library_name,
                    "media_type": stored.media_type,
                    "title": stored.title,
                    "author": stored.author,
                    "series": stored.series,
                    "narrator": stored.narrator,
                    "year": stored.year,
                }
                if stored.abs_item_id not in books:
                    books[stored.abs_item_id] = stored_values
                    continue
                book = books[stored.abs_item_id]
                for field, value in stored_values.items():
                    if not book.get(field) or book.get(field) in {"Unknown", "unknown"}:
                        book[field] = value
        for item_id in item_ids - books.keys():
            books[item_id] = {
                "abs_item_id": item_id,
                "library_id": library_id,
                "library_name": library_name,
                "media_type": "book",
                "title": "Unknown",
                "author": "",
            }
        series_name = str(series.get("series") or "")
        series_id = str(series.get("series_id") or "")
        for book in books.values():
            book["library_id"] = library_id
            book["library_name"] = library_name
            book["media_type"] = book.get("media_type") or "book"
            book["series"] = series_name
            book["series_id"] = series_id
        return books

    def _collection_baseline(self, db: Session) -> set[str] | None:
        row = db.query(Setting).filter(Setting.key == "notify_new_collection_baseline").first()
        if not row:
            return None
        try:
            values = json.loads(row.value or "[]")
        except (TypeError, ValueError):
            return set()
        if not isinstance(values, list):
            return set()
        return {str(value) for value in values if value}

    def _store_collection_baseline(self, db: Session, collection_ids: set[str]) -> None:
        key = "notify_new_collection_baseline"
        value = json.dumps(sorted(collection_ids))
        row = db.query(Setting).filter(Setting.key == key).first()
        if row:
            row.value = value
            row.updated_at = utcnow()
        else:
            db.add(Setting(key=key, value=value, updated_at=utcnow()))

    def _collection_membership_baseline(self, db: Session) -> dict[str, set[str]] | None:
        row = db.query(Setting).filter(Setting.key == "notify_collection_membership_baseline").first()
        if not row:
            return None
        try:
            values = json.loads(row.value or "{}")
        except (TypeError, ValueError):
            return {}
        if not isinstance(values, dict):
            return {}
        return {
            str(collection_id): {str(item_id) for item_id in item_ids if item_id}
            for collection_id, item_ids in values.items()
            if collection_id and isinstance(item_ids, list)
        }

    def _store_collection_membership_baseline(self, db: Session, membership: dict[str, set[str]]) -> None:
        key = "notify_collection_membership_baseline"
        value = json.dumps(
            {
                collection_id: sorted(item_ids)
                for collection_id, item_ids in sorted(membership.items())
            }
        )
        row = db.query(Setting).filter(Setting.key == key).first()
        if row:
            row.value = value
            row.updated_at = utcnow()
        else:
            db.add(Setting(key=key, value=value, updated_at=utcnow()))

    def _collection_books(self, db: Session, collection: dict) -> dict[str, dict]:
        raw_books = collection.get("books")
        raw_books = raw_books if isinstance(raw_books, list) else []
        normalized = normalize_media_item_payload(
            raw_books,
            str(collection.get("library_id") or ""),
            str(collection.get("library_name") or ""),
        )
        books = {str(book["abs_item_id"]): book for book in normalized if book.get("abs_item_id")}
        item_ids = {
            str(book.get("id") or book.get("libraryItemId") or "").strip()
            if isinstance(book, dict)
            else str(book or "").strip()
            for book in raw_books
        }
        item_ids.discard("")
        missing_ids = item_ids - books.keys()
        if missing_ids:
            stored_books = db.query(MediaItem).filter(MediaItem.abs_item_id.in_(missing_ids)).all()
            for stored in stored_books:
                books[stored.abs_item_id] = {
                    "abs_item_id": stored.abs_item_id,
                    "library_id": stored.library_id,
                    "library_name": stored.library_name,
                    "media_type": stored.media_type,
                    "title": stored.title,
                    "author": stored.author,
                    "series": stored.series,
                    "narrator": stored.narrator,
                    "year": stored.year,
                }
        for item_id in missing_ids - books.keys():
            books[item_id] = {
                "abs_item_id": item_id,
                "library_id": collection.get("library_id") or "",
                "library_name": collection.get("library_name") or "",
                "media_type": "book",
                "title": "Unknown",
                "author": "",
            }
        return books

    async def _notify_new_collections(self, db: Session, collections: list[dict]) -> None:
        if not self.notifier:
            return
        for collection in collections:
            name = str(collection.get("name") or "Unknown collection")
            library_name = str(collection.get("library_name") or "Audiobookshelf")
            body = f"{name} was added to {library_name}."
            books = collection.get("books")
            book_count = len(books) if isinstance(books, list) else 0
            try:
                await self.notifier.notify(
                    db,
                    "new_collection",
                    "New collection added",
                    body,
                    library_id=str(collection.get("library_id") or ""),
                    context={
                        "collection_id": collection.get("collection_id") or "",
                        "collection": name,
                        "collection_description": _plain_text(collection.get("description") or ""),
                        "book_count": book_count,
                        "library_name": library_name,
                        "media_type": "collection",
                    },
                )
            except Exception as exc:
                log.warning("New collection notification failed for %s: %s", name, exc)

    async def _notify_books_added_to_collections(self, db: Session, additions: list[dict]) -> None:
        if not self.notifier:
            return
        for addition in additions:
            book = addition["book"]
            title = str(book.get("title") or "Unknown")
            author = str(book.get("author") or "Unknown author")
            collection_name = str(addition.get("collection") or "Unknown collection")
            library_name = str(addition.get("library_name") or "Audiobookshelf")
            body = f"{title} by {author} was added to {collection_name} in {library_name}."
            try:
                await self.notifier.notify(
                    db,
                    "book_added_to_collection",
                    "Book added to collection",
                    body,
                    library_id=str(addition.get("library_id") or ""),
                    context={
                        "item_id": book.get("abs_item_id") or "",
                        "title": title,
                        "author": author,
                        "series": book.get("series") or "",
                        "narrator": book.get("narrator") or "",
                        "subtitle": book.get("subtitle") or "",
                        "publisher": book.get("publisher") or "",
                        "description": _plain_text(book.get("description") or ""),
                        "isbn": book.get("isbn") or "",
                        "asin": book.get("asin") or "",
                        "language": book.get("language") or "",
                        "year": book.get("year") or "",
                        "collection_id": addition.get("collection_id") or "",
                        "collection": collection_name,
                        "collection_description": _plain_text(addition.get("collection_description") or ""),
                        "book_count": addition.get("book_count") or 0,
                        "library_name": library_name,
                        "media_type": book.get("media_type") or "book",
                    },
                )
            except Exception as exc:
                log.warning("Collection book notification failed for %s: %s", title, exc)

    async def sync_collections(self, db: Session, libraries: list[Library]) -> int:
        try:
            payload = await self.client.get_collections()
        except Exception as exc:
            log.warning("Collection sync failed: %s", exc)
            return 0
        if isinstance(payload, list):
            rows = payload
        elif isinstance(payload, dict):
            rows = payload.get("collections") or payload.get("results") or payload.get("items") or []
        else:
            rows = []

        library_names = {library.abs_library_id: library.name for library in libraries}
        collections = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            collection_id = str(row.get("id") or "").strip()
            if not collection_id:
                continue
            library_id = str(row.get("libraryId") or row.get("library_id") or "").strip()
            collection = dict(row)
            collection["collection_id"] = collection_id
            collection["library_id"] = library_id
            collection["library_name"] = library_names.get(library_id, "Audiobookshelf")
            collection["normalized_books"] = self._collection_books(db, collection)
            collections.append(collection)

        current_ids = {collection["collection_id"] for collection in collections}
        current_membership = {
            collection["collection_id"]: set(collection["normalized_books"])
            for collection in collections
        }
        baseline = self._collection_baseline(db)
        membership_baseline = self._collection_membership_baseline(db)
        if baseline is None:
            self._store_collection_baseline(db, current_ids)
            self._store_collection_membership_baseline(db, current_membership)
            db.commit()
            return 0

        new_collections = [collection for collection in collections if collection["collection_id"] not in baseline]
        additions = []
        if membership_baseline is not None:
            for collection in collections:
                collection_id = collection["collection_id"]
                if collection_id not in baseline or collection_id not in membership_baseline:
                    continue
                new_item_ids = current_membership[collection_id] - membership_baseline[collection_id]
                for item_id in sorted(new_item_ids):
                    additions.append(
                        {
                            "book": collection["normalized_books"][item_id],
                            "collection_id": collection_id,
                            "collection": collection.get("name") or "Unknown collection",
                            "collection_description": collection.get("description") or "",
                            "book_count": len(current_membership[collection_id]),
                            "library_id": collection.get("library_id") or "",
                            "library_name": collection.get("library_name") or "Audiobookshelf",
                        }
                    )

        stored_membership = dict(membership_baseline or {})
        stored_membership.update(current_membership)
        self._store_collection_baseline(db, baseline | current_ids)
        self._store_collection_membership_baseline(db, stored_membership)
        db.commit()
        await self._notify_new_collections(db, new_collections)
        await self._notify_books_added_to_collections(db, additions)
        return len(new_collections) + len(additions)

    async def _notify_new_books(self, db: Session, books: list[dict]) -> None:
        if not self.notifier:
            return
        for book in books:
            title = book.get("title") or "Unknown"
            author = book.get("author") or "Unknown author"
            library_name = book.get("library_name") or "Audiobookshelf"
            body = f"{title} by {author} was added to {library_name}."
            try:
                await self.notifier.notify(
                    db,
                    "new_book",
                    "New book added",
                    body,
                    library_id=str(book.get("library_id") or ""),
                    context={
                        "item_id": book.get("abs_item_id") or "",
                        "title": title,
                        "author": author,
                        "series": book.get("series") or "",
                        "narrator": book.get("narrator") or "",
                        "subtitle": book.get("subtitle") or "",
                        "publisher": book.get("publisher") or "",
                        "description": _plain_text(book.get("description") or ""),
                        "isbn": book.get("isbn") or "",
                        "asin": book.get("asin") or "",
                        "language": book.get("language") or "",
                        "year": book.get("year") or "",
                        "library_name": library_name,
                        "media_type": book.get("media_type") or "book",
                    },
                )
            except Exception as exc:
                log.warning("New book notification failed for %s: %s", title, exc)

    async def _notify_new_series(self, db: Session, items: list[dict]) -> None:
        if not self.notifier:
            return
        for item in items:
            series_name = str(item.get("series") or "Unknown series")
            library_name = str(item.get("library_name") or "Audiobookshelf")
            body = f"{series_name} was added to {library_name}."
            try:
                await self.notifier.notify(
                    db,
                    "new_series",
                    "New series added",
                    body,
                    library_id=str(item.get("library_id") or ""),
                    context={
                        "item_id": item.get("abs_item_id") or "",
                        "title": item.get("title") or "",
                        "author": item.get("author") or "",
                        "series": series_name,
                        "book_count": item.get("book_count") or 0,
                        "narrator": item.get("narrator") or "",
                        "subtitle": item.get("subtitle") or "",
                        "publisher": item.get("publisher") or "",
                        "description": _plain_text(item.get("description") or ""),
                        "isbn": item.get("isbn") or "",
                        "asin": item.get("asin") or "",
                        "language": item.get("language") or "",
                        "year": item.get("year") or "",
                        "library_name": library_name,
                        "media_type": item.get("media_type") or "book",
                    },
                )
            except Exception as exc:
                log.warning("New series notification failed for %s: %s", series_name, exc)

    async def _notify_books_added_to_series(self, db: Session, items: list[dict]) -> None:
        if not self.notifier:
            return
        for item in items:
            title = str(item.get("title") or "Unknown")
            author = str(item.get("author") or "Unknown author")
            series_name = str(item.get("series") or "Unknown series")
            library_name = str(item.get("library_name") or "Audiobookshelf")
            body = f"{title} by {author} was added to {series_name} in {library_name}."
            try:
                await self.notifier.notify(
                    db,
                    "book_added_to_series",
                    "Book added to series",
                    body,
                    library_id=str(item.get("library_id") or ""),
                    context={
                        "item_id": item.get("abs_item_id") or "",
                        "title": title,
                        "author": author,
                        "series": series_name,
                        "book_count": item.get("book_count") or 0,
                        "narrator": item.get("narrator") or "",
                        "subtitle": item.get("subtitle") or "",
                        "publisher": item.get("publisher") or "",
                        "description": _plain_text(item.get("description") or ""),
                        "isbn": item.get("isbn") or "",
                        "asin": item.get("asin") or "",
                        "language": item.get("language") or "",
                        "year": item.get("year") or "",
                        "library_name": library_name,
                        "media_type": item.get("media_type") or "book",
                    },
                )
            except Exception as exc:
                log.warning("Series book notification failed for %s: %s", title, exc)

    def _new_podcast_baseline_key(self, library_id: str) -> str:
        digest = hashlib.sha256(library_id.encode("utf-8")).hexdigest()[:24]
        return f"notify_new_podcast_baseline_{digest}"

    def _new_podcast_baseline_complete(self, db: Session, library_id: str) -> bool:
        key = self._new_podcast_baseline_key(library_id)
        row = db.query(Setting).filter(Setting.key == key).first()
        return bool(row and str(row.value).strip().lower() == "true")

    def _mark_new_podcast_baseline_complete(self, db: Session, library_id: str) -> None:
        key = self._new_podcast_baseline_key(library_id)
        row = db.query(Setting).filter(Setting.key == key).first()
        if row:
            row.value = "true"
            row.updated_at = utcnow()
        else:
            db.add(Setting(key=key, value="true", updated_at=utcnow()))

    async def _notify_new_podcasts(self, db: Session, podcasts: list[dict]) -> None:
        if not self.notifier:
            return
        for podcast in podcasts:
            title = podcast.get("title") or "Unknown"
            author = podcast.get("author") or "Unknown author"
            library_name = podcast.get("library_name") or "Audiobookshelf"
            body = f"{title} by {author} was added to {library_name}."
            try:
                await self.notifier.notify(
                    db,
                    "new_podcast",
                    "New podcast added",
                    body,
                    library_id=str(podcast.get("library_id") or ""),
                    context={
                        "item_id": podcast.get("abs_item_id") or "",
                        "title": title,
                        "podcast": title,
                        "podcast_title": title,
                        "author": author,
                        "description": _plain_text(podcast.get("description") or ""),
                        "podcast_description": _plain_text(podcast.get("description") or ""),
                        "itunes_id": podcast.get("itunes_id") or "",
                        "library_name": library_name,
                        "media_type": podcast.get("media_type") or "podcast",
                    },
                )
            except Exception as exc:
                log.warning("New podcast notification failed for %s: %s", title, exc)

    def _podcast_episode_baseline_key(self, podcast_id: str) -> str:
        digest = hashlib.sha256(podcast_id.encode("utf-8")).hexdigest()[:24]
        return f"notify_podcast_episode_baseline_{digest}"

    def _podcast_episode_baseline(self, db: Session, podcast_id: str) -> set[str] | None:
        key = self._podcast_episode_baseline_key(podcast_id)
        row = db.query(Setting).filter(Setting.key == key).first()
        if not row:
            return None
        try:
            values = json.loads(row.value or "[]")
        except (TypeError, ValueError):
            return set()
        if not isinstance(values, list):
            return set()
        return {str(value) for value in values if value}

    def _store_podcast_episode_baseline(self, db: Session, podcast_id: str, episode_ids: set[str]) -> None:
        key = self._podcast_episode_baseline_key(podcast_id)
        value = json.dumps(sorted(episode_ids))
        row = db.query(Setting).filter(Setting.key == key).first()
        if row:
            row.value = value
            row.updated_at = utcnow()
        else:
            db.add(Setting(key=key, value=value, updated_at=utcnow()))

    def _clear_podcast_episode_baselines(self, db: Session, items: list[dict]) -> None:
        for item in items:
            podcast_id = str(item.get("abs_item_id") or "").strip()
            if not podcast_id:
                continue
            key = self._podcast_episode_baseline_key(podcast_id)
            row = db.query(Setting).filter(Setting.key == key).first()
            if row:
                db.delete(row)

    async def _notify_new_podcast_episodes(self, db: Session, episodes: list[dict]) -> None:
        if not self.notifier:
            return
        for episode in episodes:
            podcast_title = episode.get("podcast_title") or "Unknown podcast"
            episode_title = episode.get("episode_title") or "Unknown episode"
            library_name = episode.get("library_name") or "Audiobookshelf"
            body = f"{podcast_title} - {episode_title} was added to {library_name}."
            try:
                await self.notifier.notify(
                    db,
                    "new_podcast_episode",
                    "New podcast episode added",
                    body,
                    library_id=str(episode.get("library_id") or ""),
                    context={
                        "item_id": episode.get("podcast_id") or "",
                        "podcast_title": podcast_title,
                        "episode_title": episode_title,
                        "episode_description": episode.get("episode_description") or "",
                        "podcast_description": episode.get("podcast_description") or "",
                        "itunes_id": episode.get("itunes_id") or "",
                        "library_name": library_name,
                        "media_type": "podcast",
                    },
                )
            except Exception as exc:
                log.warning("New podcast episode notification failed for %s: %s", episode_title, exc)

    async def _sync_podcast_episode_baselines(self, db: Session, library: Library, items: list[dict]) -> list[dict]:
        new_episodes: list[dict] = []
        for item in items:
            podcast_id = str(item.get("abs_item_id") or "").strip()
            if not podcast_id:
                continue
            try:
                payload = await self.client.get_item(podcast_id, expanded=True)
            except Exception as exc:
                log.debug("Podcast episode sync failed for %s: %s", podcast_id, exc)
                continue

            media = payload.get("media") if isinstance(payload, dict) else None
            media = media if isinstance(media, dict) else {}
            metadata = media.get("metadata")
            metadata = metadata if isinstance(metadata, dict) else {}
            raw_episodes = media.get("episodes")
            raw_episodes = raw_episodes if isinstance(raw_episodes, list) else []

            podcast_title = str(metadata.get("title") or item.get("title") or "Unknown podcast")
            episodes = [episode for episode in raw_episodes if isinstance(episode, dict) and episode.get("id")]
            current_ids = {str(episode["id"]) for episode in episodes}
            baseline = self._podcast_episode_baseline(db, podcast_id)

            if baseline is not None:
                for episode in episodes:
                    episode_id = str(episode["id"])
                    if episode_id in baseline:
                        continue
                    new_episodes.append(
                        {
                            "podcast_title": podcast_title,
                            "episode_title": str(episode.get("title") or "Unknown episode"),
                            "episode_description": _plain_text(episode.get("description") or episode.get("subtitle") or ""),
                            "podcast_description": _plain_text(metadata.get("description") or ""),
                            "itunes_id": str(metadata.get("itunesId") or metadata.get("itunesID") or metadata.get("itunes_id") or ""),
                            "library_name": library.name,
                            "library_id": library.abs_library_id,
                            "podcast_id": podcast_id,
                        }
                    )

            remembered_ids = current_ids if baseline is None else baseline | current_ids
            self._store_podcast_episode_baseline(db, podcast_id, remembered_ids)

        return new_episodes

    def _chunks(self, values: Iterable[str], size: int = 900) -> Iterable[list[str]]:
        chunk: list[str] = []
        seen: set[str] = set()
        for value in values:
            if not value or value in seen:
                continue
            seen.add(value)
            chunk.append(value)
            if len(chunk) >= size:
                yield chunk
                chunk = []
        if chunk:
            yield chunk

    def _media_items_by_abs_id(self, db: Session, item_ids: Iterable[str]) -> dict[str, MediaItem]:
        media_items: dict[str, MediaItem] = {}
        for chunk in self._chunks(item_ids):
            for item in db.query(MediaItem).filter(MediaItem.abs_item_id.in_(chunk)).all():
                media_items[item.abs_item_id] = item
        return media_items

    def _history_by_session_id(
        self, db: Session, session_ids: Iterable[str]
    ) -> dict[str, ListeningHistory]:
        history_rows: dict[str, ListeningHistory] = {}
        for chunk in self._chunks(session_ids):
            for row in db.query(ListeningHistory).filter(ListeningHistory.abs_session_id.in_(chunk)).all():
                history_rows[row.abs_session_id] = row
        return history_rows

    def _history_model_values(self, row: dict) -> dict:
        values = dict(row)
        values.pop("raw_json", None)
        return {key: value for key, value in values.items() if hasattr(ListeningHistory, key)}

    def _libraries_by_abs_id(self, db: Session) -> dict[str, Library]:
        return {library.abs_library_id: library for library in db.query(Library).all()}

    async def sync_users(self, db: Session) -> list[AbsUser]:
        payload = await self.client.get_users()
        users = normalize_user_payload(payload)
        user_ids = {user["abs_user_id"] for user in users if user.get("abs_user_id")}
        existing_map: dict[str, AbsUser] = {}
        for chunk in self._chunks(user_ids):
            for existing in db.query(AbsUser).filter(AbsUser.abs_user_id.in_(chunk)).all():
                existing_map[existing.abs_user_id] = existing

        saved = []
        for user in users:
            user_id = user.get("abs_user_id") or ""
            if not user_id:
                continue
            existing = existing_map.get(user_id)
            if existing:
                existing.username = user["username"]
                existing.display_name = user["display_name"]
                existing.is_active = user["is_active"]
            else:
                existing = AbsUser(**user)
                db.add(existing)
                existing_map[user_id] = existing
            saved.append(existing)
        db.commit()
        return saved

    async def sync_libraries(self, db: Session) -> list[Library]:
        try:
            payload = await self.client.get_libraries()
        except Exception as exc:
            log.warning("Library sync failed: %s", exc)
            return []

        libraries = normalize_library_payload(payload)
        library_ids = {library["abs_library_id"] for library in libraries if library.get("abs_library_id")}
        existing_map: dict[str, Library] = {}
        for chunk in self._chunks(library_ids):
            for existing in db.query(Library).filter(Library.abs_library_id.in_(chunk)).all():
                existing_map[existing.abs_library_id] = existing

        saved = []
        now = utcnow()
        for library in libraries:
            library_id = library.get("abs_library_id") or ""
            if not library_id:
                continue
            existing = existing_map.get(library_id)
            if existing:
                for key, value in library.items():
                    setattr(existing, key, value)
                existing.updated_at = now
            else:
                existing = Library(**library, updated_at=now)
                db.add(existing)
                existing_map[library_id] = existing
            saved.append(existing)
        db.commit()
        return saved

    def _payload_total(self, payload) -> int | None:
        if not isinstance(payload, dict):
            return None
        for key in ("total", "totalItems", "numItems", "count"):
            value = payload.get(key)
            try:
                if value is not None:
                    return int(value)
            except (TypeError, ValueError):
                continue
        return None

    def _payload_has_next_page(
        self,
        payload,
        page: int = 0,
        row_count: int | None = None,
        items_per_page: int | None = None,
        loaded_count: int | None = None,
        page_size: int | None = None,
        normalized_count: int | None = None,
    ) -> bool:
        if not isinstance(payload, dict):
            return False

        per_page = items_per_page or page_size
        if not per_page:
            for key in ("itemsPerPage", "pageSize", "limit"):
                value = payload.get(key)
                try:
                    if value is not None:
                        per_page = int(value)
                        break
                except (TypeError, ValueError):
                    continue
        per_page = per_page or 50

        current_count = row_count
        if current_count is None:
            current_count = normalized_count if normalized_count is not None else 0
        if current_count <= 0:
            return False

        loaded = loaded_count
        if loaded is None:
            loaded = page * per_page + current_count

        for key in ("totalPages", "numPages", "pages"):
            value = payload.get(key)
            try:
                if value is not None:
                    return page + 1 < int(value)
            except (TypeError, ValueError):
                continue

        total = self._payload_total(payload)
        if total is not None:
            return loaded < total

        for key in ("hasNextPage", "hasNext", "nextPage"):
            value = payload.get(key)
            if isinstance(value, bool):
                return value
            if value not in (None, ""):
                return True

        return current_count >= per_page if per_page else False

    async def _get_user_history_page(self, user_id: str, page: int, items_per_page: int) -> dict:
        try:
            return await self.client.get_user_listening_sessions(
                user_id,
                items_per_page=items_per_page,
                page=page,
            )
        except TypeError:
            if page > 0:
                return {"sessions": []}
            return await self.client.get_user_listening_sessions(user_id, items_per_page=items_per_page)

    async def _get_library_items_page(self, library_id: str, page: int, limit: int) -> dict | list:
        try:
            return await self.client.get_library_items(library_id, limit=limit, page=page)
        except TypeError:
            if page > 0:
                return {"results": []}
            return await self.client.get_library_items(library_id, limit=limit)

    async def _get_library_series_page(self, library_id: str, page: int, limit: int) -> dict | list:
        try:
            return await self.client.get_library_series(library_id, limit=limit, page=page)
        except TypeError:
            if page > 0:
                return {"results": []}
            return await self.client.get_library_series(library_id, limit=limit)

    async def _fetch_library_items(self, library: Library, limit: int) -> tuple[list[dict], int | None, bool]:
        items: list[dict] = []
        total: int | None = None
        page = 0
        max_pages = 1000

        while page < max_pages:
            payload = await self._get_library_items_page(library.abs_library_id, page=page, limit=limit)
            page_items = normalize_media_item_payload(payload, library.abs_library_id, library.name)
            if total is None:
                total = self._payload_total(payload)
            items.extend(page_items)

            if not self._payload_has_next_page(
                payload,
                page=page,
                row_count=len(page_items),
                page_size=limit,
                loaded_count=len(items),
                normalized_count=len(page_items),
            ):
                return items, total, True

            page += 1

        log.warning("Stopped library item sync for %s after %s page(s)", library.name, max_pages)
        return items, total, False

    async def _fetch_library_series(self, library: Library, limit: int = 1000) -> tuple[list[dict], bool]:
        rows: list[dict] = []
        page = 0
        max_pages = 1000

        while page < max_pages:
            payload = await self._get_library_series_page(
                library.abs_library_id,
                page=page,
                limit=limit,
            )
            if isinstance(payload, list):
                page_rows = payload
            elif isinstance(payload, dict):
                page_rows = payload.get("results") or payload.get("series") or payload.get("items") or []
            else:
                page_rows = []
            page_rows = [row for row in page_rows if isinstance(row, dict)]
            rows.extend(page_rows)

            if not self._payload_has_next_page(
                payload,
                page=page,
                row_count=len(page_rows),
                page_size=limit,
                loaded_count=len(rows),
            ):
                return rows, True

            page += 1

        log.warning("Stopped series sync for %s after %s page(s)", library.name, max_pages)
        return rows, False

    async def sync_series(self, db: Session, libraries: list[Library]) -> int:
        detected = 0
        new_series: list[dict] = []
        series_additions: list[dict] = []
        for library in libraries:
            if str(library.media_type or "").strip().lower() != "book":
                continue
            try:
                rows, complete = await self._fetch_library_series(library)
            except Exception as exc:
                log.warning("Series sync failed for %s: %s", library.name, exc)
                continue
            if not complete:
                continue

            current_series: dict[str, dict] = {}
            current_membership: dict[str, set[str]] = {}
            for row in rows:
                series_id = str(row.get("id") or row.get("seriesId") or "").strip()
                series_name = str(row.get("name") or row.get("series") or "").strip()
                series = dict(row)
                series["series_id"] = series_id
                series["series"] = series_name
                series["library_id"] = library.abs_library_id
                series["library_name"] = library.name
                series_key = self._series_key(series)
                if not series_key:
                    continue
                books = self._series_books(db, series)
                series["normalized_books"] = books
                current_series[series_key] = series
                current_membership[series_key] = set(books)

            baseline = self._new_series_baseline(db, library.abs_library_id)
            membership_baseline = self._series_membership_baseline(db, library.abs_library_id)
            baseline, membership_baseline = self._migrate_series_baselines(
                current_series,
                baseline,
                membership_baseline,
            )
            if baseline is None:
                self._store_new_series_baseline(
                    db,
                    library.abs_library_id,
                    set(current_series),
                )
                self._store_series_membership_baseline(
                    db,
                    library.abs_library_id,
                    current_membership,
                )
                continue

            for series_key in sorted(current_series.keys() - baseline):
                series = current_series[series_key]
                books = series["normalized_books"]
                item = dict(next(iter(books.values()), {}))
                item.update(
                    {
                        "library_id": library.abs_library_id,
                        "library_name": library.name,
                        "series": series["series"],
                        "series_id": series["series_id"],
                        "book_count": len(books),
                    }
                )
                new_series.append(item)

            if membership_baseline is not None:
                for series_key, series in current_series.items():
                    if series_key not in baseline or series_key not in membership_baseline:
                        continue
                    books = series["normalized_books"]
                    added_item_ids = current_membership[series_key] - membership_baseline[series_key]
                    for item_id in sorted(added_item_ids):
                        item = dict(books[item_id])
                        item["book_count"] = len(books)
                        series_additions.append(item)

            remembered_membership = dict(membership_baseline or {})
            remembered_membership.update(current_membership)
            self._store_new_series_baseline(
                db,
                library.abs_library_id,
                baseline | current_series.keys(),
            )
            self._store_series_membership_baseline(
                db,
                library.abs_library_id,
                remembered_membership,
            )
            detected += len(current_series.keys() - baseline)
            if membership_baseline is not None:
                detected += sum(
                    len(current_membership[series_key] - membership_baseline[series_key])
                    for series_key in current_series.keys() & baseline & membership_baseline.keys()
                )

        db.commit()
        await self._notify_new_series(db, new_series)
        await self._notify_books_added_to_series(db, series_additions)
        return detected

    async def _fetch_user_history_rows(self, user_id: str, items_per_page: int = 50) -> list[dict]:
        rows: list[dict] = []
        page = 0
        max_pages = 100
        while page < max_pages:
            payload = await self._get_user_history_page(user_id, page, items_per_page)
            page_rows = normalize_history_payload(payload)
            rows.extend(page_rows)
            if not self._payload_has_next_page(payload, page=page, row_count=len(page_rows), items_per_page=items_per_page, loaded_count=len(rows)):
                break
            page += 1
        return rows

    async def sync_recent_items(self, db: Session, libraries: list[Library]) -> int:
        started = time.perf_counter()
        imported = 0
        new_books: list[dict] = []
        new_podcasts: list[dict] = []
        new_podcast_episodes: list[dict] = []
        request_limit = 5000
        for library in libraries:
            library_started = time.perf_counter()
            try:
                items, total, full_library_loaded = await self._fetch_library_items(library, request_limit)
            except Exception as exc:
                log.debug("Recent item sync failed for %s: %s", library.name, exc)
                continue

            current_ids = {item["abs_item_id"] for item in items if item.get("abs_item_id")}
            existing_map = self._media_items_by_abs_id(db, current_ids)
            media_type = str(library.media_type or "").strip().lower()
            is_book_library = media_type == "book"
            is_podcast_library = media_type == "podcast"
            book_baseline_complete = self._new_book_baseline_complete(db, library.abs_library_id) if is_book_library else False
            podcast_baseline_complete = self._new_podcast_baseline_complete(db, library.abs_library_id) if is_podcast_library else False
            now = utcnow()

            if total is not None:
                library.item_count = max(total, 0)
                library.updated_at = now

            media_item_fields = {column.name for column in MediaItem.__table__.columns} - {"id", "created_at", "updated_at"}
            for item in items:
                item_id = item.get("abs_item_id")
                if not item_id:
                    continue
                stored_item = {key: value for key, value in item.items() if key in media_item_fields}
                existing = existing_map.get(item_id)
                if existing:
                    for key, value in stored_item.items():
                        setattr(existing, key, value)
                    existing.updated_at = now
                else:
                    existing = MediaItem(**stored_item, updated_at=now)
                    db.add(existing)
                    existing_map[item_id] = existing
                    imported += 1
                    if is_book_library and book_baseline_complete:
                        new_book = dict(item)
                        new_book["library_id"] = library.abs_library_id
                        new_book["library_name"] = library.name
                        new_books.append(new_book)
                    if is_podcast_library and podcast_baseline_complete:
                        new_podcast = dict(item)
                        new_podcast["library_id"] = library.abs_library_id
                        new_podcast["library_name"] = library.name
                        new_podcasts.append(new_podcast)

            if is_book_library and full_library_loaded and not book_baseline_complete:
                self._mark_new_book_baseline_complete(db, library.abs_library_id)
            if is_podcast_library and full_library_loaded and not podcast_baseline_complete:
                self._mark_new_podcast_baseline_complete(db, library.abs_library_id)
            if is_podcast_library and full_library_loaded:
                episode_notifications_enabled = bool(self.notifier and event_enabled("new_podcast_episode"))
                if episode_notifications_enabled:
                    new_podcast_episodes.extend(await self._sync_podcast_episode_baselines(db, library, items))
                else:
                    self._clear_podcast_episode_baselines(db, items)

            if current_ids and full_library_loaded:
                deleted = (
                    db.query(MediaItem)
                    .filter(MediaItem.library_id == library.abs_library_id)
                    .filter(~MediaItem.abs_item_id.in_(current_ids))
                    .delete(synchronize_session=False)
                )
                if deleted:
                    log.info("Pruned %s deleted media item(s) from library %s", deleted, library.name)

            library_elapsed = time.perf_counter() - library_started
            if library_elapsed >= 2:
                log.info(
                    "Recent item sync for library %s completed in %.2fs (%s item(s))",
                    library.name,
                    library_elapsed,
                    len(items),
                )

        db.commit()
        await self._notify_new_books(db, new_books)
        await self._notify_new_podcasts(db, new_podcasts)
        await self._notify_new_podcast_episodes(db, new_podcast_episodes)
        elapsed = time.perf_counter() - started
        if elapsed >= 2:
            log.info("Recent item sync completed in %.2fs (%s imported)", elapsed, imported)
        return imported

    def _enrich_history_row_from_media_maps(
        self,
        row: dict,
        media_map: dict[str, MediaItem],
        library_map: dict[str, Library],
    ) -> None:
        item_id = row.get("abs_item_id") or ""
        media_item = media_map.get(item_id)
        if media_item:
            if not row.get("title") or row.get("title") == "Unknown":
                row["title"] = media_item.title or row.get("title") or "Unknown"
            if not row.get("author"):
                row["author"] = media_item.author or ""
            if not row.get("media_type") or row.get("media_type") == "unknown":
                row["media_type"] = media_item.media_type or row.get("media_type") or "unknown"
            if not row.get("library_id"):
                row["library_id"] = media_item.library_id or ""
            if not row.get("library_name"):
                row["library_name"] = media_item.library_name or ""

        library_id = row.get("library_id") or ""
        if library_id and not row.get("library_name"):
            library = library_map.get(library_id)
            if library:
                row["library_name"] = library.name or ""

    async def poll(self, db: Session) -> int:
        started = time.perf_counter()
        users = await self.sync_users(db)
        libraries = await self.sync_libraries(db)
        await self.sync_recent_items(db, libraries)
        await self.sync_series(db, libraries)
        await self.sync_collections(db, libraries)
        usernames = friendly_names(db)
        library_map = self._libraries_by_abs_id(db)

        imported = 0
        for user in users:
            if not user.abs_user_id or user.abs_user_id == "unknown":
                continue
            try:
                rows = await self._fetch_user_history_rows(user.abs_user_id)
            except Exception as exc:
                log.warning("History sync failed for user %s: %s", user.username, exc)
                continue

            item_ids = {row.get("abs_item_id") for row in rows if row.get("abs_item_id")}
            session_ids = {row.get("abs_session_id") for row in rows if row.get("abs_session_id")}
            media_map = self._media_items_by_abs_id(db, item_ids)
            existing_map = self._history_by_session_id(db, session_ids)

            user_started = time.perf_counter()
            for row in rows:
                if row["abs_user_id"] in usernames:
                    row["username"] = usernames[row["abs_user_id"]]
                self._enrich_history_row_from_media_maps(row, media_map, library_map)

                session_id = row.get("abs_session_id") or ""
                existing = existing_map.get(session_id)
                values = self._history_model_values(row)
                if existing:
                    for key, value in values.items():
                        setattr(existing, key, value)
                else:
                    existing = ListeningHistory(**values)
                    db.add(existing)
                    if session_id:
                        existing_map[session_id] = existing
                    imported += 1

            user_elapsed = time.perf_counter() - user_started
            if user_elapsed >= 2:
                log.info(
                    "History row processing for user %s completed in %.2fs (%s row(s))",
                    user.username,
                    user_elapsed,
                    len(rows),
                )

        db.commit()
        elapsed = time.perf_counter() - started
        if elapsed >= 2:
            log.info("History poll completed in %.2fs (%s imported)", elapsed, imported)
        return imported
