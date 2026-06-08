import json
import logging

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
        saved = []
        for user in users:
            existing = db.query(AbsUser).filter_by(abs_user_id=user["abs_user_id"]).first()
            if existing:
                existing.username = user["username"]
                existing.display_name = user["display_name"]
                existing.is_active = user["is_active"]
            else:
                existing = AbsUser(**user)
                db.add(existing)
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
        saved = []
        for library in libraries:
            existing = db.query(Library).filter_by(abs_library_id=library["abs_library_id"]).first()
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

            for item in items:
                existing = db.query(MediaItem).filter_by(abs_item_id=item["abs_item_id"]).first()
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


    def _enrich_history_row_from_media(self, db: Session, row: dict) -> None:
        item_id = row.get("abs_item_id") or ""
        media_item = db.query(MediaItem).filter_by(abs_item_id=item_id).first() if item_id else None
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
            library = db.query(Library).filter_by(abs_library_id=library_id).first()
            if library:
                row["library_name"] = library.name or ""


    async def poll(self, db: Session) -> int:
        users = await self.sync_users(db)
        libraries = await self.sync_libraries(db)
        await self.sync_recent_items(db, libraries)
        usernames = friendly_names(db)

        imported = 0
        for user in users:
            if not user.abs_user_id or user.abs_user_id == "unknown":
                continue
            try:
                payload = await self.client.get_user_listening_sessions(user.abs_user_id)
            except Exception as exc:
                log.warning("History sync failed for user %s: %s", user.username, exc)
                continue

            rows = normalize_history_payload(payload)
            for row in rows:
                if row["abs_user_id"] in usernames:
                    row["username"] = usernames[row["abs_user_id"]]
                self._enrich_history_row_from_media(db, row)
                existing = db.query(ListeningHistory).filter_by(abs_session_id=row["abs_session_id"]).first()
                row["raw_json"] = json.dumps(row["raw_json"], default=str)
                if existing:
                    for key, value in row.items():
                        setattr(existing, key, value)
                else:
                    db.add(ListeningHistory(**row))
                    imported += 1
        db.commit()
        return imported
