from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Gauge, Summary, generate_latest
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from absulli.database.models import AbsUser, ActivitySession, Library, ListeningHistory, MediaItem
from absulli.database.session import get_db
from absulli.core.setup_state import get_setup_setting, is_setup_complete
from absulli.core.time import utcnow, utcnow_iso, unix_seconds
from absulli.core.security import rotate_session_version, verify_metrics_access
from absulli.web.queries import active_sessions_query, enrich_active_rows

router = APIRouter(prefix="/api")
metrics_router = APIRouter()


@router.post("/auth/revoke-sessions")
def revoke_browser_sessions(request: Request):
    if getattr(request.state, "absulli_auth_method", "") != "api_token":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API token required to revoke browser sessions",
        )

    rotate_session_version()
    return {"status": "ok", "revoked_sessions": True}


@router.get("/status")
def status(db: Session = Depends(get_db)):
    abs_reachable_value = get_setup_setting("abs_reachable", "")
    return {
        "status": "ok",
        "time": utcnow_iso(),
        "setup_required": not is_setup_complete(),
        "abs_reachable": abs_reachable_value == "true" if abs_reachable_value else None,
        "abs_last_success_at": get_setup_setting("abs_last_success_at", ""),
        "abs_last_failure_at": get_setup_setting("abs_last_failure_at", ""),
        "active_sessions": active_sessions_query(db).count(),
        "history_rows": db.query(ListeningHistory).count(),
        "users": db.query(AbsUser).count(),
        "libraries": db.query(Library).count(),
        "recent_items": db.query(MediaItem).count(),
    }


@router.get("/activity")
def activity(db: Session = Depends(get_db)):
    rows = active_sessions_query(db).order_by(desc(ActivitySession.last_seen_at)).all()
    enrich_active_rows(db, rows)
    return [
        {
            "session_key": row.session_key,
            "abs_item_id": row.abs_item_id,
            "username": row.username,
            "title": row.title,
            "author": row.author,
            "media_type": row.media_type,
            "library_id": row.library_id,
            "library_name": row.library_name,
            "current_time": row.current_time,
            "duration": row.duration,
            "time_listening": row.time_listening,
            "progress": row.progress,
            "device": row.device,
            "device_name": row.device_name,
            "model": row.model,
            "client": row.client,
            "started_at": row.started_at,
            "updated_at": row.updated_at,
            "last_seen_at": row.last_seen_at,
        }
        for row in rows
    ]


@router.get("/history")
def history(limit: int = 100, db: Session = Depends(get_db)):
    rows = db.query(ListeningHistory).order_by(desc(ListeningHistory.imported_at)).limit(min(limit, 500)).all()
    return [
        {
            "session_key": row.abs_session_id,
            "abs_item_id": row.abs_item_id,
            "username": row.username,
            "title": row.title,
            "author": row.author,
            "media_type": row.media_type,
            "library_id": row.library_id,
            "library_name": row.library_name,
            "duration_seconds": row.duration_seconds,
            "current_time": row.current_time,
            "progress": row.progress,
            "client": row.client,
            "model": row.model,
            "started_at": row.started_at,
            "updated_at": row.updated_at,
            "imported_at": row.imported_at,
        }
        for row in rows
    ]


@router.get("/users")
def users(db: Session = Depends(get_db)):
    return [
        {"id": row.abs_user_id, "username": row.username, "display_name": row.display_name, "is_active": row.is_active}
        for row in db.query(AbsUser).order_by(AbsUser.username).all()
    ]


@router.get("/libraries")
def libraries(db: Session = Depends(get_db)):
    return [
        {
            "id": row.abs_library_id,
            "name": row.name,
            "media_type": row.media_type,
            "item_count": row.item_count,
            "updated_at": row.updated_at,
        }
        for row in db.query(Library).order_by(Library.display_order, Library.name).all()
    ]


