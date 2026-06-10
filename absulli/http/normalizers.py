from __future__ import annotations

from datetime import datetime, timezone

from absulli.core.time import utcnow
from typing import Any



def first_present(data: dict[str, Any], *keys: str, default: Any = "") -> Any:
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return value
    return default


def safe_float(value: Any, default: float = 0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_text(value: Any, *preferred_keys: str) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, dict):
        for key in preferred_keys:
            nested = value.get(key)
            if nested not in (None, ""):
                return safe_text(nested)
        for key in ("displayName", "username", "deviceName", "clientName", "name", "model", "manufacturer", "ipAddress", "id"):
            nested = value.get(key)
            if nested not in (None, ""):
                return safe_text(nested)
        return ""
    if isinstance(value, list):
        parts = [safe_text(v) for v in value]
        return ", ".join(part for part in parts if part)
    return str(value)


def parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, (int, float)):
        if value > 10_000_000_000:
            value = value / 1000
        return datetime.fromtimestamp(value, tz=timezone.utc).replace(tzinfo=None)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return None
    return None


def clamp_percent(value: float) -> float:
    if value < 0:
        return 0
    if value > 100:
        return 100
    return value


def extract_author(row: dict[str, Any], item: dict[str, Any], media: dict[str, Any]) -> str:
    metadata = media.get("metadata") if isinstance(media.get("metadata"), dict) else {}
    authors = metadata.get("authors") or media.get("authors") or item.get("authors") or row.get("authors")
    if isinstance(authors, list):
        names = []
        for author in authors:
            if isinstance(author, dict):
                names.append(safe_text(author, "name", "authorName"))
            else:
                names.append(safe_text(author))
        return ", ".join(name for name in names if name)
    return safe_text(
        first_present(
            row,
            "author",
            "authorName",
            default=first_present(
                item,
                "author",
                "authorName",
                default=first_present(metadata, "author", "authorName", default=first_present(media, "author", "authorName", default="")),
            ),
        )
    )



def extract_author_id(row: dict[str, Any], item: dict[str, Any], media: dict[str, Any]) -> str:
    metadata = media.get("metadata") if isinstance(media.get("metadata"), dict) else {}
    authors = metadata.get("authors") or media.get("authors") or item.get("authors") or row.get("authors")
    if isinstance(authors, list) and authors:
        first = authors[0]
        if isinstance(first, dict):
            return safe_text(first.get("id") or first.get("authorId") or first.get("asin") or "")
    author = metadata.get("author") or media.get("author") or item.get("author") or row.get("author")
    if isinstance(author, dict):
        return safe_text(author.get("id") or author.get("authorId") or "")
    return safe_text(
        first_present(
            row,
            "authorId",
            default=first_present(item, "authorId", default=first_present(metadata, "authorId", default=first_present(media, "authorId", default=""))),
        )
    )

def extract_library_id(row: dict[str, Any], item: dict[str, Any]) -> str:
    library = row.get("library") or item.get("library") or {}
    return safe_text(first_present(row, "libraryId", default=first_present(item, "libraryId", default=first_present(library, "id", default=""))))


def extract_library_name(row: dict[str, Any], item: dict[str, Any]) -> str:
    library = row.get("library") or item.get("library") or {}
    return safe_text(first_present(row, "libraryName", default=first_present(item, "libraryName", default=first_present(library, "name", default=""))))


def extract_device_fields(row: dict[str, Any]) -> dict[str, str]:
    device_obj = first_present(row, "device", "deviceInfo", default={})
    client_obj = first_present(row, "client", "player", default={})
    device_name = safe_text(device_obj, "deviceName", "name")
    model = safe_text(device_obj, "model", "deviceName", "name")
    client = safe_text(client_obj, "clientName", "name")
    if not client:
        client = safe_text(device_obj, "clientName")
    device = device_name or model or safe_text(device_obj)
    return {
        "device": device,
        "device_name": device_name or device,
        "model": model or "unknown",
        "client": client or "unknown",
    }


def normalize_online_payload(payload: dict[str, Any] | list[Any]) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        rows = payload
    else:
        rows = payload.get("openSessions") or payload.get("sessions") or payload.get("usersOnline") or []
    now = utcnow()
    normalized: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        item = row.get("libraryItem") or row.get("mediaItem") or row.get("item") or {}
        media = item.get("media") or row.get("media") or {}
        user = row.get("user") or {}
        title = safe_text(first_present(row, "displayTitle", "title", default=first_present(item, "title", "name", default=first_present(media, "title", default="Unknown"))))
        duration = safe_float(first_present(row, "duration", default=first_present(media, "duration", default=0)))
        current_time = safe_float(first_present(row, "currentTime", "progress", default=0))
        time_listening = safe_float(first_present(row, "timeListening", "durationListening", default=0))
        progress = clamp_percent(round((current_time / duration) * 100, 4)) if duration else 0
        user_id = safe_text(first_present(row, "userId", default=first_present(user, "id", default="unknown")))
        session_id = safe_text(first_present(row, "id", "sessionId", "socketId", default=f"{user_id}:{title}"))
        started = parse_ts(first_present(row, "startedAt", "startTime", "createdAt", default=None))
        updated = parse_ts(first_present(row, "updatedAt", "lastUpdate", default=None))
        device_fields = extract_device_fields(row)
        normalized.append(
            {
                "session_key": session_id,
                "abs_user_id": user_id,
                "username": safe_text(first_present(row, "username", default=first_present(user, "username", "name", default=user_id))),
                "abs_item_id": safe_text(first_present(row, "libraryItemId", "mediaItemId", default=first_present(item, "id", default=""))),
                "title": title,
                "author": extract_author(row, item, media),
                "media_type": safe_text(first_present(row, "mediaType", default=first_present(item, "mediaType", "type", default="unknown"))),
                "library_id": extract_library_id(row, item),
                "library_name": extract_library_name(row, item),
                **device_fields,
                "ip_address": safe_text(first_present(row, "ipAddress", "ip", default=""), "ipAddress", "ip"),
                "started_at": started or now,
                "updated_at": updated,
                "current_time": current_time,
                "duration": duration,
                "time_listening": time_listening,
                "progress": progress,
                "last_seen_at": now,
            }
        )
    return normalized


