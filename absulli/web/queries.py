from datetime import datetime, timedelta
from urllib.parse import quote

from sqlalchemy import and_, desc, func, or_
from sqlalchemy.orm import Session

from absulli.core.time import utcnow
from absulli.database.models import AbsUser, ActivitySession, Library, ListeningHistory, MediaItem


def active_cutoff() -> datetime:
    return utcnow() - timedelta(seconds=300)


def active_sessions_query(db: Session):
    cutoff = active_cutoff()
    known_title = func.lower(func.coalesce(ActivitySession.title, "")).notin_(("", "unknown"))
    known_media_type = func.lower(func.coalesce(ActivitySession.media_type, "")).notin_(("", "unknown"))
    has_item_id = func.coalesce(ActivitySession.abs_item_id, "") != ""
    has_timing = or_(
        func.coalesce(ActivitySession.duration, 0) > 0,
        func.coalesce(ActivitySession.current_time, 0) > 0,
        func.coalesce(ActivitySession.time_listening, 0) > 0,
    )
    return db.query(ActivitySession).filter(
        ActivitySession.is_active.is_(True),
        func.coalesce(ActivitySession.updated_at, ActivitySession.last_seen_at) >= cutoff,
        or_(
            has_item_id,
            has_timing,
            and_(known_title, known_media_type),
        ),
    )


def enrich_active_rows(db: Session, rows: list[ActivitySession]) -> None:
    item_ids = {row.abs_item_id for row in rows if row.abs_item_id}
    media_items = (
        {
            item.abs_item_id: item
            for item in db.query(MediaItem).filter(MediaItem.abs_item_id.in_(item_ids)).all()
        }
        if item_ids
        else {}
    )
    library_names = {library.abs_library_id: library.name for library in db.query(Library).all()}

    for row in rows:
        item = media_items.get(row.abs_item_id)
        if item:
            row.library_id = row.library_id or item.library_id or ""
            row.library_name = row.library_name or item.library_name or ""
            row.author = row.author or item.author or ""
            row.media_type = row.media_type or item.media_type or "unknown"
        if not row.library_name and row.library_id in library_names:
            row.library_name = library_names[row.library_id] or ""


def fmt_seconds(seconds: float | int | None) -> str:
    seconds = int(seconds or 0)
    hours, rem = divmod(seconds, 3600)
    minutes, _ = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def compact_number(value: float | int | None) -> str:
    value = int(value or 0)
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}k"
    return str(value)


def top_rows(query_rows, value_formatter=compact_number, include_item_id: bool = False, url_builder=None):
    rows = []
    for row in query_rows:
        if include_item_id:
            item_id, name, value = row
            name = name or "Unknown"
            item = {"item_id": item_id or "", "name": name, "value": value_formatter(value)}
        else:
            name, value = row
            name = name or "Unknown"
            item = {"name": name, "value": value_formatter(value)}
        if url_builder:
            item["url"] = url_builder(name)
        rows.append(item)
    return rows


def first_item_id(items: list[dict]) -> str:
    for item in items:
        if item.get("item_id"):
            return item["item_id"]
    return ""


def clamp_days(value: str | int | None, default: int = 30) -> int:
    try:
        days = int(value or default)
    except (TypeError, ValueError):
        days = default
    return max(1, min(999, days))


def clean_stat_metric(value: str | None) -> str:
    return "duration" if value == "duration" else "count"


def clean_recent_type(value: str | None) -> str:
    return value if value in {"book", "podcast"} else "all"


def clamp_recent_limit(value: str | int | None, default: int = 50) -> int:
    try:
        limit = int(value or default)
    except (TypeError, ValueError):
        limit = default
    return max(1, min(999, limit))


