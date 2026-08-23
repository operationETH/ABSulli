import json
import logging
import os
import secrets
import sys
from urllib.parse import quote

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from absulli import __version__
import absulli.core.setup_state as setup_state
from absulli.core.security import auth_username, auth_password_hash, password_hash, verify_login
from absulli.database.models import AbsUser, ActivitySession, Library, ListeningHistory, MediaItem, NotificationEvent
from absulli.notifiers.agents import (
    DiscordAgent,
    EmailAgent,
    GotifyAgent,
    NtfyAgent,
    PushbulletAgent,
    PushoverAgent,
    SlackAgent,
    TelegramAgent,
    WebhookAgent,
)
from absulli.notifiers.manager import WEBHOOK_DEFAULT_PAYLOAD

log = logging.getLogger(__name__)

HISTORY_PAGE_SIZE_OPTIONS = (10, 25, 50, 100)
HISTORY_PAGE_SIZE_SETTING = "history_page_size"
DEFAULT_HISTORY_PAGE_SIZE = 25

SETTINGS_TAB_IDS = ("general", "network", "users", "notifications", "api", "about")


def clean_settings_tab(value: object) -> str:
    tab = str(value or "general").strip().lower()
    return tab if tab in SETTINGS_TAB_IDS else "general"


def settings_tab_context(active_tab: str) -> list[dict[str, str]]:
    labels = {
        "general": "General",
        "network": "Network",
        "users": "Users",
        "notifications": "Notifications",
        "api": "API",
        "about": "About",
    }
    return [{"id": tab_id, "label": labels[tab_id]} for tab_id in SETTINGS_TAB_IDS]


def bool_label(value: bool) -> str:
    return "True" if value else "False"


def clean_history_page_size(value: object, default: int = DEFAULT_HISTORY_PAGE_SIZE) -> int:
    try:
        size = int(str(value or "").strip())
    except (TypeError, ValueError):
        return default
    return size if size in HISTORY_PAGE_SIZE_OPTIONS else default


def history_page_size(request: Request) -> int:
    requested = request.query_params.get("limit")
    if requested is not None:
        size = clean_history_page_size(requested, default=0)
        if size in HISTORY_PAGE_SIZE_OPTIONS:
            current = setup_state.get_setup_setting(HISTORY_PAGE_SIZE_SETTING, "")
            if current != str(size):
                setup_state.set_setup_setting(HISTORY_PAGE_SIZE_SETTING, str(size))
            return size

    return clean_history_page_size(
        setup_state.get_setup_setting(HISTORY_PAGE_SIZE_SETTING, str(DEFAULT_HISTORY_PAGE_SIZE)),
        default=DEFAULT_HISTORY_PAGE_SIZE,
    )


def history_page_size_context(limit: int) -> dict[str, object]:
    return {
        "history_page_size": limit,
        "history_page_size_options": HISTORY_PAGE_SIZE_OPTIONS,
    }


def clean_http_url(value: str, label: str) -> str:
    url = (value or "").strip().rstrip("/")
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"{label} must start with http:// or https://")
    return url


def clean_gotify_url(value: str) -> str:
    return clean_http_url(value, "Gotify URL")


def clean_agent_value(value: str) -> str:
    return str(value or "").strip()


NOTIFICATION_EVENT_SETTINGS = {
    "playback_start": {
        "setting": "notify_playback_started",
        "label": "Playback Started",
        "description": "Send when a user starts listening.",
        "default": False,
    },
    "playback_stop": {
        "setting": "notify_playback_stopped",
        "label": "Playback Stopped",
        "description": "Send when a user stops listening.",
        "default": False,
    },
    "abs_connection_failed": {
        "setting": "notify_abs_connection_failed",
        "label": "Audiobookshelf Connection Failed",
        "description": "Send when ABSulli cannot reach Audiobookshelf.",
        "default": False,
    },
    "abs_connection_restored": {
        "setting": "notify_abs_connection_restored",
        "label": "Audiobookshelf Connection Restored",
        "description": "Send when Audiobookshelf starts responding again.",
        "default": False,
    },
    "new_book": {
        "setting": "notify_new_book",
        "label": "New Book Added",
        "description": "Send when ABSulli detects a new book after the initial library scan.",
        "default": False,
    },
    "new_podcast": {
        "setting": "notify_new_podcast",
        "label": "New Podcast Added",
        "description": "Send when ABSulli detects a new podcast after the initial library scan.",
        "default": False,
    },
    "new_podcast_episode": {
        "setting": "notify_new_podcast_episode",
        "label": "New Podcast Episode Added",
        "description": "Send when ABSulli detects a newly downloaded podcast episode after the initial episode scan.",
        "default": False,
    },
}

