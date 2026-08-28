import json
import logging
import re
import string
import unicodedata
from urllib.parse import quote
from sqlalchemy.orm import Session

from absulli.core.config import Settings
from absulli.core.security import notification_cover_token
import absulli.core.setup_state as setup_state
from absulli.database.models import NotificationDelivery, NotificationEvent
from absulli.notifiers.agents import DiscordAgent, EmailAgent, GotifyAgent, NtfyAgent, PushbulletAgent, PushoverAgent, SlackAgent, TelegramAgent, WebhookAgent

log = logging.getLogger(__name__)

NOTIFICATION_EVENT_DEFAULTS = {
    "playback_start": False,
    "playback_stop": False,
    "abs_connection_failed": False,
    "abs_connection_restored": False,
    "new_book": False,
    "new_podcast": False,
    "new_podcast_episode": False,
}

NOTIFICATION_EVENT_SETTINGS = {
    "playback_start": "notify_playback_started",
    "playback_stop": "notify_playback_stopped",
    "abs_connection_failed": "notify_abs_connection_failed",
    "abs_connection_restored": "notify_abs_connection_restored",
    "new_book": "notify_new_book",
    "new_podcast": "notify_new_podcast",
    "new_podcast_episode": "notify_new_podcast_episode",
}

LIBRARY_SCOPED_EVENT_TYPES = {
    "playback_start",
    "playback_stop",
    "new_book",
    "new_podcast",
    "new_podcast_episode",
}

NOTIFICATION_TEMPLATE_VARIABLES = {
    "event_type",
    "notification_title",
    "notification_body",
    "title",
    "author",
    "series",
    "narrator",
    "subtitle",
    "publisher",
    "description",
    "description_full",
    "isbn",
    "asin",
    "language",
    "year",
    "library",
    "username",
    "media_type",
    "podcast",
    "episode",
    "podcast_description",
    "podcast_description_full",
    "episode_description",
    "episode_description_full",
    "itunes_id",
    "isbn_url",
    "item_id",
    "audiobookshelf_url",
    "cover_url",
    "audible_url",
    "apple_podcasts_url",
}


def validate_notification_template(value: str) -> str:
    template = str(value or "")
    try:
        parsed = list(string.Formatter().parse(template))
    except ValueError as exc:
        raise ValueError("Notification template has invalid braces") from exc
    for _literal, field_name, format_spec, conversion in parsed:
        if field_name is None:
            continue
        if field_name not in NOTIFICATION_TEMPLATE_VARIABLES:
            raise ValueError(f"Unknown notification variable: {{{field_name}}}")
        if format_spec or conversion:
            raise ValueError("Notification variables do not support formatting options")
    return template


def render_notification_template(template: str, values: dict[str, object]) -> str:
    validated = validate_notification_template(template)
    safe_values = {name: str(values.get(name) or "") for name in NOTIFICATION_TEMPLATE_VARIABLES}
    return validated.format_map(safe_values)



WEBHOOK_DEFAULT_PAYLOAD = json.dumps({
    "event": "{event_type}",
    "title": "{notification_title}",
    "message": "{notification_body}",
    "user": {"username": "{username}"},
    "media": {
        "type": "{media_type}",
        "title": "{title}",
        "subtitle": "{subtitle}",
        "author": "{author}",
        "narrator": "{narrator}",
        "series": "{series}",
        "publisher": "{publisher}",
        "year": "{year}",
        "language": "{language}",
        "description": "{description}",
        "library": "{library}",
        "item_id": "{item_id}"
    },
    "podcast": {
        "title": "{podcast}",
        "episode": "{episode}",
        "description": "{podcast_description}",
        "episode_description": "{episode_description}",
        "itunes_id": "{itunes_id}"
    },
    "ids": {"isbn": "{isbn}", "asin": "{asin}"},
    "links": {
        "audiobookshelf": "{audiobookshelf_url}",
        "cover": "{cover_url}",
        "audible": "{audible_url}",
        "libro_fm": "{isbn_url}",
        "apple_podcasts": "{apple_podcasts_url}"
    }
}, indent=2)

NOTIFICATION_AGENT_IDS = (
    "email",
    "discord",
    "gotify",
    "ntfy",
    "pushbullet",
    "pushover",
    "slack",
    "telegram",
    "webhook",
)