def build_home_cards(db: Session, metric: str = "count", days: int = 30):
    metric = clean_stat_metric(metric)
    days = clamp_days(days)
    since = utcnow() - timedelta(days=days)
    history_date = func.coalesce(
        ListeningHistory.started_at,
        ListeningHistory.updated_at,
        ListeningHistory.imported_at,
    )
    history_filter = history_date >= since
    stat_value = (
        func.coalesce(func.sum(ListeningHistory.duration_seconds), 0).label("value")
        if metric == "duration"
        else func.count(ListeningHistory.id).label("value")
    )
    stat_formatter = fmt_seconds if metric == "duration" else compact_number
    played_title = "Most Listened Books" if metric == "duration" else "Most Played Books"
    value_label = "duration" if metric == "duration" else "plays"

    top_titles = (
        db.query(
            MediaItem.abs_item_id,
            MediaItem.title,
            stat_value,
        )
        .join(MediaItem, MediaItem.abs_item_id == ListeningHistory.abs_item_id)
        .filter(history_filter)
        .group_by(MediaItem.abs_item_id, MediaItem.title)
        .order_by(desc("value"))
        .limit(10)
        .all()
    )
    popular_titles = (
        db.query(
            MediaItem.abs_item_id,
            MediaItem.title,
            func.count(func.distinct(ListeningHistory.abs_user_id)).label("users"),
        )
        .join(MediaItem, MediaItem.abs_item_id == ListeningHistory.abs_item_id)
        .filter(history_filter)
        .group_by(MediaItem.abs_item_id, MediaItem.title)
        .order_by(desc("users"))
        .limit(10)
        .all()
    )
    recent_titles = (
        db.query(
            MediaItem.abs_item_id,
            MediaItem.title,
            func.max(history_date).label("last_seen"),
        )
        .join(MediaItem, MediaItem.abs_item_id == ListeningHistory.abs_item_id)
        .filter(history_filter)
        .group_by(MediaItem.abs_item_id, MediaItem.title)
        .order_by(desc("last_seen"))
        .limit(10)
        .all()
    )
    active_users = (
        db.query(ListeningHistory.username, stat_value)
        .filter(history_filter)
        .group_by(ListeningHistory.username)
        .order_by(desc("value"))
        .limit(10)
        .all()
    )
    client_name = func.coalesce(ListeningHistory.client, ListeningHistory.device, "Unknown")
    active_clients = (
        db.query(client_name, stat_value)
        .filter(history_filter)
        .group_by(client_name)
        .order_by(desc("value"))
        .limit(10)
        .all()
    )
    library_name = func.coalesce(
        func.nullif(ListeningHistory.library_name, ""),
        func.nullif(MediaItem.library_name, ""),
        func.nullif(Library.name, ""),
        func.nullif(ListeningHistory.media_type, ""),
        "Unknown",
    )
    library_id = func.coalesce(
        func.nullif(ListeningHistory.library_id, ""),
        func.nullif(MediaItem.library_id, ""),
        func.nullif(Library.abs_library_id, ""),
        "",
    )
    active_libraries = (
        db.query(library_name.label("name"), library_id.label("library_id"), stat_value)
        .outerjoin(MediaItem, MediaItem.abs_item_id == ListeningHistory.abs_item_id)
        .outerjoin(
            Library,
            or_(
                Library.abs_library_id == ListeningHistory.library_id,
                Library.abs_library_id == MediaItem.library_id,
            ),
        )
        .filter(history_filter)
        .group_by(library_name, library_id)
        .order_by(desc("value"))
        .limit(10)
        .all()
    )

    def date_value(dt):
        return dt.strftime("%m/%d") if dt else "–"

    played_items = top_rows(top_titles, stat_formatter, include_item_id=True)
    popular_items = top_rows(popular_titles, include_item_id=True)
    recent_items = top_rows(recent_titles, date_value, include_item_id=True)

    active_library_items = []
    for name, item_library_id, value in active_libraries:
        display_name = name or "Unknown"
        item = {"name": display_name, "value": stat_formatter(value)}
        if item_library_id:
            item["url"] = f"/libraries/{quote(item_library_id, safe='')}"
        active_library_items.append(item)

    return [
        {"title": played_title, "subtitle": value_label, "icon": "▤", "bg": "linear-gradient(135deg,#466985,#252b35)", "cover_item_id": first_item_id(played_items), "items": played_items, "wide": False},
        {"title": "Most Popular Books", "subtitle": "users", "icon": "▤", "bg": "linear-gradient(135deg,#725e88,#302b38)", "cover_item_id": first_item_id(popular_items), "items": popular_items, "wide": False},
        {"title": "Recently Listened", "subtitle": "last", "icon": "◫", "bg": "linear-gradient(135deg,#7b6a45,#333026)", "cover_item_id": first_item_id(recent_items), "items": recent_items, "wide": False},
        {"title": "Most Active Libraries", "subtitle": value_label, "icon": "♫", "bg": "linear-gradient(135deg,#6f6f6f,#252525)", "cover_item_id": "", "items": active_library_items, "wide": False},
        {"title": "Most Active Users", "subtitle": value_label, "icon": "●", "bg": "linear-gradient(135deg,#846f88,#37313a)", "cover_item_id": "", "items": top_rows(active_users, stat_formatter, url_builder=lambda name: f"/users/{quote(name, safe='')}"), "wide": False},
        {"title": "Most Active Platforms", "subtitle": value_label, "icon": "☰", "bg": "linear-gradient(135deg,#4e7b58,#24352a)", "cover_item_id": "", "items": top_rows(active_clients, stat_formatter), "wide": False},
    ]