NOTIFICATION_TEMPLATE_VARIABLE_GROUPS = [
    {
        "label": "Media",
        "variables": [
            ("{author}", "Author"),
            ("{description}", "Short book or current media description"),
            ("{episode}", "Episode title"),
            ("{episode_description}", "Short episode description"),
            ("{language}", "Language"),
            ("{library}", "Library name"),
            ("{media_type}", "Media type"),
            ("{narrator}", "Narrator"),
            ("{podcast}", "Podcast title"),
            ("{podcast_description}", "Short podcast description"),
            ("{publisher}", "Publisher"),
            ("{series}", "Series"),
            ("{subtitle}", "Subtitle"),
            ("{title}", "Media title"),
            ("{year}", "Published year"),
        ],
    },
    {
        "label": "Links and IDs",
        "variables": [
            ("{apple_podcasts_url}", "Apple Podcasts URL"),
            ("{asin}", "Audible ASIN"),
            ("{audiobookshelf_url}", "Direct Audiobookshelf item URL"),
            ("{cover_url}", "Signed cover image URL"),
            ("{isbn}", "ISBN"),
            ("{item_id}", "Audiobookshelf item ID"),
            ("{itunes_id}", "Apple Podcasts iTunes ID"),
        ],
    },
    {
        "label": "Notification",
        "variables": [
            ("{event_type}", "ABSulli event type"),
            ("{notification_body}", "Default notification body"),
            ("{notification_title}", "Default notification title"),
            ("{username}", "User name"),
        ],
    },
]


WEBHOOK_TEMPLATE_VARIABLE_GROUPS = [
    {
        "label": group["label"],
        "variables": list(group["variables"]),
    }
    for group in NOTIFICATION_TEMPLATE_VARIABLE_GROUPS
]
for group in WEBHOOK_TEMPLATE_VARIABLE_GROUPS:
    if group["label"] == "Media":
        group["variables"] = sorted(
            group["variables"]
            + [
                ("{description_full}", "Full media description"),
                ("{episode_description_full}", "Full episode description"),
                ("{podcast_description_full}", "Full podcast description"),
            ],
            key=lambda item: item[0],
        )
    if group["label"] == "Links and IDs":
        group["variables"] = sorted(
            group["variables"]
            + [
                ("{audible_url}", "Audible item URL"),
                ("{isbn_url}", "Libro.fm ISBN URL"),
            ],
            key=lambda item: item[0],
        )


def webhook_payload_template_context() -> str:
    return setup_state.get_setup_setting("webhook_payload_template", "").strip() or WEBHOOK_DEFAULT_PAYLOAD


def notification_templates_context(agent_id: str) -> list[dict[str, object]]:
    templates = []
    for event_type, meta in NOTIFICATION_EVENT_SETTINGS.items():
        templates.append(
            {
                "event_type": event_type,
                "label": meta["label"],
                "title_setting": f"{agent_id}_{event_type}_title_template",
                "body_setting": f"{agent_id}_{event_type}_body_template",
                "title_template": setup_state.get_setup_setting(f"{agent_id}_{event_type}_title_template", ""),
                "body_template": setup_state.get_setup_setting(f"{agent_id}_{event_type}_body_template", ""),
            }
        )
    return templates


def notification_advanced_open(agent_id: str) -> bool:
    if bool_setting(f"{agent_id}_advanced_open", False):
        return True
    library_value = setup_state.get_setup_setting(f"{agent_id}_notification_libraries", "")
    if library_value not in {"", "*"}:
        return True
    if agent_id != "webhook" and any(
        setup_state.get_setup_setting(f"{agent_id}_{event_type}_{part}_template", "").strip()
        for event_type in NOTIFICATION_EVENT_SETTINGS
        for part in ("title", "body")
    ):
        return True
    if agent_id == "webhook":
        if setup_state.get_setup_setting("webhook_authorization_header", "").strip():
            return True
        if setup_state.get_setup_setting("webhook_custom_headers_json", "").strip():
            return True
        payload_template = setup_state.get_setup_setting("webhook_payload_template", "").strip()
        if payload_template and payload_template != WEBHOOK_DEFAULT_PAYLOAD:
            return True
    return False

GENERAL_FIELD_CONFIGS = [
    {"name": "abs_url", "label": "Audiobookshelf URL", "type": "url", "required": True, "placeholder": "http://audiobookshelf:13378"},
    {"name": "abs_api_key", "label": "Audiobookshelf API Key", "type": "password", "required": True, "placeholder": ""},
    {"name": "abs_verify_ssl", "label": "Verify SSL Certificates", "type": "checkbox", "required": False, "default": True},
    {"name": "abs_request_timeout", "label": "Request Timeout", "type": "number", "required": True, "placeholder": "15"},
    {"name": "abs_poll_interval", "label": "Activity Poll Interval", "type": "number", "required": True, "placeholder": "15"},
    {"name": "abs_history_poll_interval", "label": "History Poll Interval", "type": "number", "required": True, "placeholder": "300"},
]

