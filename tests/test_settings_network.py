from fastapi import FastAPI
from fastapi.testclient import TestClient

import absulli.core.setup_state as setup_state
import absulli.web.routes as web_routes
from absulli.core.config import get_settings
from absulli.core.security import client_key
from absulli.web.routes import router as web_router


def make_client(monkeypatch, store=None):
    store = store if store is not None else {}
    monkeypatch.setenv("ABSULLI_SECRET_KEY", "test-secret-key-that-is-long-enough-32")
    for key in [
        "ABSULLI_TRUST_PROXY",
        "ABSULLI_COOKIE_SECURE",
        "ABSULLI_METRICS_TOKEN",
        "ABSULLI_SECURITY_HSTS_ENABLED",
        "ABSULLI_SECURITY_HSTS_MAX_AGE_SECONDS",
        "ABSULLI_SECURITY_HSTS_INCLUDE_SUBDOMAINS",
        "ABSULLI_SECURITY_HSTS_PRELOAD",
        "ABSULLI_CORS_ALLOWED_ORIGINS",
        "ABSULLI_CORS_ALLOW_CREDENTIALS",
        "ABSULLI_CORS_ALLOWED_METHODS",
        "ABSULLI_CORS_ALLOWED_HEADERS",
    ]:
        monkeypatch.delenv(key, raising=False)
    get_settings.cache_clear()

    def fake_get(key, default=""):
        return store.get(key, default)

    def fake_set_many(values):
        store.update(values)

    monkeypatch.setattr(web_routes, "get_setup_setting", fake_get)
    monkeypatch.setattr(web_routes, "set_setup_settings", fake_set_many)
    monkeypatch.setattr(setup_state, "get_setup_setting", fake_get)
    monkeypatch.setattr(setup_state, "set_setup_settings", fake_set_many)
    monkeypatch.setattr(web_routes, "validate_csrf_token", lambda request, token: True)

    app = FastAPI()
    app.include_router(web_router)
    return TestClient(app), store


def test_network_settings_tab_renders_clean_network_fields(monkeypatch):
    client, _store = make_client(
        monkeypatch,
        {
            "trust_proxy": "true",
            "cookie_secure": "true",
            "metrics_token": "stored-token",
        },
    )

    response = client.get("/settings?tab=network")

    assert response.status_code == 200
    assert "Network Settings" in response.text
    assert "Trust Reverse Proxy Headers" in response.text
    assert "Use X-Forwarded-For" in response.text
    assert 'name="trust_proxy" checked' in response.text
    assert "HTTPS Secure Cookies" in response.text
    assert "Metrics Token" in response.text
    assert "Configured — leave blank to keep" in response.text
    assert "HSTS Header" in response.text
    assert "CORS Allowed Origins" in response.text
    assert "Restart Required" in response.text
    assert "Methods, headers, and credentials use defaults" in response.text
    assert "Advanced CORS options can be changed in a .env file" in response.text
    assert "Content Security Policy" not in response.text
    assert "HSTS Max Age Seconds" not in response.text
    assert "CORS Allowed Methods" not in response.text
    assert "CORS Allowed Headers" not in response.text