def build_library_cards(db: Session):
    library_rows = []
    for library in db.query(Library).order_by(Library.display_order.asc(), Library.name.asc()).all():
        imported_count = db.query(MediaItem).filter(MediaItem.library_id == library.abs_library_id).count()
        value = max(int(library.item_count or 0), int(imported_count or 0))
        library_rows.append(
            {
                "name": library.name or "Unknown",
                "value": compact_number(value),
                "sort_value": value,
                "url": f"/libraries/{quote(library.abs_library_id, safe='')}",
            }
        )
    library_rows = sorted(library_rows, key=lambda item: int(item.get("sort_value") or 0), reverse=True)[:10]

    item_types = (
        db.query(MediaItem.media_type, func.count(MediaItem.id).label("items"))
        .group_by(MediaItem.media_type)
        .order_by(desc("items"))
        .limit(10)
        .all()
    )
    authors = (
        db.query(
            MediaItem.author,
            func.count(MediaItem.id).label("items"),
            func.min(MediaItem.author_id).label("author_id"),
            func.min(MediaItem.abs_item_id).label("cover_item_id"),
        )
        .filter(MediaItem.author != "")
        .group_by(MediaItem.author)
        .order_by(desc("items"))
        .limit(10)
        .all()
    )
    author_rows = [
        {
            "name": name or "Unknown",
            "value": compact_number(value),
            "author_id": author_id or "",
            "cover_item_id": cover_item_id or "",
            "url": f"/authors/{quote(name or 'Unknown', safe='')}",
        }
        for name, value, author_id, cover_item_id in authors
    ]
    cover_author_id = next((item["author_id"] for item in author_rows if item.get("author_id")), "")
    cover_item_id = next((item["cover_item_id"] for item in author_rows if item.get("cover_item_id")), "")
    return [
        {"title": "Libraries", "icon": "▤", "bg": "linear-gradient(135deg,#6f6645,#2f2e27)", "items": library_rows, "wide": False},
        {"title": "Media Types", "icon": "◫", "bg": "linear-gradient(135deg,#565656,#2e2e2e)", "items": top_rows(item_types), "wide": False},
        {
            "title": "Authors",
            "icon": "✎",
            "bg": "linear-gradient(135deg,#4d647a,#27313b)",
            "items": author_rows,
            "cover_author_id": cover_author_id,
            "cover_item_id": cover_item_id,
            "wide": False,
        },
    ]


def media_history_date():
    return func.coalesce(
        ListeningHistory.started_at,
        ListeningHistory.updated_at,
        ListeningHistory.imported_at,
    )




def history_row_date(row: ListeningHistory) -> datetime | None:
    return row.started_at or row.updated_at or row.imported_at


def window_stats_for_history(rows: list[ListeningHistory]) -> list[dict]:
    now = utcnow()
    windows = [
        ("Last 24 hours", now - timedelta(days=1)),
        ("Last 7 days", now - timedelta(days=7)),
        ("Last 30 days", now - timedelta(days=30)),
        ("All Time", None),
    ]
    stats = []
    for label, since in windows:
        window_rows = [
            row
            for row in rows
            if since is None or ((row_date := history_row_date(row)) and row_date >= since)
        ]
        seconds = sum(row.duration_seconds or 0 for row in window_rows)
        stats.append({"label": label, "plays": len(window_rows), "duration": fmt_seconds(seconds)})
    return stats


def accumulate_history_stats(rows: list[ListeningHistory], key_fn, seed_fn, limit: int = 12, update_fn=None) -> list[dict]:
    totals: dict[str, dict[str, float | int | str]] = {}
    for row in rows:
        key = str(key_fn(row) or "Unknown")
        stats = totals.setdefault(key, seed_fn(row, key))
        stats["plays"] = int(stats["plays"]) + 1
        stats["seconds"] = float(stats["seconds"]) + float(row.duration_seconds or 0)
        if update_fn:
            update_fn(stats, row)
    return sorted(totals.values(), key=lambda item: (int(item["plays"]), float(item["seconds"])), reverse=True)[:limit]