NETWORK_FIELD_CONFIGS = [
    {
        "name": "public_url",
        "label": "ABSulli Public URL",
        "description": "Base URL used for notification cover art. Leave blank to disable cover art in rich notifications.",
        "type": "url",
        "placeholder": "https://absulli.example.com",
    },
    {
        "name": "trust_proxy",
        "label": "Trust Reverse Proxy Headers",
        "description": "Use X-Forwarded-For from your reverse proxy for login rate limits and audit logs.",
        "type": "checkbox",
        "default": False,
    },
    {
        "name": "cookie_secure",
        "label": "HTTPS Secure Cookies",
        "description": "Mark browser cookies as HTTPS-only when ABSulli is served through TLS.",
        "type": "checkbox",
        "default": False,
    },
    {
        "name": "security_hsts_enabled",
        "label": "HSTS Header",
        "description": "Tell browsers to only use HTTPS for this site. Recommended when ABSulli is behind HTTPS.",
        "type": "checkbox",
        "default": False,
        "restart_required": True,
    },
    {
        "name": "metrics_token",
        "label": "Metrics Token",
        "description": "Protect Prometheus /metrics access with a token. Leave blank to disable token authentication.",
        "type": "password",
        "placeholder": "",
    },
    {
        "name": "cors_allowed_origins",
        "label": "CORS Allowed Origins",
        "description": "Allow browser requests from specific external origins. Leave blank to disable CORS. Changes apply immediately.",
        "type": "text",
        "placeholder": "https://absulli.example.com, https://example.com",
    },
]

USER_FIELD_CONFIGS = [
    {"name": "auth_username", "label": "Admin Username", "type": "text", "required": True, "placeholder": "admin", "group": "account"},
    {"name": "current_password", "label": "Current Admin Password", "type": "password", "required": False, "placeholder": "Required to change password", "group": "account"},
    {"name": "auth_password", "label": "New Admin Password", "type": "password", "required": False, "placeholder": "", "group": "account"},
    {"name": "auth_password_confirm", "label": "Confirm New Password", "type": "password", "required": False, "placeholder": "", "group": "account"},
]