def test_network_settings_save_persists_simple_values_and_keeps_blank_metrics_token(monkeypatch):
    client, store = make_client(monkeypatch, {"metrics_token": "saved-token"})

    response = client.post(
        "/settings/network",
        data={
            "csrf_token": "valid-token",
            "trust_proxy": "on",
            "cookie_secure": "on",
            "metrics_token": "",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/settings?tab=network&saved=network"
    assert store["trust_proxy"] == "true"
    assert store["cookie_secure"] == "true"
    assert store["metrics_token"] == "saved-token"
    assert store["security_hsts_enabled"] == "false"
    assert store["cors_allowed_origins"] == ""
    assert "security_hsts_max_age_seconds" not in store
    assert "cors_allowed_methods" not in store
    assert "cors_allowed_headers" not in store


def test_network_settings_rejects_change_me_metrics_token(monkeypatch):
    client, store = make_client(monkeypatch)

    response = client.post(
        "/settings/network",
        data={
            "csrf_token": "valid-token",
            "metrics_token": "change_me",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "error=Metrics%20token%20cannot%20be%20change_me" in response.headers["location"]
    assert store == {}


def test_network_settings_env_values_win_over_saved_settings(monkeypatch):
    store = {"trust_proxy": "true", "metrics_token": "saved-token"}
    client, store = make_client(monkeypatch, store)
    monkeypatch.setenv("ABSULLI_TRUST_PROXY", "false")
    monkeypatch.setenv("ABSULLI_METRICS_TOKEN", "env-token")
    get_settings.cache_clear()

    page = client.get("/settings?tab=network")
    assert page.status_code == 200
    assert "Managed by .env" in page.text

    response = client.post(
        "/settings/network",
        data={
            "csrf_token": "valid-token",
            "trust_proxy": "on",
            "cookie_secure": "on",
            "metrics_token": "new-token",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert store["trust_proxy"] == "true"
    assert store["metrics_token"] == "saved-token"
    assert store["cookie_secure"] == "true"


def test_effective_network_settings_read_saved_values(monkeypatch):
    make_client(
        monkeypatch,
        {
            "trust_proxy": "true",
            "cookie_secure": "true",
            "metrics_token": "saved-token",
        },
    )

    settings = get_settings()

    assert settings.effective_trust_proxy is True
    assert settings.session_cookie_secure is True
    assert settings.effective_metrics_token == "saved-token"


def test_client_key_uses_saved_trust_proxy_setting(monkeypatch):
    make_client(monkeypatch, {"trust_proxy": "true"})

    class Request:
        headers = {"X-Forwarded-For": "203.0.113.10, 10.0.0.1"}
        client = type("Client", (), {"host": "127.0.0.1"})()

    assert client_key(Request()) == "203.0.113.10"


def test_network_settings_save_persists_cors_origins_and_hsts_toggle(monkeypatch):
    client, store = make_client(monkeypatch)

    response = client.post(
        "/settings/network",
        data={
            "csrf_token": "valid-token",
            "security_hsts_enabled": "on",
            "cors_allowed_origins": " https://app.example.com/ , http://localhost:3000 ",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert store["security_hsts_enabled"] == "true"
    assert store["cors_allowed_origins"] == "https://app.example.com,http://localhost:3000"
    assert "cors_allow_credentials" not in store
    assert "cors_allowed_methods" not in store
    assert "cors_allowed_headers" not in store


def test_network_settings_rejects_invalid_cors_origin(monkeypatch):
    client, store = make_client(monkeypatch)

    response = client.post(
        "/settings/network",
        data={
            "csrf_token": "valid-token",
            "cors_allowed_origins": "app.example.com",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "error=CORS%20allowed%20origins%20must%20start%20with%20http%3A//%20or%20https%3A//" in response.headers["location"]
    assert store == {}


def test_cors_and_hsts_detail_settings_use_defaults_unless_env_overrides(monkeypatch):
    make_client(
        monkeypatch,
        {
            "cors_allowed_origins": "https://gui.example.com",
            "cors_allow_credentials": "true",
            "cors_allowed_methods": "GET,DELETE",
            "cors_allowed_headers": "X-Bad",
            "security_hsts_enabled": "true",
            "security_hsts_max_age_seconds": "42",
            "security_hsts_include_subdomains": "false",
            "security_hsts_preload": "true",
        },
    )

    settings = get_settings()

    assert settings.effective_cors_allowed_origins == "https://gui.example.com"
    assert settings.cors_allowed_origins_list == ["https://gui.example.com"]
    assert settings.effective_cors_allow_credentials is False
    assert settings.cors_allowed_methods_list == ["GET", "POST", "OPTIONS"]
    assert settings.cors_allowed_headers_list == [
        "Authorization",
        "Content-Type",
        "X-Absulli-Api-Token",
        "X-Absulli-Metrics-Token",
        "X-CSRF-Token",
    ]
    assert settings.effective_security_hsts_enabled is True
    assert settings.effective_security_hsts_max_age_seconds == 31536000
    assert settings.effective_security_hsts_include_subdomains is True
    assert settings.effective_security_hsts_preload is False

    monkeypatch.setenv("ABSULLI_CORS_ALLOW_CREDENTIALS", "true")
    monkeypatch.setenv("ABSULLI_CORS_ALLOWED_METHODS", "GET,POST,DELETE,OPTIONS")
    monkeypatch.setenv("ABSULLI_CORS_ALLOWED_HEADERS", "Authorization,Content-Type,X-Custom")
    monkeypatch.setenv("ABSULLI_SECURITY_HSTS_MAX_AGE_SECONDS", "123")
    monkeypatch.setenv("ABSULLI_SECURITY_HSTS_INCLUDE_SUBDOMAINS", "false")
    monkeypatch.setenv("ABSULLI_SECURITY_HSTS_PRELOAD", "true")
    get_settings.cache_clear()
    settings = get_settings()

    assert settings.effective_cors_allow_credentials is True
    assert settings.cors_allowed_methods_list == ["GET", "POST", "DELETE", "OPTIONS"]
    assert settings.cors_allowed_headers_list == ["Authorization", "Content-Type", "X-Custom"]
    assert settings.effective_security_hsts_max_age_seconds == 123
    assert settings.effective_security_hsts_include_subdomains is False
    assert settings.effective_security_hsts_preload is True