def user_stats_from_history(rows: list[ListeningHistory], limit: int = 12) -> list[dict]:
    sorted_rows = accumulate_history_stats(
        rows,
        lambda row: row.username or "Unknown",
        lambda row, key: {"username": key, "plays": 0, "seconds": 0},
        limit=limit,
    )
    return [
        {
            "username": str(row["username"]),
            "initial": str(row["username"] or "?")[:1].upper(),
            "plays": int(row["plays"]),
            "duration": fmt_seconds(float(row["seconds"])),
        }
        for row in sorted_rows
    ]

def media_window_stats(db: Session, item_id: str):
    now = utcnow()
    history_date = media_history_date()
    windows = [
        ("Last 24 hours", now - timedelta(days=1)),
        ("Last 7 days", now - timedelta(days=7)),
        ("Last 30 days", now - timedelta(days=30)),
        ("All Time", None),
    ]
    stats = []
    for label, since in windows:
        query = db.query(
            func.count(ListeningHistory.id),
            func.coalesce(func.sum(ListeningHistory.duration_seconds), 0),
        ).filter(ListeningHistory.abs_item_id == item_id)
        if since is not None:
            query = query.filter(history_date >= since)
        plays, seconds = query.one()
        stats.append({"label": label, "plays": int(plays or 0), "duration": fmt_seconds(seconds)})
    return stats


def media_user_stats(db: Session, item_id: str):
    rows = (
        db.query(
            ListeningHistory.username,
            func.count(ListeningHistory.id).label("plays"),
            func.coalesce(func.sum(ListeningHistory.duration_seconds), 0).label("seconds"),
        )
        .filter(ListeningHistory.abs_item_id == item_id)
        .group_by(ListeningHistory.username)
        .order_by(desc("plays"), desc("seconds"))
        .limit(12)
        .all()
    )
    return [
        {
            "username": username or "Unknown",
            "initial": (username or "?")[:1].upper(),
            "plays": int(plays or 0),
            "duration": fmt_seconds(seconds),
        }
        for username, plays, seconds in rows
    ]


def resolve_media_title(media_item: MediaItem | None, history_row: ListeningHistory | None, item_id: str) -> str:
    if media_item and media_item.title:
        return media_item.title
    if history_row and history_row.title:
        return history_row.title
    return item_id


def library_icon(media_type: str | None) -> str:
    media_type = (media_type or "").casefold()
    if "podcast" in media_type:
        return "☊"
    if "book" in media_type:
        return "▤"
    return "◫"


def library_items(db: Session, library_id: str, limit: int | None = None) -> list[MediaItem]:
    query = (
        db.query(MediaItem)
        .filter(MediaItem.library_id == library_id)
        .order_by(desc(MediaItem.added_at), MediaItem.title.asc())
    )
    if limit is not None:
        query = query.limit(limit)
    return query.all()


def library_history_rows(db: Session, library: Library, limit: int | None = None) -> list[ListeningHistory]:
    query = db.query(ListeningHistory).filter(
        or_(
            ListeningHistory.library_id == library.abs_library_id,
            ListeningHistory.library_name == library.name,
        )
    ).order_by(desc(media_history_date()))
    if limit is not None:
        query = query.limit(limit)
    return query.all()


def library_window_stats(db: Session, library: Library):
    return window_stats_for_history(library_history_rows(db, library))


def library_user_stats(db: Session, library: Library):
    return user_stats_from_history(library_history_rows(db, library))


def library_recently_played_items(db: Session, library: Library, limit: int = 20) -> list[dict]:
    rows = library_history_rows(db, library, limit=limit * 4)
    item_ids = {row.abs_item_id for row in rows if row.abs_item_id}
    media_items = (
        {
            item.abs_item_id: item
            for item in db.query(MediaItem).filter(MediaItem.abs_item_id.in_(item_ids)).all()
        }
        if item_ids
        else {}
    )
    seen: set[str] = set()
    items: list[dict] = []
    for row in rows:
        item_id = row.abs_item_id or ""
        dedupe_key = item_id or row.title or str(row.id)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        media_item = media_items.get(item_id)
        items.append(
            {
                "abs_item_id": item_id,
                "title": (media_item.title if media_item else "") or row.title or "Unknown title",
                "subtitle": (media_item.series if media_item else "") or row.author or row.media_type or "Unknown",
                "when": row.started_at or row.updated_at or row.imported_at,
            }
        )
        if len(items) >= limit:
            break
    return items