AGENT_FIELD_CONFIGS = {
    "email": {
        "label": "Email",
        "icon": "",
        "icon_template": "partials/notification_icons/email.svg",
        "fields": [
            {"name": "email_smtp_host", "label": "SMTP Host", "type": "text", "required": True, "placeholder": "smtp.example.com"},
            {"name": "email_smtp_port", "label": "SMTP Port", "type": "number", "required": True, "placeholder": "465"},
            {"name": "email_from", "label": "From Address", "type": "email", "required": True, "placeholder": "absulli@example.com"},
            {"name": "email_to", "label": "To Address", "type": "email", "required": True, "placeholder": "you@example.com"},
            {"name": "email_smtp_username", "label": "SMTP Username", "type": "text", "required": False, "placeholder": "optional"},
            {"name": "email_smtp_password", "label": "SMTP Password", "type": "password", "required": False, "placeholder": "optional"},
            {"name": "email_use_tls", "label": "Use SSL/TLS", "type": "checkbox", "required": False, "default": True},
        ],
    },
    "discord": {
        "label": "Discord",
        "icon": "",
        "icon_template": "partials/notification_icons/discord.svg",
        "fields": [
            {"name": "discord_webhook_url", "label": "Webhook URL", "type": "url", "required": True, "placeholder": "https://discord.com/api/webhooks/..."},
            {"name": "discord_include_cover_art", "label": "Include Cover Art", "type": "checkbox", "required": False, "default": False, "full_row": True, "helper": "Show the media cover in rich notifications."},
            {"name": "discord_open_in_audiobookshelf", "label": "Open in Audiobookshelf", "type": "checkbox", "required": False, "default": False, "full_row": True, "helper": "Add a link that opens the item in Audiobookshelf."},
        ],
    },
    "gotify": {
        "label": "Gotify",
        "icon": "",
        "icon_template": "partials/notification_icons/gotify.svg",
        "fields": [
            {"name": "gotify_url", "label": "Server URL", "type": "url", "required": True, "placeholder": ""},
            {"name": "gotify_token", "label": "Application Token", "type": "password", "required": True, "placeholder": ""},
            {"name": "gotify_include_cover_art", "label": "Include Cover Art", "type": "checkbox", "required": False, "default": False, "full_row": True, "helper": "Show the media cover in rich notifications."},
            {"name": "gotify_open_in_audiobookshelf", "label": "Open in Audiobookshelf", "type": "checkbox", "required": False, "default": False, "full_row": True, "helper": "Add a link that opens the item in Audiobookshelf."},
        ],
    },
    "ntfy": {
        "label": "ntfy.sh",
        "icon": "",
        "icon_template": "partials/notification_icons/ntfy.svg",
        "fields": [
            {"name": "ntfy_url", "label": "Topic URL", "type": "url", "required": True, "placeholder": "https://ntfy.sh/my-topic"},
            {"name": "ntfy_token", "label": "Access Token", "type": "password", "required": False, "placeholder": ""},
        ],
    },
    "pushbullet": {
        "label": "Pushbullet",
        "icon": "",
        "icon_template": "partials/notification_icons/pushbullet.svg",
        "fields": [
            {"name": "pushbullet_token", "label": "Access Token", "type": "password", "required": True, "placeholder": ""},
            {"name": "pushbullet_open_in_audiobookshelf", "label": "Open in Audiobookshelf", "type": "checkbox", "required": False, "default": False, "full_row": True, "helper": "Open the item directly from the Pushbullet notification."},
        ],
    },
    "pushover": {
        "label": "Pushover",
        "icon": "",
        "icon_template": "partials/notification_icons/pushover.svg",
        "fields": [
            {"name": "pushover_app_token", "label": "Application Token", "type": "password", "required": True, "placeholder": ""},
            {"name": "pushover_user_key", "label": "User Key", "type": "password", "required": True, "placeholder": ""},
        ],
    },
    "slack": {
        "label": "Slack",
        "icon": "",
        "icon_template": "partials/notification_icons/slack.svg",
        "fields": [
            {"name": "slack_webhook_url", "label": "Webhook URL", "type": "url", "required": True, "placeholder": "https://hooks.slack.com/services/..."},
        ],
    },
    "telegram": {
        "label": "Telegram",
        "icon": "",
        "icon_template": "partials/notification_icons/telegram.svg",
        "fields": [
            {"name": "telegram_bot_token", "label": "Bot Token", "type": "password", "required": True, "placeholder": ""},
            {"name": "telegram_chat_id", "label": "Chat ID", "type": "text", "required": True, "placeholder": ""},
        ],
    },
    "webhook": {
        "label": "Webhook",
        "icon": "",
        "icon_template": "partials/notification_icons/webhook.svg",
        "fields": [
            {"name": "webhook_url", "label": "Webhook URL", "type": "url", "required": True, "placeholder": "https://example.com/webhook"},
        ],
    },
}

NOTIFICATION_AGENT_TABS = [
    {
        "id": agent_id,
        "label": config["label"],
        "icon": config["icon"],
        "icon_template": config.get("icon_template", ""),
        "available": True,
    }
    for agent_id, config in AGENT_FIELD_CONFIGS.items()
]




def compact_bytes(value: int) -> str:
    size = float(max(value, 0))
    units = ("B", "KB", "MB", "GB", "TB")
    for unit in units:
        if size < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024


def database_file_size(settings) -> str:
    db_path = settings.data_dir / "absulli.db"
    try:
        return compact_bytes(db_path.stat().st_size)
    except OSError:
        return "Unavailable"


def alembic_version(db: Session) -> str:
    try:
        version = db.execute(text("SELECT version_num FROM alembic_version LIMIT 1")).scalar()
    except Exception as exc:
        log.debug("Unable to read Alembic version: %s", exc)
        return "Unavailable"
    return str(version or "Unavailable")


def about_settings_context(settings, db: Session, version_status: dict[str, object] | None = None) -> list[dict[str, object]]:
    version_status = version_status or {}
    return [
        {
            "label": "ABSulli Version",
            "value": str(version_status.get("current_version") or f"v{__version__}"),
            "status_label": str(version_status.get("badge_label") or "Unknown"),
            "status_class": str(version_status.get("badge_class") or "unknown"),
            "status_url": str(version_status.get("release_url") or "") if version_status.get("channel") in {"stable", "nightly"} else "",
        },
        {"label": "Data Directory", "value": str(settings.data_dir)},
        {"label": "Database Path", "value": str(settings.data_dir / "absulli.db")},
        {"label": "Database Size", "value": database_file_size(settings)},
        {"label": "Migration Version", "value": alembic_version(db)},
        {"label": "Python Version", "value": sys.version.split()[0]},
        {"label": "Time Zone", "value": os.environ.get("TZ", "").strip() or "Not configured"},
    ]


