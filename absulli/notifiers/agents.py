from __future__ import annotations

import asyncio
from email.message import EmailMessage
import smtplib
from typing import Any

import httpx


class GotifyAgent:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.token = token

    async def send(self, title: str, message: str, extra: dict[str, Any] | None = None) -> None:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                f"{self.base_url}/message",
                headers={"X-Gotify-Key": self.token},
                json={"title": title, "message": message, "priority": 4, "extras": extra or {}},
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

    async def send(self, title: str, message: str, extra: dict[str, Any] | None = None) -> None:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                self.webhook_url,
                json={
                    "username": "ABSulli",
                    "embeds": [{"title": title, "description": message, "color": 14006049}],
                },
            )
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
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                "https://api.pushbullet.com/v2/pushes",
                headers={"Access-Token": self.access_token},
                json={"type": "note", "title": title, "body": message},
            )
            response.raise_for_status()


class WebhookAgent:
    def __init__(self, url: str):
        self.url = url

    async def send(self, title: str, message: str, extra: dict[str, Any] | None = None) -> None:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                self.url,
                json={"title": title, "message": message, "extra": extra or {}},
            )
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
    ):
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.sender = sender
        self.recipient = recipient
        self.username = username
        self.password = password
        self.use_tls = use_tls

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
            if self.username or self.password:
                smtp.login(self.username, self.password)
            smtp.send_message(email)
