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