def about_data_context(db: Session) -> list[dict[str, object]]:
    return [
        {"label": "Users", "value": db.query(AbsUser).count()},
        {"label": "Libraries", "value": db.query(Library).count()},
        {"label": "Media Items", "value": db.query(MediaItem).count()},
        {"label": "Listening History Rows", "value": db.query(ListeningHistory).count()},
        {"label": "Active Sessions", "value": db.query(ActivitySession).filter(ActivitySession.is_active.is_(True)).count()},
        {"label": "Notification Events", "value": db.query(NotificationEvent).count()},
    ]


def bool_setting(name: str, default: bool = False) -> bool:
    value = setup_state.get_setup_setting(name, "")
    if value == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def notification_library_scope_context(agent_id: str, libraries: list[Library]) -> dict[str, object]:
    value = setup_state.get_setup_setting(f"{agent_id}_notification_libraries", "")
    all_libraries = value in {"", "*"}
    selected: set[str] = set()
    if not all_libraries:
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            decoded = None
        if isinstance(decoded, list):
            selected = {str(item) for item in decoded if item}
        else:
            all_libraries = True
    return {
        "all_libraries": all_libraries,
        "libraries": [
            {
                "id": library.abs_library_id,
                "name": library.name or library.abs_library_id,
                "media_type": library.media_type or "unknown",
                "selected": library.abs_library_id in selected,
            }
            for library in libraries
        ],
    }


def notification_library_value_from_form(agent_id: str, form, libraries: list[Library]) -> str:
    if form.get(f"{agent_id}_library_scope_present") != "1":
        return setup_state.get_setup_setting(f"{agent_id}_notification_libraries", "") or "*"
    if form.get(f"{agent_id}_all_libraries") == "on":
        return "*"
    known_ids = {library.abs_library_id for library in libraries}
    selected = sorted(
        {
            str(value).strip()
            for value in form.getlist(f"{agent_id}_library_ids")
            if str(value).strip() in known_ids
        }
    )
    return json.dumps(selected)


def notification_events_context(agent_id: str) -> list[dict[str, object]]:
    events = []
    for event_type, meta in NOTIFICATION_EVENT_SETTINGS.items():
        base_setting = str(meta["setting"])
        setting_name = f"{agent_id}_{base_setting}"
        saved_value = setup_state.get_setup_setting(setting_name, "")
        if saved_value == "":
            enabled = bool_setting(base_setting, bool(meta["default"]))
        else:
            enabled = str(saved_value).strip().lower() in {"1", "true", "yes", "on"}
        events.append(
            {
                "event_type": event_type,
                "setting": setting_name,
                "label": meta["label"],
                "description": meta["description"],
                "enabled": enabled,
            }
        )
    return events

def settings_field_from_env(settings, field_name: str) -> bool:
    return settings.field_configured(field_name)


def general_saved_field_value(settings, field: dict[str, object]) -> str:
    field_name = str(field["name"])
    field_type = str(field.get("type", "text"))
    if field_type == "checkbox":
        return "true" if getattr(settings, f"effective_{field_name}", getattr(settings, field_name, False)) else "false"
    if settings_field_from_env(settings, field_name):
        value = getattr(settings, field_name, "")
        return str(value or "").strip()
    if field_name == "abs_url":
        return settings.effective_abs_url
    if field_name == "abs_api_key":
        return setup_state.get_setup_setting("abs_api_key", "")
    effective_name = f"effective_{field_name}"
    if hasattr(settings, effective_name):
        return str(getattr(settings, effective_name))
    return setup_state.get_setup_setting(field_name, str(getattr(settings, field_name, "")))


def general_settings_context(settings) -> list[dict[str, object]]:
    fields = []
    for field in GENERAL_FIELD_CONFIGS:
        field_name = str(field["name"])
        field_type = str(field.get("type", "text"))
        value = general_saved_field_value(settings, field)
        from_env = settings_field_from_env(settings, field_name)
        fields.append(
            {
                **field,
                "value": value,
                "from_env": from_env,
                "token_configured": bool(value) if field_type == "password" else False,
            }
        )
    return fields


def clean_int_range(value: str, label: str, minimum: int, maximum: int) -> str:
    try:
        number = int(str(value or "").strip())
    except ValueError as exc:
        raise ValueError(f"{label} must be a number") from exc
    if number < minimum or number > maximum:
        raise ValueError(f"{label} must be between {minimum} and {maximum}")
    return str(number)


def clean_cors_origins(value: str) -> str:
    origins = []
    for origin in str(value or "").split(","):
        origin = origin.strip().rstrip("/")
        if not origin:
            continue
        if not origin.startswith(("http://", "https://")):
            raise ValueError("CORS allowed origins must start with http:// or https://")
        origins.append(origin)
    return ",".join(origins)


