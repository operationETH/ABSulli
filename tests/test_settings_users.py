from fastapi import FastAPI
from fastapi.testclient import TestClient

import absulli.core.setup_state as setup_state
import absulli.core.security as security
import absulli.web.routes as web_routes
from absulli.core.config import get_settings
from absulli.core.security import auth_username, verify_login
from absulli.web.routes import router as web_router


def make_client(monkeypatch, store=None):
    store = store if store is not None else {}
    monkeypatch.setenv("ABSULLI_SECRET_KEY", "test-secret-key-that-is-long-enough-32")
    for key in [
        "ABSULLI_AUTH_USERNAME",
        "ABSULLI_AUTH_PASSWORD",
        "ABSULLI_AUTH_PASSWORD_HASH",
        "ABSULLI_AUTH_SESSION_MINUTES",
        "ABSULLI_AUTH_LOGIN_MAX_ATTEMPTS",
        "ABSULLI_AUTH_LOGIN_WINDOW_SECONDS",
        "ABSULLI_AUTH_LOGIN_LOCKOUT_SECONDS",
        "ABSULLI_API_KEY",
        "ABSULLI_API_TOKEN",
    ]:
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
    monkeypatch.setattr(security, "get_setup_setting", fake_get)
    monkeypatch.setattr(web_routes, "validate_csrf_token", lambda request, token: True)
    monkeypatch.setattr(web_routes, "rotate_session_version", lambda: "rotated")
    monkeypatch.setattr(web_routes, "set_session_cookie", lambda response, username: response.headers.__setitem__("x-test-session-user", username))

    app = FastAPI()
    app.include_router(web_router)
    return TestClient(app), store


def test_users_settings_tab_renders_editable_auth_fields(monkeypatch):
    client, _store = make_client(
        monkeypatch,
        {
            "auth_username": "kenny",
            "auth_password_hash": web_routes.password_hash("old-password"),
            "auth_session_minutes": "480",
            "auth_login_max_attempts": "5",
            "auth_login_window_seconds": "300",
            "auth_login_lockout_seconds": "600",
        },
    )

    response = client.get("/settings?tab=users")

    assert response.status_code == 200
    assert "Admin Username" in response.text
    assert "Current Admin Password" in response.text
    assert 'name="current_password"' in response.text
    assert 'name="auth_username"' in response.text
    assert 'value="kenny"' in response.text
    assert 'placeholder="Required to change password"' in response.text
    assert "Configured — leave blank to keep" not in response.text
    assert "Leave blank to keep the saved value." not in response.text
    assert "Revoke existing browser sessions after saving" in response.text
    assert "Auth Summary" in response.text
    assert "Login policy details can be changed in a .env file." in response.text
    assert "Login Protection" in response.text
    assert 'name="auth_session_minutes"' not in response.text
    assert 'name="auth_login_max_attempts"' not in response.text


