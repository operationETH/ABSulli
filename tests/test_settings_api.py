from fastapi import FastAPI
from fastapi.testclient import TestClient

import absulli.core.setup_state as setup_state
import absulli.web.routes as web_routes
from absulli.core.config import get_settings
from absulli.web.routes import router as web_router


def make_client(monkeypatch, store=None):
    store = store if store is not None else {}
    monkeypatch.setenv("ABSULLI_SECRET_KEY", "test-secret-key-that-is-long-enough-32")
    for key in ["ABSULLI_API_ENABLED", "ABSULLI_API_KEY", "ABSULLI_API_TOKEN"]:
        monkeypatch.delenv(key, raising=False)
    get_settings.cache_clear()

    def fake_get(key, default=""):
        return store.get(key, default)

    def fake_set(key, value):
        store[key] = value

    def fake_set_many(values):
        store.update(values)

    monkeypatch.setattr(web_routes, "get_setup_setting", fake_get)
    monkeypatch.setattr(web_routes, "set_setup_setting", fake_set)
    monkeypatch.setattr(web_routes, "set_setup_settings", fake_set_many)
    monkeypatch.setattr(setup_state, "get_setup_setting", fake_get)
    monkeypatch.setattr(setup_state, "set_setup_setting", fake_set)
    monkeypatch.setattr(setup_state, "set_setup_settings", fake_set_many)
    monkeypatch.setattr(web_routes, "validate_csrf_token", lambda request, token: True)

    app = FastAPI()
    app.include_router(web_router)
    return TestClient(app), store


def test_api_tab_generates_and_renders_key(monkeypatch):
    client, store = make_client(monkeypatch)

    response = client.get("/settings?tab=api")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "API Access" in response.text
    assert 'name="api_enabled"' in response.text
    assert 'id="absulli-api-key"' in response.text
    assert store["api_token"]


def test_api_settings_enable_and_disable(monkeypatch):
    client, store = make_client(monkeypatch, {"api_token": "saved-key"})

    enabled = client.post(
        "/settings/api",
        data={"csrf_token": "valid-token", "api_enabled": "on"},
        follow_redirects=False,
    )
    assert enabled.status_code == 303
    assert store["api_enabled"] == "true"
    assert store["api_token"] == "saved-key"

    disabled = client.post(
        "/settings/api",
        data={"csrf_token": "valid-token"},
        follow_redirects=False,
    )
    assert disabled.status_code == 303
    assert store["api_enabled"] == "false"


def test_api_regenerate_replaces_saved_key(monkeypatch):
    client, store = make_client(monkeypatch, {"api_token": "old-key"})

    response = client.post(
        "/settings/api/regenerate",
        data={"csrf_token": "valid-token"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/settings?tab=api&saved=api"
    assert store["api_token"] != "old-key"
    assert len(store["api_token"]) >= 32


def test_api_environment_values_are_read_only(monkeypatch):
    client, store = make_client(
        monkeypatch,
        {"api_enabled": "false", "api_token": "saved-key"},
    )
    monkeypatch.setenv("ABSULLI_API_ENABLED", "true")
    monkeypatch.setenv("ABSULLI_API_KEY", "environment-key")
    get_settings.cache_clear()

    page = client.get("/settings?tab=api")
    assert page.status_code == 200
    assert "environment-key" in page.text
    assert "Managed by .env" in page.text
    assert "Regenerate Key" not in page.text

    response = client.post(
        "/settings/api",
        data={"csrf_token": "valid-token"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert store["api_enabled"] == "false"
    assert store["api_token"] == "saved-key"