def general_values_from_form(settings, form) -> dict[str, str]:
    values: dict[str, str] = {}
    for field in GENERAL_FIELD_CONFIGS:
        field_name = str(field["name"])
        field_type = str(field.get("type", "text"))
        if settings_field_from_env(settings, field_name):
            current = getattr(settings, field_name, "")
            values[field_name] = str(current or "").strip()
            continue
        if field_type == "checkbox":
            values[field_name] = "true" if form.get(field_name) == "on" else "false"
            continue
        submitted = clean_agent_value(str(form.get(field_name) or ""))
        if field_type == "password" and not submitted:
            submitted = setup_state.get_setup_setting(field_name, "")
        values[field_name] = submitted

    values["abs_url"] = clean_http_url(values.get("abs_url", ""), "Audiobookshelf URL")
    if not values.get("abs_url"):
        raise ValueError("Audiobookshelf URL is required")
    if not values.get("abs_api_key") or values.get("abs_api_key") == "change_me":
        raise ValueError("Audiobookshelf API key is required")
    values["abs_request_timeout"] = clean_int_range(values.get("abs_request_timeout", ""), "Request timeout", 1, 300)
    values["abs_poll_interval"] = clean_int_range(values.get("abs_poll_interval", ""), "Activity poll interval", 3, 3600)
    values["abs_history_poll_interval"] = clean_int_range(values.get("abs_history_poll_interval", ""), "History poll interval", 60, 86400)
    return values


def saved_settings_field_value(settings, field: dict[str, object]) -> str:
    field_name = str(field["name"])
    field_type = str(field.get("type", "text"))
    effective_name = f"effective_{field_name}"
    if field_type == "checkbox":
        value = getattr(settings, effective_name, getattr(settings, field_name, bool(field.get("default", False))))
        return "true" if value else "false"
    if settings_field_from_env(settings, field_name):
        value = getattr(settings, field_name, "")
        if hasattr(value, "get_secret_value"):
            value = value.get_secret_value()
        return str(value or "").strip()
    if hasattr(settings, effective_name):
        return str(getattr(settings, effective_name) or "").strip()
    return setup_state.get_setup_setting(field_name, str(getattr(settings, field_name, "")))


def settings_fields_context(settings, field_configs: list[dict[str, object]]) -> list[dict[str, object]]:
    fields = []
    for field in field_configs:
        field_name = str(field["name"])
        field_type = str(field.get("type", "text"))
        value = saved_settings_field_value(settings, field)
        from_env = settings_field_from_env(settings, field_name)
        fields.append(
            {
                **field,
                "value": value,
                "from_env": from_env,
                "token_configured": bool(value) if field_type == "password" else False,
            }
        )
    return fields


def network_settings_context(settings) -> list[dict[str, object]]:
    return settings_fields_context(settings, NETWORK_FIELD_CONFIGS)


def user_settings_context_fields(settings) -> list[dict[str, object]]:
    fields = []
    for field in USER_FIELD_CONFIGS:
        field_name = str(field["name"])
        field_type = str(field.get("type", "text"))
        if field_name == "current_password":
            value = ""
            from_env = settings.auth_password_from_env or settings.auth_password_hash_from_env
            token_configured = False
        elif field_name == "auth_password_confirm":
            value = ""
            from_env = settings.auth_password_from_env or settings.auth_password_hash_from_env
            token_configured = False
        elif field_name == "auth_password":
            value = ""
            from_env = settings.auth_password_from_env or settings.auth_password_hash_from_env
            token_configured = bool(auth_password_hash() or settings.auth_password_from_env)
        elif field_name == "auth_username":
            value = auth_username()
            from_env = settings.auth_username_from_env
            token_configured = False
        else:
            value = saved_settings_field_value(settings, field)
            from_env = settings_field_from_env(settings, field_name)
            token_configured = bool(value) if field_type == "password" else False
        fields.append(
            {
                **field,
                "value": value,
                "from_env": from_env,
                "token_configured": token_configured,
            }
        )
    return fields


def user_values_from_form(settings, form) -> tuple[dict[str, str], bool]:
    values: dict[str, str] = {}
    password_changed = False

    if not settings.auth_username_from_env:
        username = clean_agent_value(str(form.get("auth_username") or ""))
        if not username:
            raise ValueError("Admin username is required")
        if len(username) > 80:
            raise ValueError("Admin username must be 80 characters or fewer")
        values["auth_username"] = username

    if not settings.auth_password_from_env and not settings.auth_password_hash_from_env:
        new_password = str(form.get("auth_password") or "")
        confirm_password = str(form.get("auth_password_confirm") or "")
        if new_password or confirm_password:
            current_password = str(form.get("current_password") or "")
            if not verify_login(auth_username(), current_password):
                raise ValueError("Current password is required to set a new one")
            if len(new_password) < 8:
                raise ValueError("Admin password must be at least 8 characters")
            if new_password != confirm_password:
                raise ValueError("Admin passwords do not match")
            values["auth_password_hash"] = password_hash(new_password)
            password_changed = True



    return values, password_changed



