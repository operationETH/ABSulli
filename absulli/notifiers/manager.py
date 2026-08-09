import logging
from sqlalchemy.orm import Session

from absulli.core.config import Settings
import absulli.core.setup_state as setup_state
from absulli.database.models import NotificationEvent
from absulli.notifiers.agents import DiscordAgent, EmailAgent, GotifyAgent, NtfyAgent, PushbulletAgent, PushoverAgent, SlackAgent, TelegramAgent, WebhookAgent

log = logging.getLogger(__name__)

NOTIFICATION_EVENT_DEFAULTS = {
    "playback_start": False,
    "playback_stop": False,
    "abs_connection_failed": False,
    "abs_connection_restored": False,
    "new_book": False,
    "new_podcast": False,
}

NOTIFICATION_EVENT_SETTINGS = {
    "playback_start": "notify_playback_started",
    "playback_stop": "notify_playback_stopped",
    "abs_connection_failed": "notify_abs_connection_failed",
    "abs_connection_restored": "notify_abs_connection_restored",
    "new_book": "notify_new_book",
    "new_podcast": "notify_new_podcast",
}


def event_enabled(event_type: str) -> bool:
    setting_name = NOTIFICATION_EVENT_SETTINGS.get(event_type)
    if not setting_name:
        return True
    value = setup_state.get_setup_setting(setting_name, "")
    if value == "":
        return NOTIFICATION_EVENT_DEFAULTS.get(event_type, True)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


class NotificationManager:
    def __init__(self, settings: Settings):
        self.settings = settings

    def agents(self):
        agents = []

        gotify_url = self.settings.effective_gotify_url
        gotify_token = self.settings.effective_gotify_token
        if gotify_url and gotify_token:
            agents.append(GotifyAgent(gotify_url, gotify_token))

        ntfy_url = self.settings.effective_setting("ntfy_url").rstrip("/")
        if ntfy_url:
            agents.append(NtfyAgent(ntfy_url, self.settings.effective_setting("ntfy_token")))

        discord_webhook_url = self.settings.effective_setting("discord_webhook_url")
        if discord_webhook_url:
            agents.append(DiscordAgent(discord_webhook_url))

        slack_webhook_url = self.settings.effective_setting("slack_webhook_url")
        if slack_webhook_url:
            agents.append(SlackAgent(slack_webhook_url))

        telegram_bot_token = self.settings.effective_setting("telegram_bot_token")
        telegram_chat_id = self.settings.effective_setting("telegram_chat_id")
        if telegram_bot_token and telegram_chat_id:
            agents.append(TelegramAgent(telegram_bot_token, telegram_chat_id))

        pushover_app_token = self.settings.effective_setting("pushover_app_token")
        pushover_user_key = self.settings.effective_setting("pushover_user_key")
        if pushover_app_token and pushover_user_key:
            agents.append(PushoverAgent(pushover_app_token, pushover_user_key))

        pushbullet_token = self.settings.effective_setting("pushbullet_token")
        if pushbullet_token:
            agents.append(PushbulletAgent(pushbullet_token))

        webhook_url = self.settings.effective_setting("webhook_url")
        if webhook_url:
            agents.append(WebhookAgent(webhook_url))

        email_smtp_host = self.settings.effective_setting("email_smtp_host")
        email_from = self.settings.effective_setting("email_from")
        email_to = self.settings.effective_setting("email_to")
        if email_smtp_host and email_from and email_to:
            port_value = self.settings.effective_setting("email_smtp_port") or str(self.settings.email_smtp_port)
            try:
                smtp_port = int(port_value)
            except ValueError:
                smtp_port = 465
            agents.append(
                EmailAgent(
                    smtp_host=email_smtp_host,
                    smtp_port=smtp_port,
                    sender=email_from,
                    recipient=email_to,
                    username=self.settings.effective_setting("email_smtp_username"),
                    password=self.settings.effective_setting("email_smtp_password"),
                    use_tls=self.settings.effective_bool_setting("email_use_tls", default=True),
                )
            )

        return agents

    async def notify(self, db: Session, event_type: str, title: str, body: str) -> None:
        if not event_enabled(event_type):
            return

        event = NotificationEvent(event_type=event_type, title=title, body=body)
        db.add(event)
        db.commit()
        delivered = False
        for agent in self.agents():
            try:
                await agent.send(title, body, {"event_type": event_type})
                delivered = True
            except Exception as exc:
                log.warning("Notification agent failed: %s", exc)
        event.delivered = delivered
        db.commit()
