import logging
from typing import Any

import httpx

from absulli.core.config import Settings

log = logging.getLogger(__name__)


class AudiobookshelfClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._client: httpx.AsyncClient | None = None
        self._client_config: tuple[str, str, int, bool] | None = None

    async def __aenter__(self) -> "AudiobookshelfClient":
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    def _base_url(self, override: str | None = None) -> str:
        return (override or self.settings.effective_abs_url).strip().rstrip("/")

    def _api_key(self, override: str | None = None) -> str:
        return (override or self.settings.effective_abs_api_key or "").strip()

    def _current_client_config(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> tuple[str, str, int, bool]:
        return (
            self._base_url(base_url),
            self._api_key(api_key),
            int(self.settings.effective_abs_request_timeout),
            bool(self.settings.effective_abs_verify_ssl),
        )

    async def _get_client(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> httpx.AsyncClient:
        base_url, api_key, timeout, verify_ssl = self._current_client_config(base_url, api_key)
        if not api_key or api_key == "change_me":
            raise RuntimeError("ABS_API_KEY is not configured")

        config = (base_url, api_key, timeout, verify_ssl)
        if self._client and self._client_config == config and not self._client.is_closed:
            return self._client

        await self.aclose()
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            verify=verify_ssl,
            headers={"Authorization": f"Bearer {api_key}"},
        )
        self._client_config = config
        return self._client

    async def aclose(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
        self._client = None
        self._client_config = None

    async def _get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> Any:
        client = await self._get_client(base_url=base_url, api_key=api_key)
        response = await client.get(path, params=params)
        response.raise_for_status()
        return response.json()

    async def _get_optional(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> Any | None:
        try:
            return await self._get(path, params=params, base_url=base_url, api_key=api_key)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise

    async def test_connection(self, base_url: str, api_key: str) -> None:
        try:
            await self._get("/api/me", base_url=base_url, api_key=api_key)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise
            await self._get("/api/libraries", base_url=base_url, api_key=api_key)

    async def get_online_users(self) -> dict[str, Any] | list[Any]:
        return await self._get("/api/users/online")

    async def get_users(self) -> dict[str, Any] | list[Any]:
        return await self._get("/api/users")

    async def _get_image(
        self,
        path: str,
        width: int = 300,
        height: int | None = None,
        image_format: str = "webp",
    ) -> tuple[bytes, str, str]:
        api_key = self._api_key()
        if not api_key or api_key == "change_me":
            raise RuntimeError("ABS_API_KEY is not configured")

        params: dict[str, Any] = {"width": width, "format": image_format}
        if height:
            params["height"] = height

        client = await self._get_client()
        response = await client.get(
            path,
            params=params,
            headers={"Accept": "image/webp,image/jpeg,image/*;q=0.8"},
        )
        response.raise_for_status()

        content_type = response.headers.get("content-type", "image/webp")
        cache_control = response.headers.get("cache-control", "public, max-age=86400")
        return response.content, content_type, cache_control

    async def get_item_cover(
        self,
        item_id: str,
        width: int = 300,
        height: int | None = None,
        image_format: str = "webp",
    ) -> tuple[bytes, str, str]:
        return await self._get_image(
            f"/api/items/{item_id}/cover",
            width=width,
            height=height,
            image_format=image_format,
        )

    async def get_author_image(
        self,
        author_id: str,
        width: int = 420,
        height: int | None = None,
        image_format: str = "webp",
    ) -> tuple[bytes, str, str]:
        paths = (
            f"/api/authors/{author_id}/image",
            f"/api/authors/{author_id}/cover",
            f"/api/authors/{author_id}/photo",
        )
        last_error: httpx.HTTPStatusError | None = None
        for path in paths:
            try:
                return await self._get_image(
                    path,
                    width=width,
                    height=height,
                    image_format=image_format,
                )
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    last_error = exc
                    continue
                raise
        if last_error:
            raise last_error
        raise RuntimeError("Author image not found")

    async def get_author(self, author_id: str, include: str = "items,series") -> dict[str, Any] | None:
        return await self._get_optional(
            f"/api/authors/{author_id}",
            params={"include": include} if include else None,
        )

    async def get_library_authors(self, library_id: str) -> dict[str, Any] | list[Any] | None:
        return await self._get_optional(f"/api/libraries/{library_id}/authors")

    async def find_author_in_libraries(self, author_name: str, library_ids: list[str]) -> dict[str, Any] | None:
        normalized = author_name.strip().casefold()
        if not normalized:
            return None

        for library_id in library_ids:
            if not library_id:
                continue
            payload = await self.get_library_authors(library_id)
            candidates: list[Any]
            if isinstance(payload, list):
                candidates = payload
            elif isinstance(payload, dict):
                candidates = payload.get("authors") or payload.get("results") or payload.get("items") or []
            else:
                candidates = []

            for row in candidates:
                if not isinstance(row, dict):
                    continue
                name = str(row.get("name") or row.get("authorName") or row.get("displayName") or "").strip()
                if name.casefold() == normalized:
                    return row
        return None

    async def get_libraries(self) -> dict[str, Any] | list[Any]:
        return await self._get("/api/libraries")

    async def get_library_items(self, library_id: str, limit: int = 5000, page: int = 0) -> dict[str, Any] | list[Any]:
        return await self._get(
            f"/api/libraries/{library_id}/items",
            params={"limit": limit, "page": page, "sort": "addedAt", "desc": 1},
        )

    async def get_item(self, item_id: str, expanded: bool = False) -> dict[str, Any]:
        params = {"expanded": 1} if expanded else None
        return await self._get(f"/api/items/{item_id}", params=params)

    async def get_user_listening_sessions(
        self,
        user_id: str,
        items_per_page: int = 50,
        page: int = 0,
    ) -> dict[str, Any]:
        return await self._get(
            f"/api/users/{user_id}/listening-sessions",
            params={"itemsPerPage": items_per_page, "page": page},
        )
