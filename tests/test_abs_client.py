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


def mock_client(monkeypatch, handler):
    client = AudiobookshelfClient(make_settings())
    transport_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://abs.example")

    async def get_client(**kwargs):
        return transport_client

    monkeypatch.setattr(client, "_get_client", get_client)
    return client, transport_client


def test_reuses_async_client_until_config_changes(monkeypatch):
    created = []
    original = httpx.AsyncClient

    def handler(request):
        return httpx.Response(200, json={"path": request.url.path})

    def create(**kwargs):
        created.append(kwargs)
        return original(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", create)
    client = AudiobookshelfClient(make_settings())
    asyncio.run(client.get_users())
    first = client._client
    asyncio.run(client.get_libraries())
    asyncio.run(client._get("/api/me", api_key="new-api-key"))
    assert len(created) == 2
    assert first.is_closed
    assert created[0] == {
        "base_url": "https://abs.example", "timeout": 9, "verify": False,
        "headers": {"Authorization": "Bearer api-key"},
    }
    assert created[1]["headers"] == {"Authorization": "Bearer new-api-key"}
    asyncio.run(client.aclose())


def test_missing_api_key_raises_before_creating_http_client(monkeypatch):
    client = AudiobookshelfClient(make_settings(ABS_API_KEY="change_me"))
    with pytest.raises(RuntimeError, match="ABS_API_KEY is not configured"):
        asyncio.run(client.get_users())
    assert client._client is None


def test_get_library_items_sends_expected_path_and_params(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"results": []})

    client, transport = mock_client(monkeypatch, handler)
    assert asyncio.run(client.get_library_items("library-1", limit=123)) == {"results": []}
    assert calls[0].url.path == "/api/libraries/library-1/items"
    assert dict(calls[0].url.params) == {"limit": "123", "page": "0", "sort": "addedAt", "desc": "1"}
    asyncio.run(transport.aclose())


def test_get_collections_sends_expected_path(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"collections": []})

    client, transport = mock_client(monkeypatch, handler)
    assert asyncio.run(client.get_collections()) == {"collections": []}
    assert calls[0].url.path == "/api/collections"
    asyncio.run(transport.aclose())


def test_get_library_series_sends_expected_path_and_params(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"results": []})

    client, transport = mock_client(monkeypatch, handler)
    assert asyncio.run(client.get_library_series("library-1", limit=123, page=2)) == {"results": []}
    assert calls[0].url.path == "/api/libraries/library-1/series"
    assert dict(calls[0].url.params) == {"limit": "123", "page": "2"}
    asyncio.run(transport.aclose())


def test_optional_get_returns_none_only_for_404(monkeypatch):
    def handler(request):
        return httpx.Response(404 if "missing" in request.url.path else 500)

    client, transport = mock_client(monkeypatch, handler)
    assert asyncio.run(client.get_author("missing-author")) is None
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(client.get_author("broken-author"))
    asyncio.run(transport.aclose())


def test_author_image_falls_back_across_supported_paths(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) < 3:
            return httpx.Response(404)
        return httpx.Response(200, headers={"content-type": "image/jpeg", "cache-control": "public, max-age=60"}, content=b"jpeg-bytes")

    client, transport = mock_client(monkeypatch, handler)
    content, content_type, cache_control = asyncio.run(
        client.get_author_image("author-1", width=222, height=333, image_format="jpeg")
    )
    assert (content, content_type, cache_control) == (b"jpeg-bytes", "image/jpeg", "public, max-age=60")
    assert [request.url.path for request in calls] == [
        "/api/authors/author-1/image", "/api/authors/author-1/cover", "/api/authors/author-1/photo"
    ]
    assert dict(calls[0].url.params) == {"width": "222", "format": "jpeg", "height": "333"}
    assert calls[0].headers["accept"] == "image/webp,image/jpeg,image/*;q=0.8"
    asyncio.run(transport.aclose())


def test_find_author_in_libraries_matches_supported_payload_shapes(monkeypatch):
    payloads = {
        "/api/libraries/lib-1/authors": {"authors": [{"id": "a1", "name": "Someone Else"}]},
        "/api/libraries/lib-2/authors": {"items": [{"id": "a2", "displayName": "Jane Author"}]},
    }

    def handler(request):
        return httpx.Response(200, json=payloads[request.url.path])

    client, transport = mock_client(monkeypatch, handler)
    assert asyncio.run(client.find_author_in_libraries(" jane author ", ["lib-1", "lib-2"])) == {"id": "a2", "displayName": "Jane Author"}
    asyncio.run(transport.aclose())


def test_get_item_sends_expanded_param(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"id": "podcast-1"})

    client, transport = mock_client(monkeypatch, handler)
    assert asyncio.run(client.get_item("podcast-1", expanded=True)) == {"id": "podcast-1"}
    assert calls[0].url.path == "/api/items/podcast-1"
    assert dict(calls[0].url.params) == {"expanded": "1"}
    asyncio.run(transport.aclose())


def test_image_stream_rejects_oversized_content(monkeypatch):
    from absulli.http.abs_client import MAX_ABS_IMAGE_BYTES, UpstreamResponseTooLarge

    async def handler(request):
        return httpx.Response(200, content=b"x" * (MAX_ABS_IMAGE_BYTES + 1))

    transport = httpx.MockTransport(handler)
    async_client = httpx.AsyncClient(transport=transport, base_url="https://abs.example")
    client = AudiobookshelfClient(make_settings())

    async def get_client():
        return async_client

    monkeypatch.setattr(client, "_get_client", get_client)
    with pytest.raises(UpstreamResponseTooLarge):
        asyncio.run(client.get_item_cover("item-1"))
    asyncio.run(async_client.aclose())


def test_json_stream_rejects_oversized_content(monkeypatch):
    from absulli.http.abs_client import MAX_ABS_JSON_BYTES, UpstreamResponseTooLarge

    async def handler(request):
        return httpx.Response(200, headers={"content-length": str(MAX_ABS_JSON_BYTES + 1)}, content=b"{}")

    async_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://abs.example")
    client = AudiobookshelfClient(make_settings())

    async def get_client(**kwargs):
        return async_client

    monkeypatch.setattr(client, "_get_client", get_client)
    with pytest.raises(UpstreamResponseTooLarge):
        asyncio.run(client.get_libraries())
    asyncio.run(async_client.aclose())


def test_invalid_content_length_uses_stream_limit(monkeypatch):
    from absulli.http.abs_client import MAX_ABS_IMAGE_BYTES, UpstreamResponseTooLarge

    def handler(request):
        return httpx.Response(200, headers={"content-length": "bad"}, content=b"image")

    client, transport = mock_client(monkeypatch, handler)
    assert asyncio.run(client.get_item_cover("item-1"))[0] == b"image"
    asyncio.run(transport.aclose())

    def oversized_handler(request):
        return httpx.Response(200, headers={"content-length": "bad"}, content=b"x" * (MAX_ABS_IMAGE_BYTES + 1))

    client, transport = mock_client(monkeypatch, oversized_handler)
    with pytest.raises(UpstreamResponseTooLarge):
        asyncio.run(client.get_item_cover("item-1"))
    asyncio.run(transport.aclose())
