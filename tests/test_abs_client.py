import asyncio

import httpx
import pytest

from absulli.core.config import Settings
from absulli.http.abs_client import AudiobookshelfClient


def make_settings(**overrides):
    values = {
        "ABSULLI_SECRET_KEY": "x" * 32,
        "ABS_URL": "https://abs.example/",
        "ABS_API_KEY": "api-key",
        "ABS_REQUEST_TIMEOUT": 9,
        "ABS_VERIFY_SSL": False,
    }
    values.update(overrides)
    return Settings(**values)


class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None, content=b"image-bytes"):
        self.status_code = status_code
        self._payload = payload if payload is not None else {"ok": True}
        self.headers = headers or {}
        self.content = content
        self.request = httpx.Request("GET", "https://abs.example/test")

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            response = httpx.Response(self.status_code, request=self.request)
            raise httpx.HTTPStatusError("error", request=self.request, response=response)


def test_reuses_async_client_until_config_changes(monkeypatch):
    created = []

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.is_closed = False
            self.get_calls = []
            created.append(self)

        async def get(self, path, **kwargs):
            self.get_calls.append((path, kwargs))
            return FakeResponse(payload={"path": path})

        async def aclose(self):
            self.is_closed = True

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    client = AudiobookshelfClient(make_settings())
    asyncio.run(client.get_users())
    asyncio.run(client.get_libraries())
    asyncio.run(client._get("/api/me", api_key="new-api-key"))

    assert len(created) == 2
    assert created[0].is_closed is True
    assert created[0].kwargs == {
        "base_url": "https://abs.example",
        "timeout": 9,
        "verify": False,
        "headers": {"Authorization": "Bearer api-key"},
    }
    assert created[1].kwargs["headers"] == {"Authorization": "Bearer new-api-key"}
    assert [path for path, _kwargs in created[0].get_calls] == ["/api/users", "/api/libraries"]

    asyncio.run(client.aclose())
    assert created[1].is_closed is True


def test_missing_api_key_raises_before_creating_http_client(monkeypatch):
    created = []

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            created.append(kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    client = AudiobookshelfClient(make_settings(ABS_API_KEY="change_me"))

    with pytest.raises(RuntimeError, match="ABS_API_KEY is not configured"):
        asyncio.run(client.get_users())

    assert created == []


def test_get_library_items_sends_expected_path_and_params(monkeypatch):
    calls = []

    class FakeAsyncClient:
        is_closed = False

        def __init__(self, **kwargs):
            pass

        async def get(self, path, **kwargs):
            calls.append((path, kwargs))
            return FakeResponse(payload={"results": []})

        async def aclose(self):
            self.is_closed = True

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    client = AudiobookshelfClient(make_settings())
    result = asyncio.run(client.get_library_items("library-1", limit=123))

    assert result == {"results": []}
    assert calls == [
        (
            "/api/libraries/library-1/items",
            {"params": {"limit": 123, "page": 0, "sort": "addedAt", "desc": 1}},
        )
    ]


def test_get_collections_sends_expected_path(monkeypatch):
    calls = []

    class FakeAsyncClient:
        is_closed = False

        def __init__(self, **kwargs):
            pass

        async def get(self, path, **kwargs):
            calls.append((path, kwargs))
            return FakeResponse(payload={"collections": []})

        async def aclose(self):
            self.is_closed = True

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    client = AudiobookshelfClient(make_settings())
    result = asyncio.run(client.get_collections())

    assert result == {"collections": []}
    assert calls == [("/api/collections", {"params": None})]


def test_get_library_series_sends_expected_path_and_params(monkeypatch):
    calls = []

    class FakeAsyncClient:
        is_closed = False

        def __init__(self, **kwargs):
            pass

        async def get(self, path, **kwargs):
            calls.append((path, kwargs))
            return FakeResponse(payload={"results": []})

        async def aclose(self):
            self.is_closed = True

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    client = AudiobookshelfClient(make_settings())
    result = asyncio.run(client.get_library_series("library-1", limit=123, page=2))

    assert result == {"results": []}
    assert calls == [
        (
            "/api/libraries/library-1/series",
            {"params": {"limit": 123, "page": 2}},
        )
    ]


def test_optional_get_returns_none_only_for_404(monkeypatch):
    statuses = [404, 500]

    class FakeAsyncClient:
        is_closed = False

        def __init__(self, **kwargs):
            pass

        async def get(self, path, **kwargs):
            return FakeResponse(status_code=statuses.pop(0))

        async def aclose(self):
            self.is_closed = True

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    client = AudiobookshelfClient(make_settings())

    assert asyncio.run(client.get_author("missing-author")) is None
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(client.get_author("broken-author"))


def test_author_image_falls_back_across_supported_paths(monkeypatch):
    calls = []

    class FakeAsyncClient:
        is_closed = False

        def __init__(self, **kwargs):
            pass

        async def get(self, path, **kwargs):
            calls.append((path, kwargs))
            if len(calls) < 3:
                return FakeResponse(status_code=404)
            return FakeResponse(
                payload={},
                headers={"content-type": "image/jpeg", "cache-control": "public, max-age=60"},
                content=b"jpeg-bytes",
            )

        async def aclose(self):
            self.is_closed = True

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    client = AudiobookshelfClient(make_settings())
    content, content_type, cache_control = asyncio.run(
        client.get_author_image("author-1", width=222, height=333, image_format="jpeg")
    )

    assert content == b"jpeg-bytes"
    assert content_type == "image/jpeg"
    assert cache_control == "public, max-age=60"
    assert [path for path, _kwargs in calls] == [
        "/api/authors/author-1/image",
        "/api/authors/author-1/cover",
        "/api/authors/author-1/photo",
    ]
    assert calls[0][1] == {
        "params": {"width": 222, "format": "jpeg", "height": 333},
        "headers": {"Accept": "image/webp,image/jpeg,image/*;q=0.8"},
    }


def test_find_author_in_libraries_matches_supported_payload_shapes(monkeypatch):
    payloads = {
        "/api/libraries/lib-1/authors": {"authors": [{"id": "a1", "name": "Someone Else"}]},
        "/api/libraries/lib-2/authors": {"items": [{"id": "a2", "displayName": "Jane Author"}]},
    }

    class FakeAsyncClient:
        is_closed = False

        def __init__(self, **kwargs):
            pass

        async def get(self, path, **kwargs):
            return FakeResponse(payload=payloads[path])

        async def aclose(self):
            self.is_closed = True

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    client = AudiobookshelfClient(make_settings())
    result = asyncio.run(client.find_author_in_libraries(" jane author ", ["lib-1", "lib-2"]))

    assert result == {"id": "a2", "displayName": "Jane Author"}


def test_get_item_sends_expanded_param(monkeypatch):
    calls = []

    class FakeAsyncClient:
        is_closed = False

        def __init__(self, **kwargs):
            pass

        async def get(self, path, **kwargs):
            calls.append((path, kwargs))
            return FakeResponse(payload={"id": "podcast-1"})

        async def aclose(self):
            self.is_closed = True

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    client = AudiobookshelfClient(make_settings())
    result = asyncio.run(client.get_item("podcast-1", expanded=True))

    assert result == {"id": "podcast-1"}
    assert calls == [("/api/items/podcast-1", {"params": {"expanded": 1}})]
