import logging
from datetime import timedelta

from sqlalchemy.orm import Session

from absulli.database.models import ActivitySession, Library, MediaItem
from absulli.http.abs_client import AudiobookshelfClient
from absulli.http.normalizers import normalize_online_payload, utcnow
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
                session["media_type"] = session.get("media_type") or item.media_type or "unknown"
                if session.get("title") in ("", "Unknown") and item.title:
                    session["title"] = item.title

            library_id = session.get("library_id") or ""
            if not session.get("library_name") and library_id in library_names:
                session["library_name"] = library_names[library_id]

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

        for session in sessions:
            existing = db.query(ActivitySession).filter_by(session_key=session["session_key"]).first()
            if existing:
                for key, value in session.items():
                    setattr(existing, key, value)
                existing.is_active = True
                existing.last_seen_at = now
            else:
                existing = ActivitySession(is_active=True, **session)
                db.add(existing)
                await self.notifier.notify(
                    db,
                    "playback_start",
                    f"{session['username']} started listening",
                    session["title"],
                )

        stale_cutoff = now - timedelta(seconds=45)
        stale = db.query(ActivitySession).filter(ActivitySession.is_active.is_(True)).all()
        for row in stale:
            if row.session_key not in active_keys and row.last_seen_at < stale_cutoff:
                row.is_active = False
                await self.notifier.notify(
                    db,
                    "playback_stop",
                    f"{row.username} stopped listening",
                    row.title,
                )

        db.commit()
        return len(sessions)
