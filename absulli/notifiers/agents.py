from __future__ import annotations

import asyncio
from email.message import EmailMessage
import json
import re
import smtplib
import ssl
from typing import Any

import httpx


class GotifyAgent:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.token = token

    async def send(self, title: str, message: str, extra: dict[str, Any] | None = None) -> None:
        extra = extra or {}
        cover_url = str(extra.get("cover_url") or "").strip()
        click_url = str(extra.get("click_url") or "").strip()
        rendered_message = message
        extras: dict[str, Any] = {}
        markdown_enabled = bool(cover_url or click_url)
        if markdown_enabled:
            rendered_message = re.sub(r"(?<!\n)\n(?!\n)", "  \n", rendered_message)
        if click_url:
            rendered_message = f"{rendered_message}\n\n[Open in Audiobookshelf]({click_url})"
            notification = extras.setdefault("client::notification", {})
            notification["click"] = {"url": click_url}
        if cover_url:
            rendered_message = f"{rendered_message}\n\n![]({cover_url})"
            notification = extras.setdefault("client::notification", {})
            notification["bigImageUrl"] = cover_url
        extras["client::display"] = {"contentType": "text/markdown" if markdown_enabled else "text/plain"}
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                f"{self.base_url}/message",
                headers={"X-Gotify-Key": self.token},
                json={"title": title, "message": rendered_message, "priority": 4, "extras": extras},
            )
            response.raise_for_status()


class NtfyAgent:
    def __init__(self, topic_url: str, token: str = ""):
        self.topic_url = topic_url.rstrip("/")
        self.token = token

    async def send(self, title: str, message: str, extra: dict[str, Any] | None = None) -> None:
        headers = {"Title": title, "Tags": "headphones"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(self.topic_url, headers=headers, content=message.encode("utf-8"))
            response.raise_for_status()


class DiscordAgent:
    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url

    @staticmethod
    def _truncate(value: str, limit: int) -> str:
        if len(value) <= limit:
            return value
        if limit <= 3:
            return value[:limit]
        return value[: limit - 3].rstrip() + "..."

    async def send(self, title: str, message: str, extra: dict[str, Any] | None = None) -> None:
        extra = extra or {}
        cover_url = str(extra.get("cover_url") or "").strip()
        click_url = str(extra.get("click_url") or "").strip()
        embed_title = self._truncate(title, 256)
        description = message
        if click_url:
            suffix = f"\n\n[Open in Audiobookshelf]({click_url})"
            if len(suffix) < 4096:
                description = self._truncate(description, 4096 - len(suffix)) + suffix
            else:
                description = self._truncate(description, 4096)
        else:
            description = self._truncate(description, 4096)
        embed: dict[str, Any] = {"title": embed_title, "description": description, "color": 14006049}
        if click_url:
            embed["url"] = click_url
        payload = {"username": "ABSulli", "allowed_mentions": {"parse": []}, "embeds": [embed]}
        async with httpx.AsyncClient(timeout=10) as client:
            cover_response = None
            if cover_url:
                try:
                    cover_response = await client.get(cover_url)
                    cover_response.raise_for_status()
                except Exception:
                    cover_response = None
            if cover_response is not None:
                content_type = str(cover_response.headers.get("content-type") or "image/webp").split(";", 1)[0].strip()
                extension = {
                    "image/jpeg": "jpg",
                    "image/png": "png",
                    "image/gif": "gif",
                    "image/webp": "webp",
                }.get(content_type, "webp")
                filename = f"cover.{extension}"
                embed["thumbnail"] = {"url": f"attachment://{filename}"}
                response = await client.post(
                    self.webhook_url,
                    data={"payload_json": json.dumps(payload)},
                    files={"files[0]": (filename, cover_response.content, content_type)},
                )
            else:
                if cover_url:
                    embed["thumbnail"] = {"url": cover_url}
                response = await client.post(self.webhook_url, json=payload)
            response.raise_for_status()


class SlackAgent:
    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url

    async def send(self, title: str, message: str, extra: dict[str, Any] | None = None) -> None:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(self.webhook_url, json={"text": f"*{title}*\n{message}"})
            response.raise_for_status()


class TelegramAgent:
    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id

    async def send(self, title: str, message: str, extra: dict[str, Any] | None = None) -> None:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
                json={"chat_id": self.chat_id, "text": f"{title}\n\n{message}"},
            )
            response.raise_for_status()


class PushoverAgent:
    def __init__(self, app_token: str, user_key: str):
        self.app_token = app_token
        self.user_key = user_key

    async def send(self, title: str, message: str, extra: dict[str, Any] | None = None) -> None:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                "https://api.pushover.net/1/messages.json",
                data={"token": self.app_token, "user": self.user_key, "title": title, "message": message},
            )
            response.raise_for_status()


class PushbulletAgent:
    def __init__(self, access_token: str):
        self.access_token = access_token

    async def send(self, title: str, message: str, extra: dict[str, Any] | None = None) -> None:
        extra = extra or {}
        click_url = str(extra.get("click_url") or "").strip()
        payload = {"type": "note", "title": title, "body": message}
        if click_url:
            payload = {"type": "link", "title": title, "body": message, "url": click_url}
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                "https://api.pushbullet.com/v2/pushes",
                headers={"Access-Token": self.access_token},
                json=payload,
            )
            response.raise_for_status()


class WebhookAgent:
    def __init__(self, url: str, headers: dict[str, str] | None = None):
        self.url = url
        self.headers = dict(headers or {})

    async def send(self, title: str, message: str, extra: dict[str, Any] | None = None) -> None:
        extra = extra or {}
        payload = extra.get("webhook_payload")
        if payload is None:
            payload = {"title": title, "message": message, "extra": extra}
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(self.url, headers=self.headers, json=payload)
            response.raise_for_status()


class EmailAgent:
    def __init__(
        self,
        smtp_host: str,
        smtp_port: int,
        sender: str,
        recipient: str,
        username: str = "",
        password: str = "",
        use_tls: bool = True,
        use_starttls: bool = False,
    ):
        if use_tls and use_starttls:
            raise ValueError("SSL/TLS and STARTTLS cannot both be enabled")
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.sender = sender
        self.recipient = recipient
        self.username = username
        self.password = password
        self.use_tls = use_tls
        self.use_starttls = use_starttls

    async def send(self, title: str, message: str, extra: dict[str, Any] | None = None) -> None:
        await asyncio.to_thread(self._send_sync, title, message)

    def _send_sync(self, title: str, message: str) -> None:
        email = EmailMessage()
        email["From"] = self.sender
        email["To"] = self.recipient
        email["Subject"] = title
        email.set_content(message)

        smtp_class = smtplib.SMTP_SSL if self.use_tls else smtplib.SMTP
        with smtp_class(self.smtp_host, self.smtp_port, timeout=10) as smtp:
            if self.use_starttls:
                smtp.starttls(context=ssl.create_default_context())
            if self.username or self.password:
                smtp.login(self.username, self.password)
            smtp.send_message(email)