@metrics_router.get("/metrics")
def metrics(request: Request, db: Session = Depends(get_db)):
    verify_metrics_access(request)
    registry = CollectorRegistry()

    up = Gauge("audiobookshelf_up", "1 if Audiobookshelf was reachable during the last scrape", registry=registry)
    last_success = Gauge("audiobookshelf_last_scrape_success", "1 if last scrape was successful", registry=registry)
    last_ts = Gauge("audiobookshelf_last_scrape_timestamp_seconds", "Unix timestamp of last successful scrape", registry=registry)
    open_total = Gauge("audiobookshelf_open_sessions_total", "Number of active/open Audiobookshelf playback sessions", registry=registry)
    users_total = Gauge("audiobookshelf_users_total", "Number of users in Audiobookshelf", registry=registry)
    sessions_total = Gauge("audiobookshelf_sessions_total", "Total number of imported listening sessions", registry=registry)

    user_seconds = Gauge("audiobookshelf_user_listening_seconds_total", "Total listening time per user across all sessions", ["user"], registry=registry)
    user_sessions = Gauge("audiobookshelf_user_sessions_total", "Total number of sessions per user", ["user"], registry=registry)
    book_seconds = Gauge("audiobookshelf_book_listening_seconds_total", "Total listening time per media item title", ["media_type", "title"], registry=registry)
    device_seconds = Gauge("audiobookshelf_device_listening_seconds_total", "Total listening time per client / device model", ["client", "model"], registry=registry)
    weekday_seconds = Gauge("audiobookshelf_weekday_listening_seconds_total", "Total listening time grouped by day of week", ["day"], registry=registry)
    library_seconds = Gauge("audiobookshelf_library_listening_seconds_total", "Total listening time per library", ["library_id", "library_name"], registry=registry)
    library_sessions = Gauge("audiobookshelf_library_sessions_total", "Total number of sessions per library", ["library_id", "library_name"], registry=registry)
    library_items = Gauge("audiobookshelf_library_items_total", "Total items per library", ["library_id", "library_name"], registry=registry)

    open_info = Gauge(
        "audiobookshelf_open_session_info",
        "Information about active/open Audiobookshelf playback sessions. Value is always 1.",
        ["session_id", "user_id", "user", "title", "author", "media_type", "library_id", "library_name", "client", "model", "device_name"],
        registry=registry,
    )
    open_current = Gauge("audiobookshelf_open_session_current_time_seconds", "Current playback position for active/open sessions in seconds", ["session_id", "user", "title", "client", "model"], registry=registry)
    open_duration = Gauge("audiobookshelf_open_session_duration_seconds", "Total media duration for active/open sessions in seconds", ["session_id", "user", "title", "client", "model"], registry=registry)
    open_progress = Gauge("audiobookshelf_open_session_progress_percent", "Current playback progress for active/open sessions as a percent", ["session_id", "user", "title", "client", "model"], registry=registry)
    open_started = Gauge("audiobookshelf_open_session_started_timestamp_seconds", "Start timestamp for active/open sessions as Unix seconds", ["session_id", "user", "title", "client", "model"], registry=registry)
    open_updated = Gauge("audiobookshelf_open_session_updated_timestamp_seconds", "Last update timestamp for active/open sessions as Unix seconds", ["session_id", "user", "title", "client", "model"], registry=registry)
    open_listening = Gauge("audiobookshelf_open_session_time_listening_seconds", "Time listened during active/open session in seconds", ["session_id", "user", "title", "client", "model"], registry=registry)

    recent_info = Gauge(
        "audiobookshelf_recent_session_info",
        "Information about recently listened Audiobookshelf sessions. Value is always 1.",
        ["session_id", "user_id", "user", "title", "author", "media_type", "library_id", "library_name", "client", "model", "device_name", "date", "day"],
        registry=registry,
    )
    recent_listening = Gauge("audiobookshelf_recent_session_listening_seconds", "Listening time for recently listened sessions in seconds", ["session_id", "user", "title", "client", "model"], registry=registry)
    recent_progress = Gauge("audiobookshelf_recent_session_progress_percent", "Playback progress for recently listened sessions as a percent", ["session_id", "user", "title", "client", "model"], registry=registry)
    recent_started = Gauge("audiobookshelf_recent_session_started_timestamp_seconds", "Start timestamp for recently listened sessions as Unix seconds", ["session_id", "user", "title", "client", "model"], registry=registry)
    recent_updated = Gauge("audiobookshelf_recent_session_updated_timestamp_seconds", "Last update timestamp for recently listened sessions as Unix seconds", ["session_id", "user", "title", "client", "model"], registry=registry)

    added_info = Gauge(
        "audiobookshelf_recent_added_info",
        "Information about recently added Audiobookshelf library items. Value is always 1.",
        ["item_id", "title", "author", "media_type", "library_id", "library_name", "series", "year"],
        registry=registry,
    )
    added_duration = Gauge("audiobookshelf_recent_added_duration_seconds", "Duration for recently added library items in seconds", ["item_id", "title", "author", "media_type", "library_name"], registry=registry)
    added_size = Gauge("audiobookshelf_recent_added_size_bytes", "Size for recently added library items in bytes", ["item_id", "title", "author", "media_type", "library_name"], registry=registry)
    added_ts = Gauge("audiobookshelf_recent_added_timestamp_seconds", "Added timestamp for recently added library items as Unix seconds", ["item_id", "title", "author", "media_type", "library_name"], registry=registry)

    scrape_summary = Summary("audiobookshelf_scrape_duration_seconds", "Duration of Audiobookshelf exporter scrape in seconds", registry=registry)

    latest_seen = db.query(func.max(ActivitySession.last_seen_at)).scalar() or db.query(func.max(ListeningHistory.imported_at)).scalar()
    up.set(1)
    last_success.set(1)
    last_ts.set(unix_seconds(latest_seen) or unix_seconds(utcnow()))
    open_total.set(active_sessions_query(db).count())
    users_total.set(db.query(AbsUser).count())
    sessions_total.set(db.query(ListeningHistory).count())
    scrape_summary.observe(0)

    for user, seconds, count in (
        db.query(ListeningHistory.username, func.coalesce(func.sum(ListeningHistory.duration_seconds), 0), func.count(ListeningHistory.id))
        .group_by(ListeningHistory.username)
        .all()
    ):
        user = user or "unknown"
        user_seconds.labels(user=user).set(seconds or 0)
        user_sessions.labels(user=user).set(count or 0)

    for media_type, title, seconds in (
        db.query(ListeningHistory.media_type, ListeningHistory.title, func.coalesce(func.sum(ListeningHistory.duration_seconds), 0))
        .group_by(ListeningHistory.media_type, ListeningHistory.title)
        .all()
    ):
        book_seconds.labels(media_type=media_type or "unknown", title=title or "Unknown").set(seconds or 0)

    for client, model, seconds in (
        db.query(ListeningHistory.client, ListeningHistory.model, func.coalesce(func.sum(ListeningHistory.duration_seconds), 0))
        .group_by(ListeningHistory.client, ListeningHistory.model)
        .all()
    ):
        device_seconds.labels(client=client or "unknown", model=model or "unknown").set(seconds or 0)

    weekday_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    buckets = {name: 0 for name in weekday_names}
    for started_at, seconds in db.query(ListeningHistory.started_at, ListeningHistory.duration_seconds).all():
        if started_at:
            buckets[weekday_names[started_at.weekday()]] += seconds or 0
    for day, seconds in buckets.items():
        weekday_seconds.labels(day=day).set(seconds)

    for library_id, library_name, seconds, count in (
        db.query(ListeningHistory.library_id, ListeningHistory.library_name, func.coalesce(func.sum(ListeningHistory.duration_seconds), 0), func.count(ListeningHistory.id))
        .group_by(ListeningHistory.library_id, ListeningHistory.library_name)
        .all()
    ):
        library_seconds.labels(library_id=library_id or "unknown", library_name=library_name or "unknown").set(seconds or 0)
        library_sessions.labels(library_id=library_id or "unknown", library_name=library_name or "unknown").set(count or 0)

    for library in db.query(Library).all():
        library_items.labels(library_id=library.abs_library_id or "unknown", library_name=library.name or "unknown").set(library.item_count or 0)

    for row in active_sessions_query(db).order_by(desc(ActivitySession.last_seen_at)).all():
        labels = {
            "session_id": row.session_key or "unknown",
            "user": row.username or "unknown",
            "title": row.title or "Unknown",
            "client": row.client or "unknown",
            "model": row.model or "unknown",
        }
        open_info.labels(
            session_id=row.session_key or "unknown",
            user_id=row.abs_user_id or "unknown",
            user=row.username or "unknown",
            title=row.title or "Unknown",
            author=row.author or "unknown",
            media_type=row.media_type or "unknown",
            library_id=row.library_id or "unknown",
            library_name=row.library_name or "unknown",
            client=row.client or "unknown",
            model=row.model or "unknown",
            device_name=row.device_name or row.device or "unknown",
        ).set(1)
        open_current.labels(**labels).set(row.current_time or 0)
        open_duration.labels(**labels).set(row.duration or 0)
        open_progress.labels(**labels).set(row.progress or 0)
        open_started.labels(**labels).set(unix_seconds(row.started_at))
        open_updated.labels(**labels).set(unix_seconds(row.updated_at or row.last_seen_at))
        open_listening.labels(**labels).set(row.time_listening or 0)

    for row in db.query(ListeningHistory).order_by(desc(ListeningHistory.started_at)).limit(25).all():
        labels = {
            "session_id": row.abs_session_id or "unknown",
            "user": row.username or "unknown",
            "title": row.title or "Unknown",
            "client": row.client or "unknown",
            "model": row.model or "unknown",
        }
        date = row.started_at.strftime("%Y-%m-%d") if row.started_at else "unknown"
        day = row.started_at.strftime("%A") if row.started_at else "unknown"
        recent_info.labels(
            session_id=row.abs_session_id or "unknown",
            user_id=row.abs_user_id or "unknown",
            user=row.username or "unknown",
            title=row.title or "Unknown",
            author=row.author or "unknown",
            media_type=row.media_type or "unknown",
            library_id=row.library_id or "unknown",
            library_name=row.library_name or "unknown",
            client=row.client or "unknown",
            model=row.model or "unknown",
            device_name=row.device_name or row.device or "unknown",
            date=date,
            day=day,
        ).set(1)
        recent_listening.labels(**labels).set(row.duration_seconds or 0)
        recent_progress.labels(**labels).set(row.progress or 0)
        recent_started.labels(**labels).set(unix_seconds(row.started_at))
        recent_updated.labels(**labels).set(unix_seconds(row.updated_at))

    for item in db.query(MediaItem).order_by(desc(MediaItem.added_at)).limit(25).all():
        added_info.labels(
            item_id=item.abs_item_id or "unknown",
            title=item.title or "Unknown",
            author=item.author or "unknown",
            media_type=item.media_type or "unknown",
            library_id=item.library_id or "unknown",
            library_name=item.library_name or "unknown",
            series=item.series or "unknown",
            year=item.year or "unknown",
        ).set(1)
        item_labels = {
            "item_id": item.abs_item_id or "unknown",
            "title": item.title or "Unknown",
            "author": item.author or "unknown",
            "media_type": item.media_type or "unknown",
            "library_name": item.library_name or "unknown",
        }
        added_duration.labels(**item_labels).set(item.duration or 0)
        added_size.labels(**item_labels).set(item.size_bytes or 0)
        added_ts.labels(**item_labels).set(unix_seconds(item.added_at))

    return Response(generate_latest(registry), media_type=CONTENT_TYPE_LATEST)
