from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

import absulli.core.setup_rate_limit as limiter
import absulli.web.routes as routes
from absulli.core.config import get_settings
from absulli.web.routes import router


@pytest.fixture(autouse=True)
def reset_setup_limiter():
    with limiter._lock:
        limiter._attempts.clear()
    yield
    with limiter._lock:
        limiter._attempts.clear()


def test_setup_connection_limit_resets_after_window(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(limiter, "monotonic", lambda: clock[0])

    for _ in range(10):
        assert limiter.setup_connection_retry_after("192.0.2.1") == 0

    assert limiter.setup_connection_retry_after("192.0.2.1") == 60
    clock[0] += 35
    assert limiter.setup_connection_retry_after("192.0.2.1") == 25
    assert limiter.setup_connection_retry_after("192.0.2.2") == 0
    clock[0] += 25
    assert limiter.setup_connection_retry_after("192.0.2.1") == 0


def test_setup_connection_limit_bounds_client_storage(monkeypatch):
    monkeypatch.setattr(limiter, "MAX_CLIENT_KEYS", 3)
    for index in range(8):
        assert limiter.setup_connection_retry_after(f"192.0.2.{index}") == 0
    assert len(limiter._attempts) == 3


def test_setup_connection_endpoint_limits_outbound_requests(monkeypatch):
    monkeypatch.setenv("ABSULLI_SECRET_KEY", "test-secret-key-that-is-long-enough-32")
    monkeypatch.delenv("ABS_URL", raising=False)
    monkeypatch.delenv("ABS_API_KEY", raising=False)
    get_settings.cache_clear()
    monkeypatch.setattr(routes, "setup_required", lambda: True)
    monkeypatch.setattr(routes, "validate_csrf_token", lambda request, token: True)

    calls = []

    class FakeClient:
        def __init__(self, settings):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def test_connection(self, url, key):
            calls.append(url)

    monkeypatch.setattr(routes, "AudiobookshelfClient", FakeClient)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app, client=("192.0.2.10", 12345))
    data = {"abs_url": "http://192.168.0.106:13378", "abs_api_key": "test-key", "csrf_token": "ok"}

    invalid = client.post("/setup/test-connection", data={**data, "abs_url": "ftp://invalid"})
    assert invalid.status_code == 400
    assert calls == []

    for _ in range(10):
        response = client.post("/setup/test-connection", data=data)
        assert response.status_code == 200

    limited = client.post("/setup/test-connection", data=data)
    assert limited.status_code == 429
    assert 1 <= int(limited.headers["retry-after"]) <= 60
    assert limited.json()["ok"] is False
    assert len(calls) == 10

    another_client = TestClient(app, client=("192.0.2.11", 12345))
    assert another_client.post("/setup/test-connection", data=data).status_code == 200
    assert len(calls) == 11


def test_setup_connection_limit_ignores_untrusted_forwarded_headers(monkeypatch):
    monkeypatch.setenv("ABSULLI_SECRET_KEY", "test-secret-key-that-is-long-enough-32")
    monkeypatch.setenv("ABSULLI_TRUST_PROXY", "true")
    monkeypatch.setenv("ABSULLI_TRUSTED_PROXY_IPS", "10.2.0.3/32")
    get_settings.cache_clear()
    monkeypatch.setattr(routes, "setup_required", lambda: True)
    monkeypatch.setattr(routes, "validate_csrf_token", lambda request, token: True)

    class FakeClient:
        def __init__(self, settings):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def test_connection(self, url, key):
            pass

    monkeypatch.setattr(routes, "AudiobookshelfClient", FakeClient)
    app = FastAPI()
    app.include_router(router)
    data = {"abs_url": "http://audiobookshelf:13378", "abs_api_key": "test-key", "csrf_token": "ok"}
    untrusted = TestClient(app, client=("10.2.0.4", 12345))
    for n in range(10):
        assert untrusted.post("/setup/test-connection", data=data, headers={"X-Forwarded-For": f"198.51.100.{n}"}).status_code == 200
    assert untrusted.post("/setup/test-connection", data=data, headers={"X-Forwarded-For": "198.51.100.99"}).status_code == 429

    trusted = TestClient(app, client=("10.2.0.3", 12345))
    assert trusted.post("/setup/test-connection", data=data, headers={"X-Forwarded-For": "198.51.100.99, 192.0.2.11"}).status_code == 200
    assert trusted.post("/setup/test-connection", data=data, headers={"X-Forwarded-For": "198.51.100.99, 192.0.2.12"}).status_code == 200
