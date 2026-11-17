import asyncio

import httpx
import pytest

from absulli.core.abs_url import validate_abs_url
from absulli.core.config import Settings
from absulli.http.abs_client import AudiobookshelfClient


@pytest.mark.parametrize("url", [
    "http://192.168.0.9:13378",
    "http://192.168.0.106:1",
    "http://192.168.0.106:65535",
    "https://audiobooks.dembe.one",
    "http://192.168.0.106:8080",
    "http://audiobookshelf:80",
    "http://localhost:13378",
    "http://[::1]:13378",
    "https://abs.example.com/audiobookshelf/",
])
def test_valid_audiobookshelf_urls(url):
    assert validate_abs_url(url) == url.rstrip("/")


@pytest.mark.parametrize("url", [
    "ftp://example.com",
    "http://",
    "http://admin:password@example.com",
    "http://example.com:99999",
    "http://example.com:65536",
    "http://example.com:-1",
    "http://example.com:0",
    "http://[bad",
    "http://example.com/?url=http://localhost",
    "http://example.com/#fragment",
    "http://example.com\\@localhost",
    "http://example.com%2f@localhost",
    "http://example.com\n",
])
def test_invalid_audiobookshelf_urls(url):
    if url.endswith("\n"):
        url = "http://example.com\n/path"
    with pytest.raises(ValueError):
        validate_abs_url(url)


def test_audiobookshelf_client_does_not_follow_redirect(monkeypatch):
    seen = []
    real_client = httpx.AsyncClient

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(302, headers={"Location": "http://127.0.0.1:9000/private"})

    def mock_client(**kwargs):
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", mock_client)
    settings = Settings(
        ABSULLI_SECRET_KEY="x" * 32,
        ABS_URL="http://audiobookshelf:13378",
        ABS_API_KEY="test-key",
    )

    async def run():
        async with AudiobookshelfClient(settings) as client:
            with pytest.raises(httpx.HTTPStatusError) as exc:
                await client.test_connection("http://audiobookshelf:13378", "test-key")
            assert exc.value.response.status_code == 302

    asyncio.run(run())
    assert seen == ["http://audiobookshelf:13378/api/me"]
