import json
import logging
import time
from collections.abc import Iterable

from sqlalchemy.orm import Session

from absulli.database.models import AbsUser, Library, ListeningHistory, MediaItem
from absulli.http.abs_client import AudiobookshelfClient
from absulli.monitors.utils import friendly_names
from absulli.http.normalizers import (
    normalize_history_payload,
    normalize_library_payload,
    normalize_media_item_payload,
    normalize_user_payload,
    utcnow,
)

log = logging.getLogger(__name__)


class HistoryMonitor:
    def __init__(self, client: AudiobookshelfClient):
        self.client = client

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
        if hasattr(ListeningHistory, "raw_json"):
            raw_json = values.get("raw_json", dict(row))
            values["raw_json"] = raw_json if isinstance(raw_json, str) else json.dumps(raw_json, default=str)
        else:
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

    def _can_prune_library_items(self, payload, normalized_count: int, request_limit: int) -> bool:
        total = self._payload_total(payload)
        if total is not None:
            return total <= normalized_count
        return normalized_count < request_limit

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
            now = utcnow()

            if total is not None:
                library.item_count = max(total, 0)
                library.updated_at = now

            for item in items:
                item_id = item.get("abs_item_id")
                if not item_id:
                    continue
                existing = existing_map.get(item_id)
                if existing:
                    for key, value in item.items():
                        setattr(existing, key, value)
                    existing.updated_at = now
                else:
                    existing = MediaItem(**item, updated_at=now)
                    db.add(existing)
                    existing_map[item_id] = existing
                    imported += 1

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