def normalize_user_payload(payload: dict[str, Any] | list[Any]) -> list[dict[str, Any]]:
    rows = payload if isinstance(payload, list) else payload.get("users", [])
    normalized = []
    for row in rows:
        if isinstance(row, dict):
            normalized.append(
                {
                    "abs_user_id": safe_text(row.get("id") or row.get("userId") or row.get("username")),
                    "username": safe_text(row.get("username") or row.get("name") or "unknown"),
                    "display_name": safe_text(row.get("displayName") or row.get("name") or ""),
                    "is_active": not row.get("isDisabled", False),
                }
            )
    return normalized


def normalize_library_payload(payload: dict[str, Any] | list[Any]) -> list[dict[str, Any]]:
    rows = payload if isinstance(payload, list) else payload.get("libraries", [])
    normalized = []
    for row in rows:
        if isinstance(row, dict):
            normalized.append(
                {
                    "abs_library_id": safe_text(row.get("id") or ""),
                    "name": safe_text(row.get("name") or "Unknown"),
                    "media_type": safe_text(row.get("mediaType") or row.get("type") or "unknown"),
                    "item_count": int(row.get("numItems") or row.get("itemCount") or row.get("items") or 0),
                    "display_order": int(row.get("displayOrder") or 0),
                }
            )
    return [row for row in normalized if row["abs_library_id"]]


def normalize_media_item_payload(payload: dict[str, Any] | list[Any], library_id: str = "", library_name: str = "") -> list[dict[str, Any]]:
    rows = payload if isinstance(payload, list) else payload.get("results") or payload.get("items") or payload.get("libraryItems") or []
    normalized = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        media = row.get("media") or {}
        metadata = media.get("metadata") or row.get("metadata") or {}
        title = safe_text(row.get("title") or media.get("title") or metadata.get("title") or "Unknown")
        added_at = parse_ts(row.get("addedAt") or row.get("createdAt") or row.get("ctime"))
        series = ""
        series_items = metadata.get("series") or media.get("series") or row.get("series")
        if isinstance(series_items, list) and series_items:
            first = series_items[0]
            if isinstance(first, dict):
                series = safe_text(first.get("name") or first.get("series") or "")
            else:
                series = safe_text(first)
        elif series_items:
            series = safe_text(series_items)
        normalized.append(
            {
                "abs_item_id": safe_text(row.get("id") or row.get("libraryItemId") or ""),
                "library_id": safe_text(row.get("libraryId") or library_id),
                "library_name": library_name,
                "media_type": safe_text(row.get("mediaType") or row.get("type") or "unknown"),
                "title": title,
                "author": extract_author(row, row, media) or safe_text(metadata.get("authorName") or metadata.get("author") or ""),
                "author_id": extract_author_id(row, row, media),
                "narrator": safe_text(metadata.get("narratorName") or metadata.get("narrator") or ""),
                "series": series,
                "year": safe_text(metadata.get("publishedYear") or metadata.get("releaseDate") or metadata.get("publishedDate") or ""),
                "duration": safe_float(media.get("duration") or row.get("duration")),
                "size_bytes": safe_float(row.get("size") or row.get("sizeBytes") or media.get("size")),
                "added_at": added_at,
            }
        )
    return [row for row in normalized if row["abs_item_id"]]


def normalize_history_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("sessions") or payload.get("listeningSessions") or payload.get("results") or []
    normalized = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        item = row.get("libraryItem") or row.get("item") or {}
        media = item.get("media") or row.get("media") or {}
        user = row.get("user") or {}
        duration_seconds = safe_float(first_present(row, "timeListening", "duration", default=0))
        current_time = safe_float(first_present(row, "currentTime", default=0))
        item_duration = safe_float(first_present(row, "duration", default=first_present(media, "duration", default=0)))
        progress = safe_float(first_present(row, "progress", default=0))
        if not progress and item_duration and current_time:
            progress = (current_time / item_duration) * 100
        device_fields = extract_device_fields(row)
        normalized.append(
            {
                "abs_session_id": safe_text(first_present(row, "id", "sessionId", default="")),
                "abs_user_id": safe_text(first_present(row, "userId", default=first_present(user, "id", default="unknown"))),
                "username": safe_text(first_present(row, "username", default=first_present(user, "username", "name", default="unknown"))),
                "abs_item_id": safe_text(first_present(row, "libraryItemId", default=first_present(item, "id", default=""))),
                "title": safe_text(first_present(row, "displayTitle", "title", default=first_present(item, "title", "name", default="Unknown"))),
                "author": extract_author(row, item, media),
                "media_type": safe_text(first_present(row, "mediaType", default=first_present(item, "mediaType", "type", default="unknown"))),
                "library_id": extract_library_id(row, item),
                "library_name": extract_library_name(row, item),
                "started_at": parse_ts(first_present(row, "startedAt", "startTime", "createdAt", default=None)),
                "updated_at": parse_ts(first_present(row, "updatedAt", "lastUpdate", default=None)),
                "duration_seconds": duration_seconds,
                "current_time": current_time,
                "progress": clamp_percent(progress),
                **device_fields,
            }
        )
    return [row for row in normalized if row["abs_session_id"]]
