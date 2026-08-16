from fastapi import FastAPI
from fastapi.testclient import TestClient

import absulli.core.setup_state as setup_state
import absulli.web.routes as web_routes
from absulli.core.config import get_settings
from absulli.web.routes import router as web_router


def make_client(monkeypatch, store=None):
    store = store if store is not None else {}
    monkeypatch.setenv("ABSULLI_SECRET_KEY", "test-secret-key-that-is-long-enough-32")
    for key in [
        "ABS_URL",
        "ABS_API_KEY",
        "ABS_VERIFY_SSL",
        "ABS_REQUEST_TIMEOUT",
        "ABS_POLL_INTERVAL",
        "ABS_HISTORY_POLL_INTERVAL",
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


def test_general_settings_tab_renders_editable_audiobookshelf_fields(monkeypatch):
    client, _store = make_client(
        monkeypatch,
        {
            "abs_url": "http://saved-abs:13378",
            "abs_api_key": "saved-key",
            "abs_verify_ssl": "false",
            "abs_request_timeout": "22",
            "abs_poll_interval": "20",
            "abs_history_poll_interval": "360",
        },
    )

    response = client.get("/settings?tab=general")

    assert response.status_code == 200
    assert "Audiobookshelf Connection" in response.text
    assert "Audiobookshelf URL" in response.text
    assert "http://saved-abs:13378" in response.text
    assert 'id="abs-api-key"' in response.text
    assert 'value="saved-key"' in response.text
    assert 'type="password"' in response.text
    assert 'data-abs-api-key-toggle' in response.text
    assert 'data-abs-api-key-copy' in response.text
    assert 'readonly' not in response.text.split('id="abs-api-key"', 1)[1].split('>', 1)[0]
    assert 'name="abs_request_timeout"' in response.text
    assert 'value="22"' in response.text
    assert 'name="abs_poll_interval"' in response.text
    assert 'value="20"' in response.text
    assert 'name="abs_history_poll_interval"' in response.text
    assert 'value="360"' in response.text
    assert "general-settings-grid" in response.text
    assert "ABSULLI_VERSION" not in response.text


def test_general_settings_save_persists_values_and_keeps_blank_api_key(monkeypatch):
    client, store = make_client(monkeypatch, {"abs_api_key": "saved-key"})

    response = client.post(
        "/settings/general",
        data={
            "csrf_token": "valid-token",
            "abs_url": " http://new-abs:13378/ ",
            "abs_api_key": "",
            "abs_request_timeout": "30",
            "abs_poll_interval": "25",
            "abs_history_poll_interval": "600",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/settings?tab=general&saved=general"
    assert store["abs_url"] == "http://new-abs:13378"
    assert store["abs_api_key"] == "saved-key"
    assert store["abs_verify_ssl"] == "false"
    assert store["abs_request_timeout"] == "30"
    assert store["abs_poll_interval"] == "25"
    assert store["abs_history_poll_interval"] == "600"


def test_general_settings_save_rejects_invalid_values(monkeypatch):
    client, store = make_client(monkeypatch, {"abs_api_key": "saved-key"})

    bad_url = client.post(
        "/settings/general",
        data={
            "csrf_token": "valid-token",
            "abs_url": "abs:13378",
            "abs_api_key": "saved-key",
            "abs_request_timeout": "15",
            "abs_poll_interval": "15",
            "abs_history_poll_interval": "300",
        },
        follow_redirects=False,
    )
    assert bad_url.status_code == 303
    assert "error=Audiobookshelf%20URL%20must%20start" in bad_url.headers["location"]

    bad_interval = client.post(
        "/settings/general",
        data={
            "csrf_token": "valid-token",
            "abs_url": "http://abs:13378",
            "abs_api_key": "saved-key",
            "abs_request_timeout": "15",
            "abs_poll_interval": "2",
            "abs_history_poll_interval": "300",
        },
        follow_redirects=False,
    )
    assert bad_interval.status_code == 303
    assert "Activity%20poll%20interval%20must%20be%20between%203%20and%203600" in bad_interval.headers["location"]
    assert store == {"abs_api_key": "saved-key"}


def test_general_settings_env_values_win_over_saved_settings(monkeypatch):
    store = {"abs_url": "http://saved-abs", "abs_api_key": "saved-key"}
    client, store = make_client(monkeypatch, store)
    monkeypatch.setenv("ABS_URL", "http://env-abs:13378")
    monkeypatch.setenv("ABS_API_KEY", "env-key")
    get_settings.cache_clear()

    page = client.get("/settings?tab=general")
    assert page.status_code == 200
    assert 'name="abs_url"' in page.text
    abs_url_input = page.text.split('name="abs_url"', 1)[1].split('>', 1)[0]
    assert 'value="Configured via .env read only"' in abs_url_input
    assert 'readonly' in abs_url_input
    assert "http://env-abs:13378" not in page.text
    assert "Managed by .env." not in page.text
    assert 'id="abs-api-key"' in page.text
    assert 'value="Configured via .env read only"' in page.text
    assert 'data-abs-api-key-value="env-key"' in page.text
    assert 'data-abs-api-key-from-env="true"' in page.text
    abs_input = page.text.split('id="abs-api-key"', 1)[1].split('>', 1)[0]
    assert 'readonly' in abs_input
    assert 'data-abs-api-key-toggle' in page.text
    assert 'data-abs-api-key-copy' in page.text

    response = client.post(
        "/settings/general",
        data={
            "csrf_token": "valid-token",
            "abs_url": "http://form-abs:13378",
            "abs_api_key": "form-key",
            "abs_verify_ssl": "on",
            "abs_request_timeout": "33",
            "abs_poll_interval": "30",
            "abs_history_poll_interval": "900",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert store["abs_url"] == "http://saved-abs"
    assert store["abs_api_key"] == "saved-key"
    assert store["abs_request_timeout"] == "33"


def test_general_connection_test_uses_unsaved_form_values(monkeypatch):
    calls = []
    client, _store = make_client(monkeypatch)

    class FakeAudiobookshelfClient:
        def __init__(self, settings):
            self.settings = settings

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def test_connection(self, base_url, api_key):
            calls.append((base_url, api_key))

    monkeypatch.setattr(web_routes, "AudiobookshelfClient", FakeAudiobookshelfClient)

    response = client.post(
        "/settings/general/test",
        data={
            "csrf_token": "valid-token",
            "abs_url": "http://form-abs:13378/",
            "abs_api_key": "form-key",
            "abs_verify_ssl": "on",
            "abs_request_timeout": "15",
            "abs_poll_interval": "15",
            "abs_history_poll_interval": "300",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "message": "Audiobookshelf connection successful."}
    assert calls == [("http://form-abs:13378", "form-key")]


def test_effective_general_settings_read_saved_values(monkeypatch):
    store = {
        "abs_url": "http://saved-abs:13378",
        "abs_api_key": "saved-key",
        "abs_verify_ssl": "false",
        "abs_request_timeout": "44",
        "abs_poll_interval": "45",
        "abs_history_poll_interval": "900",
    }
    make_client(monkeypatch, store)

    settings = get_settings()

    assert settings.effective_abs_url == "http://saved-abs:13378"
    assert settings.effective_abs_api_key == "saved-key"
    assert settings.effective_abs_verify_ssl is False
    assert settings.effective_abs_request_timeout == 44
    assert settings.effective_abs_poll_interval == 45
    assert settings.effective_abs_history_poll_interval == 900