def library_top_items(db: Session, library: Library, limit: int = 10) -> list[dict]:
    rows = (
        db.query(
            ListeningHistory.abs_item_id,
            ListeningHistory.title,
            func.count(ListeningHistory.id).label("plays"),
            func.coalesce(func.sum(ListeningHistory.duration_seconds), 0).label("seconds"),
        )
        .filter(or_(ListeningHistory.library_id == library.abs_library_id, ListeningHistory.library_name == library.name))
        .group_by(ListeningHistory.abs_item_id, ListeningHistory.title)
        .order_by(desc("plays"), desc("seconds"))
        .limit(limit)
        .all()
    )
    return [
        {
            "abs_item_id": item_id or "",
            "title": title or "Unknown title",
            "plays": int(plays or 0),
            "duration": fmt_seconds(seconds),
        }
        for item_id, title, plays, seconds in rows
    ]


def author_items(db: Session, author_name: str) -> list[MediaItem]:
    return (
        db.query(MediaItem)
        .filter(MediaItem.author == author_name)
        .order_by(MediaItem.series.asc(), MediaItem.title.asc())
        .all()
    )


def _norm_text(value: str | None) -> str:
    return " ".join((value or "").casefold().split())


def author_book_map(db: Session, author_name: str) -> dict[str, str]:
    return {book.abs_item_id: book.title for book in author_items(db, author_name) if book.abs_item_id}


def author_history_filter(db: Session, author_name: str):
    item_ids = list(author_book_map(db, author_name).keys())
    if item_ids:
        return ListeningHistory.abs_item_id.in_(item_ids)
    return ListeningHistory.author == author_name


def author_history_rows(db: Session, author_name: str, limit: int | None = None) -> list[ListeningHistory]:
    book_map = author_book_map(db, author_name)
    query = db.query(ListeningHistory).filter(author_history_filter(db, author_name)).order_by(desc(media_history_date()))
    if limit is not None:
        query = query.limit(limit * 3 if book_map else limit)
    rows = query.all()
    if not book_map:
        return rows

    filtered = []
    normalized_titles = {item_id: _norm_text(title) for item_id, title in book_map.items()}
    for row in rows:
        expected_title = normalized_titles.get(row.abs_item_id or "")
        if expected_title and _norm_text(row.title) == expected_title:
            filtered.append(row)
            if limit is not None and len(filtered) >= limit:
                break
    return filtered


def author_window_stats(db: Session, author_name: str):
    return window_stats_for_history(author_history_rows(db, author_name))


def author_user_stats(db: Session, author_name: str):
    return user_stats_from_history(author_history_rows(db, author_name))



def user_display_name(user: AbsUser | None, fallback: str = "Unknown") -> str:
    if not user:
        return fallback or "Unknown"
    return user.display_name or user.username or fallback or "Unknown"


def user_initial(username: str | None) -> str:
    return (username or "?")[:1].upper()


def resolve_user(db: Session, user_key: str) -> AbsUser | None:
    user_key = (user_key or "").strip()
    if not user_key:
        return None
    return (
        db.query(AbsUser)
        .filter(
            or_(
                AbsUser.abs_user_id == user_key,
                AbsUser.username == user_key,
                AbsUser.display_name == user_key,
            )
        )
        .first()
    )


def user_history_filter(user: AbsUser | None, user_key: str):
    names = {user_key}
    if user:
        names.update({user.username, user.display_name})
        if user.abs_user_id:
            return or_(
                ListeningHistory.abs_user_id == user.abs_user_id,
                ListeningHistory.username.in_([name for name in names if name]),
            )
    return ListeningHistory.username.in_([name for name in names if name])


def user_history_rows(db: Session, user: AbsUser | None, user_key: str, limit: int | None = None) -> list[ListeningHistory]:
    query = db.query(ListeningHistory).filter(user_history_filter(user, user_key)).order_by(desc(media_history_date()))
    if limit is not None:
        query = query.limit(limit)
    return query.all()


def user_window_stats(db: Session, user: AbsUser | None, user_key: str):
    return window_stats_for_history(user_history_rows(db, user, user_key))


