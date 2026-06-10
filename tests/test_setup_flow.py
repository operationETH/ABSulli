from fastapi import FastAPI
from fastapi.testclient import TestClient

import absulli.core.security as security
import absulli.web.routes as web_routes
from absulli.core.config import get_settings
from absulli.web.routes import router as web_router


def make_setup_client(monkeypatch):
    monkeypatch.setenv("ABSULLI_SECRET_KEY", "test-secret-key-that-is-long-enough-32")
    monkeypatch.delenv("ABS_URL", raising=False)
    monkeypatch.delenv("ABS_API_KEY", raising=False)
    monkeypatch.delenv("ABSULLI_AUTH_USERNAME", raising=False)
    monkeypatch.delenv("ABSULLI_AUTH_PASSWORD", raising=False)
    monkeypatch.delenv("ABSULLI_AUTH_PASSWORD_HASH", raising=False)
    get_settings.cache_clear()

    monkeypatch.setattr(web_routes, "setup_required", lambda: True)
    monkeypatch.setattr(security, "current_session_version", lambda: "test-session-version")

    async def fake_initial_import():
        return None

    monkeypatch.setattr(web_routes, "run_initial_setup_import", fake_initial_import)

    app = FastAPI()
    app.include_router(web_router)
    return TestClient(app)


def test_setup_submit_rejects_missing_or_invalid_csrf(monkeypatch):
    client = make_setup_client(monkeypatch)
    monkeypatch.setattr(web_routes, "validate_csrf_token", lambda request, token: False)

    response = client.post(
        "/setup",
        data={
            "abs_url": "http://audiobookshelf:13378",
            "abs_api_key": "secret-key",
            "admin_username": "admin",
            "admin_password": "password123",
            "confirm_password": "password123",
            "csrf_token": "bad-token",
        },
    )

    assert response.status_code == 403
    assert "Your setup form expired" in response.text
    assert "absulli_csrf" in response.headers["set-cookie"]


def test_setup_submit_validates_abs_url_api_key_and_password(monkeypatch):
    client = make_setup_client(monkeypatch)
    monkeypatch.setattr(web_routes, "validate_csrf_token", lambda request, token: True)

    bad_url = client.post(
        "/setup",
        data={
            "abs_url": "audiobookshelf:13378",
            "abs_api_key": "secret-key",
            "admin_username": "admin",
            "admin_password": "password123",
            "confirm_password": "password123",
            "csrf_token": "valid-token",
        },
    )
    assert bad_url.status_code == 400
    assert "Audiobookshelf URL must start" in bad_url.text

    missing_key = client.post(
        "/setup",
        data={
            "abs_url": "http://audiobookshelf:13378",
            "abs_api_key": "change_me",
            "admin_username": "admin",
            "admin_password": "password123",
            "confirm_password": "password123",
            "csrf_token": "valid-token",
        },
    )
    assert missing_key.status_code == 400
    assert "Audiobookshelf API key is required" in missing_key.text

    short_password = client.post(
        "/setup",
        data={
            "abs_url": "http://audiobookshelf:13378",
            "abs_api_key": "secret-key",
            "admin_username": "admin",
            "admin_password": "short",
            "confirm_password": "short",
            "csrf_token": "valid-token",
        },
    )
    assert short_password.status_code == 400
    assert "Admin password must be at least 8 characters" in short_password.text

    mismatch = client.post(
        "/setup",
        data={
            "abs_url": "http://audiobookshelf:13378",
            "abs_api_key": "secret-key",
            "admin_username": "admin",
            "admin_password": "password123",
            "confirm_password": "different123",
            "csrf_token": "valid-token",
        },
    )
    assert mismatch.status_code == 400
    assert "Admin passwords do not match" in mismatch.text


def test_setup_submit_saves_settings_sets_cookies_and_redirects(monkeypatch):
    client = make_setup_client(monkeypatch)
    monkeypatch.setattr(web_routes, "validate_csrf_token", lambda request, token: True)
    monkeypatch.setattr(web_routes, "password_hash", lambda password: f"hashed:{password}")

    captured = {}
    monkeypatch.setattr(web_routes, "set_setup_settings", lambda values: captured.update(values))

    response = client.post(
        "/setup",
        data={
            "abs_url": " http://audiobookshelf:13378/ ",
            "abs_api_key": " secret-key ",
            "admin_username": " kenny ",
            "admin_password": "password123",
            "confirm_password": "password123",
            "csrf_token": "valid-token",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert captured == {
        "setup_complete": "true",
        "abs_url": "http://audiobookshelf:13378",
        "abs_api_key": "secret-key",
        "auth_username": "kenny",
        "auth_password_hash": "hashed:password123",
    }

    set_cookie = response.headers["set-cookie"]
    assert "absulli_session" in set_cookie
    assert "absulli_csrf" in set_cookie
    assert "httponly" in set_cookie.lower()