def test_users_settings_save_updates_username_and_password(monkeypatch):
    client, store = make_client(monkeypatch, {"auth_password_hash": web_routes.password_hash("old-password")})

    response = client.post(
        "/settings/users",
        data={
            "csrf_token": "valid-token",
            "auth_username": "newadmin",
            "current_password": "old-password",
            "auth_password": "new-password",
            "auth_password_confirm": "new-password",
            "auth_session_minutes": "600",
            "auth_login_max_attempts": "6",
            "auth_login_window_seconds": "400",
            "auth_login_lockout_seconds": "800",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/settings?tab=users&saved=users"
    assert response.headers["x-test-session-user"] == "newadmin"
    assert store["auth_username"] == "newadmin"
    assert store["auth_password_hash"] != "old-password"
    assert auth_username() == "newadmin"
    assert verify_login("newadmin", "new-password") is True


def test_users_settings_blank_password_keeps_existing_value(monkeypatch):
    existing_hash = web_routes.password_hash("old-password")
    client, store = make_client(monkeypatch, {"auth_password_hash": existing_hash})

    response = client.post(
        "/settings/users",
        data={
            "csrf_token": "valid-token",
            "auth_username": "admin",
            "current_password": "",
            "auth_password": "",
            "auth_password_confirm": "",
            "auth_session_minutes": "720",
            "auth_login_max_attempts": "8",
            "auth_login_window_seconds": "900",
            "auth_login_lockout_seconds": "900",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert store["auth_password_hash"] == existing_hash
    assert "x-test-session-user" not in response.headers


def test_users_settings_rejects_password_mismatch(monkeypatch):
    client, store = make_client(monkeypatch, {"auth_password_hash": web_routes.password_hash("old-password")})

    response = client.post(
        "/settings/users",
        data={
            "csrf_token": "valid-token",
            "auth_username": "admin",
            "current_password": "old-password",
            "auth_password": "new-password",
            "auth_password_confirm": "different-password",
            "auth_session_minutes": "720",
            "auth_login_max_attempts": "8",
            "auth_login_window_seconds": "900",
            "auth_login_lockout_seconds": "900",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "error=Admin%20passwords%20do%20not%20match" in response.headers["location"]
    assert "auth_username" not in store


def test_users_settings_rejects_password_change_without_current_password(monkeypatch):
    client, store = make_client(monkeypatch, {"auth_password_hash": web_routes.password_hash("old-password")})

    response = client.post(
        "/settings/users",
        data={
            "csrf_token": "valid-token",
            "auth_username": "admin",
            "current_password": "",
            "auth_password": "new-password",
            "auth_password_confirm": "new-password",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "error=Current%20password%20is%20required%20to%20set%20a%20new%20one" in response.headers["location"]
    assert verify_login("admin", "old-password") is True
    assert verify_login("admin", "new-password") is False


def test_users_settings_rejects_password_change_with_wrong_current_password(monkeypatch):
    client, store = make_client(monkeypatch, {"auth_password_hash": web_routes.password_hash("old-password")})

    response = client.post(
        "/settings/users",
        data={
            "csrf_token": "valid-token",
            "auth_username": "admin",
            "current_password": "wrong-password",
            "auth_password": "new-password",
            "auth_password_confirm": "new-password",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "error=Current%20password%20is%20required%20to%20set%20a%20new%20one" in response.headers["location"]
    assert verify_login("admin", "old-password") is True
    assert verify_login("admin", "new-password") is False


def test_users_settings_env_values_win_over_saved_settings(monkeypatch):
    client, store = make_client(monkeypatch, {"auth_username": "saved"})
    monkeypatch.setenv("ABSULLI_AUTH_USERNAME", "envadmin")
    get_settings.cache_clear()

    page = client.get("/settings?tab=users")
    assert page.status_code == 200
    assert "envadmin" in page.text
    assert "Managed by .env." in page.text

    response = client.post(
        "/settings/users",
        data={
            "csrf_token": "valid-token",
            "auth_username": "formadmin",
            "auth_password": "",
            "auth_password_confirm": "",
            "auth_session_minutes": "600",
            "auth_login_max_attempts": "7",
            "auth_login_window_seconds": "700",
            "auth_login_lockout_seconds": "800",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert store["auth_username"] == "saved"


def test_effective_user_settings_read_saved_values(monkeypatch):
    make_client(
        monkeypatch,
        {
            "auth_session_minutes": "333",
            "auth_login_max_attempts": "4",
            "auth_login_window_seconds": "222",
            "auth_login_lockout_seconds": "444",
        },
    )

    settings = get_settings()

    assert settings.effective_auth_session_minutes == 333
    assert settings.auth_max_age_seconds == 333 * 60
    assert settings.effective_auth_login_max_attempts == 4
    assert settings.effective_auth_login_window_seconds == 222
    assert settings.effective_auth_login_lockout_seconds == 444