def user_player_stats(db: Session, user: AbsUser | None, user_key: str, limit: int = 12):
    sorted_rows = accumulate_history_stats(
        user_history_rows(db, user, user_key),
        lambda row: row.device_name or row.model or row.device or row.client or "Unknown",
        lambda row, key: {
            "name": key,
            "platform": row.client or row.device or "Unknown",
            "plays": 0,
            "seconds": 0,
        },
        limit=limit,
    )
    return [
        {
            "name": str(row["name"]),
            "platform": str(row["platform"]),
            "initial": user_initial(str(row["name"])),
            "plays": int(row["plays"]),
            "duration": fmt_seconds(float(row["seconds"])),
        }
        for row in sorted_rows
    ]


def user_library_stats(db: Session, user: AbsUser | None, user_key: str, limit: int = 8):
    def update_library_id(stats: dict[str, float | int | str], row: ListeningHistory) -> None:
        if not stats["library_id"] and row.library_id:
            stats["library_id"] = row.library_id

    sorted_rows = accumulate_history_stats(
        user_history_rows(db, user, user_key),
        lambda row: row.library_name or row.media_type or "Unknown",
        lambda row, key: {"name": key, "library_id": row.library_id or "", "plays": 0, "seconds": 0},
        limit=limit,
        update_fn=update_library_id,
    )
    return [
        {
            "name": str(row["name"]),
            "library_id": str(row["library_id"]),
            "plays": int(row["plays"]),
            "duration": fmt_seconds(float(row["seconds"])),
        }
        for row in sorted_rows
    ]


def user_top_items(db: Session, user: AbsUser | None, user_key: str, limit: int = 10):
    rows = (
        db.query(
            ListeningHistory.abs_item_id,
            ListeningHistory.title,
            func.count(ListeningHistory.id).label("plays"),
            func.coalesce(func.sum(ListeningHistory.duration_seconds), 0).label("seconds"),
        )
        .filter(user_history_filter(user, user_key))
        .group_by(ListeningHistory.abs_item_id, ListeningHistory.title)
        .order_by(desc("plays"), desc("seconds"))
        .limit(limit)
        .all()
    )
    return [
        {
            "abs_item_id": item_id or "",
            "title": title or "Unknown title",
            "plays": int(plays or 0),
            "duration": fmt_seconds(seconds),
        }
        for item_id, title, plays, seconds in rows
    ]


def user_recently_played_items(db: Session, user: AbsUser | None, user_key: str, limit: int = 20) -> list[dict]:
    rows = user_history_rows(db, user, user_key, limit=limit * 4)
    item_ids = {row.abs_item_id for row in rows if row.abs_item_id}
    media_items = (
        {
            item.abs_item_id: item
            for item in db.query(MediaItem).filter(MediaItem.abs_item_id.in_(item_ids)).all()
        }
        if item_ids
        else {}
    )
    items: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        item_id = row.abs_item_id or ""
        dedupe_key = item_id or f"{row.title}:{row.started_at}:{row.imported_at}"
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        media_item = media_items.get(item_id)
        items.append(
            {
                "abs_item_id": item_id,
                "title": (media_item.title if media_item else "") or row.title or "Unknown title",
                "subtitle": (media_item.series if media_item else "") or row.author or row.media_type or "Unknown",
                "when": row.started_at or row.updated_at or row.imported_at,
            }
        )
        if len(items) >= limit:
            break
    return items

def author_cover_item_id(db: Session, author_name: str) -> str:
    row = (
        db.query(MediaItem.abs_item_id)
        .filter(MediaItem.author == author_name)
        .order_by(desc(MediaItem.added_at), desc(MediaItem.updated_at))
        .first()
    )
    return row[0] if row and row[0] else ""


def first_author_id_from_books(books: list[MediaItem]) -> str:
    for book in books:
        author_id = getattr(book, "author_id", "") or ""
        if author_id:
            return author_id
    return ""


def author_payload_value(payload: dict | None, *keys: str) -> str:
    if not isinstance(payload, dict):
        return ""

    payloads = [payload]
    nested_author = payload.get("author")
    if isinstance(nested_author, dict):
        payloads.append(nested_author)
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        payloads.append(metadata)

    for current in payloads:
        for key in keys:
            value = current.get(key)
            if value not in (None, ""):
                return str(value)
    return ""


def clean_author_description(description: str) -> str:
    return " ".join((description or "").replace("\r", " ").replace("\n", " ").split())