def api_settings_context(settings) -> dict[str, object]:
    token = settings.effective_api_token or setup_state.ensure_api_token()
    return {
        "enabled": settings.effective_api_enabled,
        "enabled_from_env": settings.api_enabled_from_env,
        "token": token,
        "token_from_env": settings.api_token_from_env,
        "env_managed": settings.api_enabled_from_env or settings.api_token_from_env,
    }


def api_values_from_form(settings, form) -> dict[str, str]:
    values: dict[str, str] = {}
    if not settings.api_enabled_from_env:
        values["api_enabled"] = "true" if form.get("api_enabled") == "on" else "false"
    if not settings.api_token_from_env:
        values["api_token"] = setup_state.ensure_api_token()
    return values


def regenerate_api_token(settings) -> str:
    if settings.api_token_from_env:
        raise ValueError("API key is managed by the environment")
    token = secrets.token_urlsafe(32)
    setup_state.set_setup_setting("api_token", token)
    return token

def network_values_from_form(settings, form) -> dict[str, str]:
    values: dict[str, str] = {}
    for field in NETWORK_FIELD_CONFIGS:
        field_name = str(field["name"])
        field_type = str(field.get("type", "text"))
        if settings_field_from_env(settings, field_name):
            current = getattr(settings, field_name, "")
            if hasattr(current, "get_secret_value"):
                current = current.get_secret_value()
            values[field_name] = str(current or "").strip()
            continue
        if field_type == "checkbox":
            values[field_name] = "true" if form.get(field_name) == "on" else "false"
            continue
        submitted = clean_agent_value(str(form.get(field_name) or ""))
        values[field_name] = submitted

    if values.get("metrics_token") == "change_me":
        raise ValueError("Metrics token cannot be change_me")
    if "cors_allowed_origins" in values:
        values["cors_allowed_origins"] = clean_cors_origins(values.get("cors_allowed_origins", ""))
    if "public_url" in values:
        values["public_url"] = clean_http_url(values.get("public_url", ""), "ABSulli Public URL")
    return values


def regenerate_metrics_token(settings) -> str:
    if settings_field_from_env(settings, "metrics_token"):
        raise ValueError("Metrics token is managed by the environment")
    token = secrets.token_urlsafe(32)
    setup_state.set_setup_setting("metrics_token", token)
    return token


def gotify_settings_context(settings) -> dict[str, object]:
    stored_url = setup_state.get_setup_setting("gotify_url", "")
    stored_token = setup_state.get_setup_setting("gotify_token", "")
    effective_url = settings.effective_gotify_url
    effective_token = settings.effective_gotify_token
    return {
        "enabled": bool(effective_url and effective_token),
        "url": effective_url if settings.gotify_url_from_env else stored_url,
        "token_configured": bool(effective_token),
        "url_from_env": settings.gotify_url_from_env,
        "token_from_env": settings.gotify_token_from_env,
        "env_managed": settings.gotify_url_from_env or settings.gotify_token_from_env,
        "fully_env_managed": settings.gotify_url_from_env and settings.gotify_token_from_env,
    }



def field_from_env(settings, field_name: str) -> bool:
    return settings.env_bool(field_name)


def saved_field_value(settings, field_name: str, field: dict[str, object]) -> str:
    if field.get("type") == "checkbox":
        return "true" if settings.effective_bool_setting(field_name, default=bool(field.get("default", False))) else "false"
    if field_from_env(settings, field_name):
        return settings.env_string(field_name)
    return setup_state.get_setup_setting(field_name, "")


def agent_settings_context(settings, libraries: list[Library] | None = None) -> list[dict[str, object]]:
    agents = []
    libraries = libraries or []
    for agent_id, config in AGENT_FIELD_CONFIGS.items():
        fields = []
        configured = True
        env_managed = False
        for field in config["fields"]:
            field_name = str(field["name"])
            field_type = str(field.get("type", "text"))
            value = saved_field_value(settings, field_name, field)
            from_env = field_from_env(settings, field_name)
            env_managed = env_managed or from_env
            token_configured = bool(value) if field_type == "password" else False
            if field.get("required") and not value:
                configured = False
            fields.append({**field, "value": value, "from_env": from_env, "token_configured": token_configured})

        enabled = bool_setting(f"{agent_id}_enabled", configured) and configured
        agents.append(
            {
                "id": agent_id,
                "label": config["label"],
                "icon": config["icon"],
                "icon_template": config.get("icon_template", ""),
                "available": True,
                "enabled": enabled,
                "configured": configured,
                "env_managed": env_managed,
                "fields": fields,
                "notification_events": notification_events_context(agent_id),
                "library_scope": notification_library_scope_context(agent_id, libraries),
                "message_templates": notification_templates_context(agent_id),
                "template_variable_groups": NOTIFICATION_TEMPLATE_VARIABLE_GROUPS,
                "advanced_open": notification_advanced_open(agent_id),
                "webhook_authorization_header": setup_state.get_setup_setting("webhook_authorization_header", "") if agent_id == "webhook" else "",
                "webhook_custom_headers": json.loads(setup_state.get_setup_setting("webhook_custom_headers_json", "") or "{}") if agent_id == "webhook" else {},
                "webhook_payload_template": webhook_payload_template_context() if agent_id == "webhook" else "",
                "webhook_default_payload": WEBHOOK_DEFAULT_PAYLOAD if agent_id == "webhook" else "",
            }
        )
    return agents


