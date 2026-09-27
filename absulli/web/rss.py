from __future__ import annotations

from datetime import UTC
from email.utils import format_datetime
from html import escape
import json
import re
from urllib.parse import quote, urlencode
from xml.etree import ElementTree

from absulli.core.security import notification_cover_token

RSS_FEED_ENABLED_SETTING = "rss_feed_enabled"
RSS_FEED_TOKEN_SETTING = "rss_feed_token"
RSS_DELIVERY_AGENT = "rss"
RSS_ENTRY_LIMIT = 100
RSS_EVENT_TYPES = {"new_book", "new_podcast", "new_podcast_episode"}

_INVALID_XML_CHARACTERS = re.compile(
    "[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]"
)


def rss_setting_enabled(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def notification_context(value: object) -> dict[str, str]:
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(key): str(item or "") for key, item in parsed.items()}


def _xml_text(value: object) -> str:
    return _INVALID_XML_CHARACTERS.sub("", str(value or ""))


def _published(value) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return format_datetime(value.astimezone(UTC), usegmt=True)


def _entry_link(context: dict[str, str], abs_url: str) -> str:
    item_id = context.get("item_id", "").strip()
    if not item_id or not abs_url:
        return ""
    return f"{abs_url.rstrip('/')}/item/{quote(item_id, safe='')}"


def _cover_url(context: dict[str, str], public_url: str) -> str:
    item_id = context.get("item_id", "").strip()
    if not item_id or not public_url:
        return ""
    query = urlencode({"width": 600, "token": notification_cover_token(item_id)})
    return (
        f"{public_url.rstrip('/')}/notification-covers/items/"
        f"{quote(item_id, safe='')}?{query}"
    )


def _description(
    event_type: str,
    context: dict[str, str],
    body: str,
    cover_url: str,
    item_url: str,
) -> str:
    parts: list[str] = []
    title = context.get("title", "").strip() or "Unknown"

    if cover_url:
        parts.append(
            f'<p><img src="{escape(cover_url, quote=True)}" '
            f'alt="{escape(title, quote=True)} cover"></p>'
        )

    if event_type == "new_podcast_episode":
        metadata = (
            ("Podcast", context.get("podcast_title", "")),
            ("Library", context.get("library_name", "")),
        )
    elif event_type == "new_podcast":
        metadata = (
            ("Author", context.get("author", "")),
            ("Library", context.get("library_name", "")),
        )
    else:
        metadata = (
            ("Author", context.get("author", "")),
            ("Narrator", context.get("narrator", "")),
            ("Series", context.get("series", "")),
            ("Library", context.get("library_name", "")),
            ("Published", context.get("year", "")),
        )

    details = "".join(
        f"<li><strong>{label}:</strong> {escape(value.strip())}</li>"
        for label, value in metadata
        if value.strip()
    )

    if details:
        parts.append(f"<ul>{details}</ul>")

    if event_type == "new_podcast_episode":
        description = context.get("episode_description", "").strip()
    else:
        description = context.get("description", "").strip()
    if description:
        parts.append(f"<p>{escape(description)}</p>")
    elif body:
        parts.append(f"<p>{escape(body)}</p>")

    if item_url:
        parts.append(
            f'<p><a href="{escape(item_url, quote=True)}">'
            "Open in Audiobookshelf</a></p>"
        )

    return "".join(parts)


def _display_title(event_type: str, context: dict[str, str], fallback: str) -> str:
    if event_type == "new_podcast_episode":
        podcast_title = context.get("podcast_title", "").strip() or "Unknown podcast"
        episode_title = context.get("episode_title", "").strip() or fallback
        return f"{podcast_title} - {episode_title}"

    title = context.get("title", "").strip() or fallback
    author = context.get("author", "").strip()
    display_title = f"{author} - {title}" if author else title
    year = context.get("year", "").strip()
    if year:
        display_title = f"{display_title} ({year})"
    return display_title


def build_rss_feed(
    events,
    feed_url: str,
    public_url: str,
    abs_url: str,
) -> bytes:
    atom_namespace = "http://www.w3.org/2005/Atom"
    ElementTree.register_namespace("atom", atom_namespace)

    root = ElementTree.Element("rss", {"version": "2.0"})
    channel = ElementTree.SubElement(root, "channel")

    ElementTree.SubElement(channel, "title").text = "ABSulli New Media"
    ElementTree.SubElement(channel, "link").text = _xml_text(public_url)
    ElementTree.SubElement(
        channel,
        "description",
    ).text = "New books, podcasts, and podcast episodes detected by ABSulli"
    ElementTree.SubElement(channel, "language").text = "en"

    image_url = f"{public_url.rstrip('/')}/static/img/logo-mark.png?v=1"

    image = ElementTree.SubElement(channel, "image")
    ElementTree.SubElement(image, "url").text = image_url
    ElementTree.SubElement(image, "title").text = "ABSulli New Media"
    ElementTree.SubElement(image, "link").text = _xml_text(public_url)
    ElementTree.SubElement(image, "width").text = "144"
    ElementTree.SubElement(image, "height").text = "144"

    ElementTree.SubElement(
        channel,
        f"{{{atom_namespace}}}icon",
    ).text = image_url

    ElementTree.SubElement(
        channel,
        f"{{{atom_namespace}}}link",
        {
            "href": feed_url,
            "rel": "self",
            "type": "application/rss+xml",
        },
    )

    if events:
        ElementTree.SubElement(
            channel,
            "lastBuildDate",
        ).text = _published(events[0].created_at)

    for event in events:
        context = notification_context(event.context_json)
        display_title = _display_title(
            event.event_type,
            context,
            event.title or "New media added",
        )
        link = _entry_link(context, abs_url)
        cover_url = _cover_url(context, public_url)

        item = ElementTree.SubElement(channel, "item")
        ElementTree.SubElement(item, "title").text = _xml_text(display_title)
        ElementTree.SubElement(
            item,
            "guid",
            {"isPermaLink": "false"},
        ).text = f"urn:absulli:{event.event_type.replace('_', '-')}:{event.id}"
        ElementTree.SubElement(
            item,
            "pubDate",
        ).text = _published(event.created_at)

        if link:
            ElementTree.SubElement(item, "link").text = _xml_text(link)

        library_name = context.get("library_name", "").strip()
        if library_name:
            ElementTree.SubElement(
                item,
                "category",
            ).text = _xml_text(library_name)

        ElementTree.SubElement(
            item,
            "description",
        ).text = _xml_text(
            _description(event.event_type, context, event.body, cover_url, link)
        )

    return ElementTree.tostring(
        root,
        encoding="utf-8",
        xml_declaration=True,
    )
