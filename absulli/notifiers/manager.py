import logging
from sqlalchemy.orm import Session

from absulli.core.config import Settings
from absulli.database.models import NotificationEvent
from absulli.notifiers.agents import GotifyAgent, WebhookAgent

log = logging.getLogger(__name__)


class NotificationManager:
    def __init__(self, settings: Settings):
        self.agents = []
        if settings.gotify_url and settings.gotify_token:
            self.agents.append(GotifyAgent(settings.gotify_url, settings.gotify_token))
        if settings.webhook_url:
            self.agents.append(WebhookAgent(settings.webhook_url))

    async def notify(self, db: Session, event_type: str, title: str, body: str) -> None:
        event = NotificationEvent(event_type=event_type, title=title, body=body)
        db.add(event)
        db.commit()
        delivered = False
        for agent in self.agents:
            try:
                await agent.send(title, body, {"event_type": event_type})
                delivered = True
            except Exception as exc:
                log.warning("Notification agent failed: %s", exc)
        event.delivered = delivered
        db.commit()