def agent_required_fields_present(agent_id: str, values: dict[str, str]) -> bool:
    config = AGENT_FIELD_CONFIGS[agent_id]
    for field in config["fields"]:
        if field.get("required") and not values.get(str(field["name"]), ""):
            return False
    return True


def clean_agent_url_fields(agent_id: str, values: dict[str, str]) -> dict[str, str]:
    cleaned = dict(values)
    labels = {str(field["name"]): str(field["label"]) for field in AGENT_FIELD_CONFIGS[agent_id]["fields"]}
    for key in list(cleaned):
        if key.endswith("_url") or key in {"ntfy_url", "email_smtp_host"}:
            if key == "email_smtp_host":
                cleaned[key] = clean_agent_value(cleaned[key])
            else:
                cleaned[key] = clean_http_url(cleaned[key], labels.get(key, "URL"))
    if "email_smtp_port" in cleaned:
        try:
            port = int(cleaned["email_smtp_port"] or "465")
        except ValueError as exc:
            raise ValueError("SMTP port must be a number") from exc
        if port < 1 or port > 65535:
            raise ValueError("SMTP port must be between 1 and 65535")
        cleaned["email_smtp_port"] = str(port)
    return cleaned


def agent_values_from_form(settings, agent_id: str, form) -> dict[str, str]:
    values: dict[str, str] = {}
    for field in AGENT_FIELD_CONFIGS[agent_id]["fields"]:
        field_name = str(field["name"])
        field_type = str(field.get("type", "text"))
        if field_type == "checkbox":
            values[field_name] = "true" if form.get(field_name) == "on" else "false"
            continue
        if field_from_env(settings, field_name):
            values[field_name] = settings.env_string(field_name)
            continue
        submitted_value = clean_agent_value(str(form.get(field_name) or ""))
        if field_type == "password" and not submitted_value:
            submitted_value = setup_state.get_setup_setting(field_name, "")
        values[field_name] = submitted_value
    return clean_agent_url_fields(agent_id, values)


def agent_from_values(agent_id: str, values: dict[str, str]):
    if agent_id == "gotify":
        return GotifyAgent(values["gotify_url"], values["gotify_token"])
    if agent_id == "ntfy":
        return NtfyAgent(values["ntfy_url"], values.get("ntfy_token", ""))
    if agent_id == "discord":
        return DiscordAgent(values["discord_webhook_url"])
    if agent_id == "slack":
        return SlackAgent(values["slack_webhook_url"])
    if agent_id == "telegram":
        return TelegramAgent(values["telegram_bot_token"], values["telegram_chat_id"])
    if agent_id == "pushover":
        return PushoverAgent(values["pushover_app_token"], values["pushover_user_key"])
    if agent_id == "pushbullet":
        return PushbulletAgent(values["pushbullet_token"])
    if agent_id == "webhook":
        headers = {}
        authorization = values.get("webhook_authorization_header", "").strip()
        if authorization:
            headers["Authorization"] = authorization
        custom_headers_value = values.get("webhook_custom_headers_json", "")
        custom_headers = json.loads(custom_headers_value) if custom_headers_value else {}
        if isinstance(custom_headers, dict):
            for key, value in custom_headers.items():
                if str(key).lower() != "authorization" or not authorization:
                    headers[str(key)] = str(value)
        return WebhookAgent(values["webhook_url"], headers)
    if agent_id == "email":
        return EmailAgent(
            smtp_host=values["email_smtp_host"],
            smtp_port=int(values.get("email_smtp_port") or "465"),
            sender=values["email_from"],
            recipient=values["email_to"],
            username=values.get("email_smtp_username", ""),
            password=values.get("email_smtp_password", ""),
            use_tls=values.get("email_use_tls", "true").lower() in {"1", "true", "yes", "on"},
        )
    raise ValueError("Unknown notification agent")