def _bool_value(value: object) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _libro_url(isbn: str, title: str) -> str:
    if not isbn:
        return ""
    normalized_title = unicodedata.normalize("NFKD", title or "").encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", normalized_title.lower()).strip("-")
    encoded_isbn = quote(isbn, safe="")
    if slug:
        return f"https://libro.fm/audiobooks/{encoded_isbn}-{slug}"
    return f"https://libro.fm/referral?isbn={encoded_isbn}"


def _notification_preview(value: object, limit: int = 280) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    shortened = text[: limit + 1]
    if " " in shortened:
        shortened = shortened.rsplit(" ", 1)[0]
    return shortened.rstrip(" ,;:-") + "..."




def _webhook_json_tokens(value: str) -> set[str]:
    return set(re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", str(value or "")))


def validate_webhook_json_template(value: str) -> str:
    template = str(value or "").strip()
    if not template:
        return ""
    unknown = sorted(_webhook_json_tokens(template) - NOTIFICATION_TEMPLATE_VARIABLES)
    if unknown:
        raise ValueError(f"Unknown notification variable: {{{unknown[0]}}}")
    probes = (
        {name: "value" for name in NOTIFICATION_TEMPLATE_VARIABLES},
        {name: "" for name in NOTIFICATION_TEMPLATE_VARIABLES},
    )
    for values in probes:
        rendered = render_webhook_json_template(template, values)
        try:
            json.loads(rendered)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Webhook payload template must render valid JSON: {exc.msg}") from exc
    return template


def render_webhook_json_template(template: str, values: dict[str, object]) -> str:
    source = str(template or "")
    unknown = sorted(_webhook_json_tokens(source) - NOTIFICATION_TEMPLATE_VARIABLES)
    if unknown:
        raise ValueError(f"Unknown notification variable: {{{unknown[0]}}}")

    def replace(match: re.Match[str]) -> str:
        value = str(values.get(match.group(1)) or "")
        return json.dumps(value, ensure_ascii=False)[1:-1]

    return re.sub(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", replace, source)


def render_webhook_payload(template: str, values: dict[str, object]) -> object:
    rendered = render_webhook_json_template(template, values)
    return json.loads(rendered)


def webhook_headers_from_values(
    authorization: str = "",
    custom_headers_json: str = "",
    legacy_headers_json: str = "",
) -> dict[str, str]:
    headers: dict[str, str] = {}
    for source in (legacy_headers_json, custom_headers_json):
        try:
            parsed = json.loads(source) if source else {}
        except (TypeError, ValueError):
            parsed = {}
        if not isinstance(parsed, dict):
            continue
        for key, value in parsed.items():
            name = str(key)
            existing = next((item for item in headers if item.lower() == name.lower()), None)
            if existing is not None:
                headers.pop(existing)
            headers[name] = str(value)

    authorization = str(authorization or "").strip()
    if authorization:
        existing = next((item for item in headers if item.lower() == "authorization"), None)
        if existing is not None:
            headers.pop(existing)
        headers["Authorization"] = authorization
    return headers


def clean_notification_error(value: object) -> str:
    error = str(value or "").strip()
    if not error:
        return ""
    first_line = error.splitlines()[0].strip()
    first_line = re.sub(r"""\s+for url ['"].*?['"]\s*$""", "", first_line, flags=re.IGNORECASE)
    first_line = re.sub(r"https?://\S+", "[redacted URL]", first_line)
    if len(first_line) > 180:
        first_line = first_line[:177].rstrip() + "..."
    return first_line


def agent_event_enabled(agent_id: str, event_type: str) -> bool:
    setting_name = NOTIFICATION_EVENT_SETTINGS.get(event_type)
    if not setting_name:
        return True
    value = setup_state.get_setup_setting(f"{agent_id}_{setting_name}", "")
    if value == "":
        value = setup_state.get_setup_setting(setting_name, "")
    if value == "":
        return NOTIFICATION_EVENT_DEFAULTS.get(event_type, True)
    return _bool_value(value)


def agent_library_enabled(agent_id: str, event_type: str, library_id: str = "") -> bool:
    if event_type not in LIBRARY_SCOPED_EVENT_TYPES:
        return True
    value = setup_state.get_setup_setting(f"{agent_id}_notification_libraries", "")
    if value in {"", "*"}:
        return True
    try:
        selected = json.loads(value)
    except (TypeError, ValueError):
        return True
    if not isinstance(selected, list):
        return True
    library_id = str(library_id or "").strip()
    if not library_id:
        return False
    return library_id in {str(item) for item in selected if item}


def event_enabled(event_type: str) -> bool:
    setting_name = NOTIFICATION_EVENT_SETTINGS.get(event_type)
    if not setting_name:
        return True
    return any(
        _bool_value(setup_state.get_setup_setting(f"{agent_id}_enabled", "true"))
        and agent_event_enabled(agent_id, event_type)
        for agent_id in NOTIFICATION_AGENT_IDS
    )


class NotificationManager:
    def __init__(self, settings: Settings):
        self.settings = settings

    def named_agents(self):
        agents = []

        gotify_url = self.settings.effective_gotify_url
        gotify_token = self.settings.effective_gotify_token
        if gotify_url and gotify_token:
            agents.append(("gotify", GotifyAgent(gotify_url, gotify_token)))

        ntfy_url = self.settings.effective_setting("ntfy_url").rstrip("/")
        if ntfy_url:
            agents.append(("ntfy", NtfyAgent(ntfy_url, self.settings.effective_setting("ntfy_token"))))

        discord_webhook_url = self.settings.effective_setting("discord_webhook_url")
        if discord_webhook_url:
            agents.append(("discord", DiscordAgent(discord_webhook_url)))

        slack_webhook_url = self.settings.effective_setting("slack_webhook_url")
        if slack_webhook_url:
            agents.append(("slack", SlackAgent(slack_webhook_url)))

        telegram_bot_token = self.settings.effective_setting("telegram_bot_token")
        telegram_chat_id = self.settings.effective_setting("telegram_chat_id")
        if telegram_bot_token and telegram_chat_id:
            agents.append(("telegram", TelegramAgent(telegram_bot_token, telegram_chat_id)))

        pushover_app_token = self.settings.effective_setting("pushover_app_token")
        pushover_user_key = self.settings.effective_setting("pushover_user_key")
        if pushover_app_token and pushover_user_key:
            agents.append(("pushover", PushoverAgent(pushover_app_token, pushover_user_key)))

        pushbullet_token = self.settings.effective_setting("pushbullet_token")
        if pushbullet_token:
            agents.append(("pushbullet", PushbulletAgent(pushbullet_token)))

        webhook_url = self.settings.effective_setting("webhook_url")
        if webhook_url:
            webhook_headers = webhook_headers_from_values(
                authorization=setup_state.get_setup_setting("webhook_authorization_header", ""),
                custom_headers_json=setup_state.get_setup_setting("webhook_custom_headers_json", ""),
                legacy_headers_json=setup_state.get_setup_setting("webhook_headers_json", ""),
            )
            agents.append(("webhook", WebhookAgent(webhook_url, webhook_headers)))

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
                ("email", EmailAgent(
                    smtp_host=email_smtp_host,
                    smtp_port=smtp_port,
                    sender=email_from,
                    recipient=email_to,
                    username=self.settings.effective_setting("email_smtp_username"),
                    password=self.settings.effective_setting("email_smtp_password"),
                    use_tls=self.settings.effective_bool_setting("email_use_tls", default=True),
                    use_starttls=self.settings.effective_bool_setting("email_use_starttls", default=False),
                ))
            )

        return [
            (agent_id, agent)
            for agent_id, agent in agents
            if _bool_value(setup_state.get_setup_setting(f"{agent_id}_enabled", "true"))
        ]

    def agents(self):
        return [agent for _agent_id, agent in self.named_agents()]

    async def notify(
        self,
        db: Session,
        event_type: str,
        title: str,
        body: str,
        library_id: str = "",
        context: dict[str, object] | None = None,
    ) -> None:
        agents = [
            (agent_id, agent)
            for agent_id, agent in self.named_agents()
            if agent_event_enabled(agent_id, event_type)
            and agent_library_enabled(agent_id, event_type, library_id)
        ]
        if not agents:
            return

        event = NotificationEvent(event_type=event_type, title=title, body=body)
        db.add(event)
        db.commit()
        delivered = False
        base_extra = {"event_type": event_type, **(context or {})}
        public_url = self.settings.effective_setting("public_url").rstrip("/")
        abs_url = self.settings.effective_abs_url.rstrip("/")
        for agent_id, agent in agents:
            delivery = NotificationDelivery(event_id=event.id, agent=agent_id, delivered=False, error="")
            extra = dict(base_extra)
            item_id = str(extra.get("item_id") or "").strip()
            cover_url = ""
            audiobookshelf_url = ""
            asin = str(extra.get("asin") or "").strip()
            itunes_id = str(extra.get("itunes_id") or "").strip()
            isbn = str(extra.get("isbn") or "").strip()
            audible_url = f"https://www.audible.com/pd/{quote(asin, safe='')}" if asin else ""
            isbn_url = _libro_url(isbn, str(extra.get("title") or "").strip())
            apple_podcasts_url = f"https://podcasts.apple.com/podcast/id{quote(itunes_id, safe='')}" if itunes_id else ""
            if item_id and public_url:
                token = notification_cover_token(item_id)
                encoded_item_id = quote(item_id, safe="")
                cover_url = f"{public_url}/notification-covers/items/{encoded_item_id}?width=900&token={token}"
            if item_id and abs_url:
                audiobookshelf_url = f"{abs_url}/item/{quote(item_id, safe='')}"
            description_full = str(extra.get("description") or "").strip()
            podcast_description_full = str(extra.get("podcast_description") or "").strip()
            episode_description_full = str(extra.get("episode_description") or "").strip()
            template_values = {
                **extra,
                "description": _notification_preview(description_full),
                "description_full": description_full,
                "podcast_description": _notification_preview(podcast_description_full),
                "podcast_description_full": podcast_description_full,
                "episode_description": _notification_preview(episode_description_full),
                "episode_description_full": episode_description_full,
                "library": extra.get("library") or extra.get("library_name") or "",
                "podcast": extra.get("podcast") or extra.get("podcast_title") or "",
                "episode": extra.get("episode") or extra.get("episode_title") or "",
                "notification_title": title,
                "notification_body": body,
                "audiobookshelf_url": audiobookshelf_url,
                "cover_url": cover_url,
                "audible_url": audible_url,
                "isbn_url": isbn_url,
                "apple_podcasts_url": apple_podcasts_url,
            }
            if agent_id in {"gotify", "discord"}:
                if asin and audible_url:
                    template_values["asin"] = f"[{asin}]({audible_url})"
                if isbn and isbn_url:
                    template_values["isbn"] = f"[{isbn}]({isbn_url})"
            rendered_title = title
            rendered_body = body
            if agent_id != "webhook":
                title_template = setup_state.get_setup_setting(f"{agent_id}_{event_type}_title_template", "")
                body_template = setup_state.get_setup_setting(f"{agent_id}_{event_type}_body_template", "")
                if title_template:
                    rendered_title = render_notification_template(title_template, template_values)
                if body_template:
                    rendered_body = render_notification_template(body_template, template_values)
            if agent_id == "gotify":
                if cover_url and self.settings.effective_bool_setting("gotify_include_cover_art", default=False):
                    extra["cover_url"] = cover_url
                if audiobookshelf_url and self.settings.effective_bool_setting("gotify_open_in_audiobookshelf", default=False):
                    extra["click_url"] = audiobookshelf_url
            if agent_id == "discord":
                if cover_url and self.settings.effective_bool_setting("discord_include_cover_art", default=False):
                    extra["cover_url"] = cover_url
                if audiobookshelf_url and self.settings.effective_bool_setting("discord_open_in_audiobookshelf", default=False):
                    extra["click_url"] = audiobookshelf_url
            if agent_id == "pushbullet":
                if audiobookshelf_url and self.settings.effective_bool_setting("pushbullet_open_in_audiobookshelf", default=False):
                    extra["click_url"] = audiobookshelf_url
            if agent_id == "webhook":
                payload_template = setup_state.get_setup_setting("webhook_payload_template", "").strip() or WEBHOOK_DEFAULT_PAYLOAD
                extra["webhook_payload"] = render_webhook_payload(payload_template, template_values)
            try:
                await agent.send(rendered_title, rendered_body, extra)
                delivery.delivered = True
                delivered = True
            except Exception as exc:
                delivery.error = clean_notification_error(exc)
                log.warning("Notification agent failed: %s", clean_notification_error(exc))
            db.add(delivery)
        event.delivered = delivered
        db.commit()
