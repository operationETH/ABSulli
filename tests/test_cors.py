from fastapi import FastAPI
from fastapi.testclient import TestClient

import absulli.core.setup_state as setup_state
from absulli.core.config import get_settings
from absulli.core.cors import DynamicCORSMiddleware


def test_cors_saved_origins_apply_without_restart(monkeypatch):
    store = {}
    monkeypatch.setenv("ABSULLI_SECRET_KEY", "test-secret-key-that-is-long-enough-32")
    monkeypatch.delenv("ABSULLI_CORS_ALLOWED_ORIGINS", raising=False)
    get_settings.cache_clear()

    def fake_get_if_available(key, default=""):
        return store.get(key, default)

    monkeypatch.setattr(setup_state, "get_setup_setting_if_available", fake_get_if_available)

    app = FastAPI()
    app.add_middleware(DynamicCORSMiddleware)

    @app.get("/ping")
    def ping():
        return {"ok": True}

    client = TestClient(app)

    response = client.get("/ping", headers={"Origin": "https://app.example.com"})
    assert "access-control-allow-origin" not in response.headers

    store["cors_allowed_origins"] = "https://app.example.com"
    response = client.get("/ping", headers={"Origin": "https://app.example.com"})
    assert response.headers["access-control-allow-origin"] == "https://app.example.com"

    store["cors_allowed_origins"] = "https://other.example.com"
    response = client.get("/ping", headers={"Origin": "https://app.example.com"})
    assert "access-control-allow-origin" not in response.headers

    response = client.get("/ping", headers={"Origin": "https://other.example.com"})
    assert response.headers["access-control-allow-origin"] == "https://other.example.com"

    get_settings.cache_clear()
