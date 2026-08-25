import logging
from datetime import timedelta

from sqlalchemy.orm import Session

from absulli.core.config import get_settings
from absulli.database.models import ActivitySession, Library, MediaItem
from absulli.http.abs_client import AudiobookshelfClient
from absulli.http.normalizers import normalize_item_notification_context, normalize_online_payload, normalize_podcast_playback_context, utcnow
from absulli.notifiers.manager import NotificationManager
from absulli.monitors.utils import friendly_names

log = logging.getLogger(__name__)


class ActivityMonitor:
    def __init__(self, client: AudiobookshelfClient, notifier: NotificationManager):
        self.client = client
        self.notifier = notifier


    def _library_names(self, db: Session) -> dict[str, str]:
        return {library.abs_library_id: (library.name or library.abs_library_id) for library in db.query(Library).all()}

    def _enrich_sessions(self, db: Session, sessions: list[dict]) -> None:
        item_ids = {session.get("abs_item_id") for session in sessions if session.get("abs_item_id")}
        media_items = {
            item.abs_item_id: item
            for item in db.query(MediaItem).filter(MediaItem.abs_item_id.in_(item_ids)).all()
        } if item_ids else {}
        library_names = self._library_names(db)

        for session in sessions:
            item = media_items.get(session.get("abs_item_id"))
            if item:
                session["library_id"] = session.get("library_id") or item.library_id or ""
                session["library_name"] = session.get("library_name") or item.library_name or ""
                session["author"] = session.get("author") or item.author or ""
                if session.get("media_type") in ("", "unknown") and item.media_type:
                    session["media_type"] = item.media_type
                else:
                    session["media_type"] = session.get("media_type") or "unknown"
                if session.get("title") in ("", "Unknown") and item.title:
                    session["title"] = item.title

            library_id = session.get("library_id") or ""
            if not session.get("library_name") and library_id in library_names:
                session["library_name"] = library_names[library_id]

    async def _notification_context(self, session: dict) -> dict[str, object]:
        context = {
            "item_id": session.get("abs_item_id") or "",
            "title": session.get("title") or "",
            "author": session.get("author") or "",
            "library_name": session.get("library_name") or "",
            "username": session.get("username") or "",
            "media_type": session.get("media_type") or "",
        }
        item_id = str(context["item_id"] or "").strip()
        if not item_id:
            return context
        try:
            payload = await self.client.get_item(item_id, expanded=True)
        except Exception as exc:
            log.debug("Playback notification metadata lookup failed for %s: %s", item_id, exc)
            return context
        if str(context.get("media_type") or "").lower() == "podcast":
            metadata = normalize_podcast_playback_context(
                payload,
                episode_title=str(session.get("title") or ""),
                episode_id=str(session.get("episode_id") or ""),
            )
        else:
            metadata = normalize_item_notification_context(payload)
        for key, value in metadata.items():
            if value:
                context[key] = value
        return context

    async def poll(self, db: Session) -> int:
        payload = await self.client.get_online_users()
        sessions = normalize_online_payload(payload)
        usernames = friendly_names(db)

        for session in sessions:
            if session["abs_user_id"] in usernames:
                session["username"] = usernames[session["abs_user_id"]]

        self._enrich_sessions(db, sessions)

        active_keys = {session["session_key"] for session in sessions}
        now = utcnow()
        existing_sessions = {
            row.session_key: row
            for row in (
                db.query(ActivitySession)
                .filter(ActivitySession.session_key.in_(active_keys))
                .all()
                if active_keys
                else []
            )
        }

        for session in sessions:
            existing = existing_sessions.get(session["session_key"])
            was_active = bool(existing and existing.is_active)
            if existing:
                for key, value in session.items():
                    setattr(existing, key, value)
                existing.is_active = True
                existing.last_seen_at = now
            else:
                existing = ActivitySession(is_active=True, **session)
                db.add(existing)

            if not was_active:
                await self.notifier.notify(
                    db,
                    "playback_start",
                    f"{session['username']} started listening",
                    session["title"],
                    library_id=session.get("library_id") or "",
                    context=await self._notification_context(session),
                )

        stale_seconds = max(45, get_settings().effective_abs_poll_interval * 3)
        stale_cutoff = now - timedelta(seconds=stale_seconds)
        stale_query = db.query(ActivitySession).filter(
            ActivitySession.is_active.is_(True),
            ActivitySession.last_seen_at < stale_cutoff,
        )
        if active_keys:
            stale_query = stale_query.filter(~ActivitySession.session_key.in_(active_keys))

        for row in stale_query.all():
            row.is_active = False
            await self.notifier.notify(
                db,
                "playback_stop",
                f"{row.username} stopped listening",
                row.title,
                library_id=row.library_id or "",
                context=await self._notification_context({
                    "abs_item_id": row.abs_item_id or "",
                    "title": row.title or "",
                    "author": row.author or "",
                    "library_name": row.library_name or "",
                    "username": row.username or "",
                    "media_type": row.media_type or "",
                }),
            )

        db.commit()
        return len(sessions)
