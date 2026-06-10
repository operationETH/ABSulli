import logging
from typing import Any

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

    async def sync_users(self, db: Session) -> list[AbsUser]:
        payload = await self.client.get_users()
        users = normalize_user_payload(payload)
        user_ids = [user["abs_user_id"] for user in users if user.get("abs_user_id")]
        existing_users = {
            user.abs_user_id: user
            for user in db.query(AbsUser).filter(AbsUser.abs_user_id.in_(user_ids)).all()
        } if user_ids else {}

        saved = []
        for user in users:
            existing = existing_users.get(user["abs_user_id"])
            if existing:
                existing.username = user["username"]
                existing.display_name = user["display_name"]
                existing.is_active = user["is_active"]
            else:
                existing = AbsUser(**user)
                db.add(existing)
                existing_users[user["abs_user_id"]] = existing
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
        library_ids = [library["abs_library_id"] for library in libraries if library.get("abs_library_id")]
        existing_libraries = {
            library.abs_library_id: library
            for library in db.query(Library).filter(Library.abs_library_id.in_(library_ids)).all()
        } if library_ids else {}

        saved = []
        for library in libraries:
            existing = existing_libraries.get(library["abs_library_id"])
            if existing:
                for key, value in library.items():
                    setattr(existing, key, value)
                existing.updated_at = utcnow()
            else:
                existing = Library(**library, updated_at=utcnow())
                db.add(existing)
            saved.append(existing)
        db.commit()
        return saved

    def _payload_total(self, payload: Any) -> int | None:
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

    def _can_prune_library_items(self, payload: Any, normalized_count: int, request_limit: int) -> bool:
        total = self._payload_total(payload)
        if total is not None:
            return total <= normalized_count
        return normalized_count < request_limit

    async def sync_recent_items(self, db: Session, libraries: list[Library]) -> int:
        imported = 0
        request_limit = 5000
        for library in libraries:
            try:
                payload = await self.client.get_library_items(library.abs_library_id, limit=request_limit)
            except Exception as exc:
                log.debug("Recent item sync failed for %s: %s", library.name, exc)
                continue

            items = normalize_media_item_payload(payload, library.abs_library_id, library.name)
            current_ids = {item["abs_item_id"] for item in items if item.get("abs_item_id")}

            item_ids = [item["abs_item_id"] for item in items if item.get("abs_item_id")]
            existing_items = {
                item.abs_item_id: item
                for item in db.query(MediaItem).filter(MediaItem.abs_item_id.in_(item_ids)).all()
            } if item_ids else {}

            for item in items:
                existing = existing_items.get(item["abs_item_id"])
                if existing:
                    for key, value in item.items():
                        setattr(existing, key, value)
                    existing.updated_at = utcnow()
                else:
                    db.add(MediaItem(**item, updated_at=utcnow()))
                    imported += 1

            if current_ids and self._can_prune_library_items(payload, len(items), request_limit):
                deleted = (
                    db.query(MediaItem)
                    .filter(MediaItem.library_id == library.abs_library_id)
                    .filter(~MediaItem.abs_item_id.in_(current_ids))
                    .delete(synchronize_session=False)
                )
                if deleted:
                    log.info("Pruned %s deleted media item(s) from library %s", deleted, library.name)

        db.commit()
        return imported

    def _history_payload_rows(self, payload: Any) -> list[Any]:
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            rows = payload.get("sessions") or payload.get("listeningSessions") or payload.get("results") or []
            return rows if isinstance(rows, list) else []
        return []

    def _payload_page_value(self, payload: Any, *keys: str) -> int | None:
        if not isinstance(payload, dict):
            return None
        for key in keys:
            value = payload.get(key)
            try:
                if value is not None:
                    return int(value)
            except (TypeError, ValueError):
                continue
        return None

    def _payload_has_next_page(
        self,
        payload: Any,
        page: int,
        row_count: int,
        items_per_page: int,
        loaded_count: int,
    ) -> bool:
        if not isinstance(payload, dict):
            return False

        page_info = payload.get("pageInfo") if isinstance(payload.get("pageInfo"), dict) else {}
        for key in ("hasNextPage", "hasMore", "nextPage"):
            if key in payload:
                value = payload.get(key)
                if isinstance(value, bool):
                    return value
                if value in (None, "", False):
                    return False
                return True
            if key in page_info:
                value = page_info.get(key)
                if isinstance(value, bool):
                    return value
                if value in (None, "", False):
                    return False
                return True

        total_pages = self._payload_page_value(payload, "totalPages", "pages")
        if total_pages is not None:
            return page + 1 < total_pages

        total = self._payload_total(payload)
        if total is not None:
            return loaded_count < total

        return row_count >= items_per_page

    async def _fetch_user_history_rows(self, user_id: str, items_per_page: int = 50, max_pages: int = 100) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        seen_session_ids: set[str] = set()

        for page in range(max_pages):
            payload = await self.client.get_user_listening_sessions(
                user_id,
                items_per_page=items_per_page,
                page=page,
            )
            page_rows = normalize_history_payload(payload)
            raw_row_count = len(self._history_payload_rows(payload))

            if not page_rows:
                break

            new_rows = []
            for row in page_rows:
                session_id = row.get("abs_session_id") or ""
                if session_id and session_id in seen_session_ids:
                    continue
                if session_id:
                    seen_session_ids.add(session_id)
                new_rows.append(row)

            if not new_rows:
                break

            rows.extend(new_rows)

            if not self._payload_has_next_page(payload, page, raw_row_count, items_per_page, len(rows)):
                break
        else:
            log.warning("History sync reached max page limit for user %s", user_id)

        return rows

    def _media_lookup(self, db: Session) -> dict[str, MediaItem]:
        return {item.abs_item_id: item for item in db.query(MediaItem).all() if item.abs_item_id}

    def _library_lookup(self, db: Session) -> dict[str, Library]:
        return {library.abs_library_id: library for library in db.query(Library).all() if library.abs_library_id}

    def _enrich_history_row_from_media(
        self,
        db: Session,
        row: dict[str, Any],
        media_items: dict[str, MediaItem] | None = None,
        libraries: dict[str, Library] | None = None,
    ) -> None:
        item_id = row.get("abs_item_id") or ""
        if media_items is None:
            media_item = db.query(MediaItem).filter_by(abs_item_id=item_id).first() if item_id else None
        else:
            media_item = media_items.get(item_id) if item_id else None

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
            if libraries is None:
                library = db.query(Library).filter_by(abs_library_id=library_id).first()
            else:
                library = libraries.get(library_id)
            if library:
                row["library_name"] = library.name or ""

    async def poll(self, db: Session) -> int:
        users = await self.sync_users(db)
        libraries = await self.sync_libraries(db)
        await self.sync_recent_items(db, libraries)
        usernames = friendly_names(db)
        media_items = self._media_lookup(db)
        library_lookup = self._library_lookup(db)

        imported = 0
        for user in users:
            if not user.abs_user_id or user.abs_user_id == "unknown":
                continue
            try:
                rows = await self._fetch_user_history_rows(user.abs_user_id)
            except Exception as exc:
                log.warning("History sync failed for user %s: %s", user.username, exc)
                continue

            session_ids = [row["abs_session_id"] for row in rows if row.get("abs_session_id")]
            existing_rows = {}
            if session_ids:
                existing_rows = {
                    row.abs_session_id: row
                    for row in db.query(ListeningHistory).filter(ListeningHistory.abs_session_id.in_(session_ids)).all()
                }

            for row in rows:
                if row["abs_user_id"] in usernames:
                    row["username"] = usernames[row["abs_user_id"]]
                self._enrich_history_row_from_media(db, row, media_items, library_lookup)
                existing = existing_rows.get(row["abs_session_id"])
                if existing:
                    for key, value in row.items():
                        setattr(existing, key, value)
                else:
                    db.add(ListeningHistory(**row))
                    imported += 1
        db.commit()
        return imported
