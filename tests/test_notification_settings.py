import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

import absulli.core.setup_state as setup_state
import absulli.web.routes as web_routes
import absulli.web.settings as web_settings
from absulli.core.config import Settings, get_settings
from absulli.notifiers.manager import NotificationManager
from absulli.web.routes import router as web_router


def make_client(monkeypatch, store=None, patch_csrf=True):
    store = store if store is not None else {}
    monkeypatch.setenv("ABSULLI_SECRET_KEY", "test-secret-key-that-is-long-enough-32")
    monkeypatch.delenv("GOTIFY_URL", raising=False)
    monkeypatch.delenv("GOTIFY_TOKEN", raising=False)
    get_settings.cache_clear()

    def fake_get(key, default=""):
        return store.get(key, default)

    def fake_set_many(values):
        store.update(values)

    monkeypatch.setattr(web_routes, "get_setup_setting", fake_get)
    monkeypatch.setattr(web_routes, "set_setup_settings", fake_set_many)
    monkeypatch.setattr(setup_state, "get_setup_setting", fake_get)
    monkeypatch.setattr(setup_state, "set_setup_settings", fake_set_many)
    if patch_csrf:
        monkeypatch.setattr(web_routes, "validate_csrf_token", lambda request, token: True)

    app = FastAPI()
    app.include_router(web_router)
    return TestClient(app), store


def csrf_from_settings_page(response):
    marker = 'name="csrf_token" value="'
    start = response.text.index(marker) + len(marker)
    end = response.text.index('"', start)
    return response.text[start:end]


def test_settings_page_sets_matching_csrf_cookie(monkeypatch):
    client, _store = make_client(monkeypatch, patch_csrf=False)

    response = client.get("/settings?tab=notifications")
    csrf_token = csrf_from_settings_page(response)

    assert response.status_code == 200
    assert csrf_token
    assert client.cookies.get("absulli_csrf") == csrf_token


def test_gotify_save_accepts_token_from_settings_page(monkeypatch):
    client, store = make_client(monkeypatch, patch_csrf=False)

    page = client.get("/settings?tab=notifications")
    csrf_token = csrf_from_settings_page(page)

    response = client.post(
        "/settings/notifications/gotify",
        data={
            "enabled": "on",
            "gotify_url": "http://gotify.local",
            "gotify_token": "app-token",
            "csrf_token": csrf_token,
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/settings?tab=notifications&saved=gotify&agent=gotify"
    assert store["gotify_url"] == "http://gotify.local"
    assert store["gotify_token"] == "app-token"


def test_notification_placeholders_are_clean(monkeypatch):
    client, _store = make_client(monkeypatch)

    response = client.get("/settings?tab=notifications&agent=gotify")

    assert response.status_code == 200
    assert 'name="gotify_url"' in response.text
    assert 'name="gotify_token"' in response.text
    assert 'placeholder="http://gotify:80"' not in response.text
    assert 'placeholder="Application token"' not in response.text
    assert "Leave blank to keep the saved value." not in response.text


def test_settings_page_shows_gotify_form_with_saved_values(monkeypatch):
    client, _store = make_client(
        monkeypatch,
        {"gotify_url": "http://gotify.local", "gotify_token": "saved-token"},
    )

    response = client.get("/settings?tab=notifications")

    assert response.status_code == 200
    assert "Notification Settings" in response.text
    assert "Gotify" in response.text
    assert 'value="http://gotify.local"' in response.text
    assert "Configured. Leave blank to keep" in response.text
    assert "Enabled" in response.text


def test_gotify_save_trims_and_persists_settings(monkeypatch):
    client, store = make_client(monkeypatch)

    response = client.post(
        "/settings/notifications/gotify",
        data={
            "enabled": "on",
            "gotify_url": " http://gotify.local/ ",
            "gotify_token": " app-token ",
            "csrf_token": "valid-token",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/settings?tab=notifications&saved=gotify&agent=gotify"
    assert store["gotify_url"] == "http://gotify.local"
    assert store["gotify_token"] == "app-token"


def test_gotify_save_blank_token_keeps_existing_token(monkeypatch):
    client, store = make_client(monkeypatch, {"gotify_token": "saved-token"})

    response = client.post(
        "/settings/notifications/gotify",
        data={
            "enabled": "on",
            "gotify_url": "http://gotify.local",
            "gotify_token": "",
            "csrf_token": "valid-token",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert store["gotify_url"] == "http://gotify.local"
    assert store["gotify_token"] == "saved-token"


def test_gotify_disable_clears_saved_values(monkeypatch):
    client, store = make_client(
        monkeypatch,
        {"gotify_url": "http://gotify.local", "gotify_token": "saved-token"},
    )

    response = client.post(
        "/settings/notifications/gotify",
        data={"csrf_token": "valid-token"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert store["gotify_url"] == ""
    assert store["gotify_token"] == ""


def test_gotify_env_values_win_over_saved_settings(monkeypatch):
    monkeypatch.setenv("ABSULLI_SECRET_KEY", "test-secret-key-that-is-long-enough-32")
    monkeypatch.setenv("GOTIFY_URL", "http://env-gotify")
    monkeypatch.setenv("GOTIFY_TOKEN", "env-token")
    get_settings.cache_clear()
    monkeypatch.setattr(setup_state, "get_setup_setting", lambda key, default="": {"gotify_url": "http://db-gotify", "gotify_token": "db-token"}.get(key, default))

    settings = get_settings()

    assert settings.effective_gotify_url == "http://env-gotify"
    assert settings.effective_gotify_token == "env-token"

    get_settings.cache_clear()


def test_gotify_test_uses_unsaved_form_values(monkeypatch):
    calls = []
    client, _store = make_client(monkeypatch)

    class FakeGotifyAgent:
        def __init__(self, base_url, token):
            self.base_url = base_url
            self.token = token

        async def send(self, title, message, extra=None):
            calls.append((self.base_url, self.token, title, message, extra))

    monkeypatch.setattr(web_settings, "GotifyAgent", FakeGotifyAgent)

    response = client.post(
        "/settings/notifications/gotify/test",
        data={
            "enabled": "on",
            "gotify_url": "http://gotify.local/",
            "gotify_token": "form-token",
            "csrf_token": "valid-token",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "message": "Gotify test notification sent."}
    assert calls == [
        (
            "http://gotify.local",
            "form-token",
            "ABSulli test notification",
            "Gotify notifications are configured correctly.",
            {"event_type": "test"},
        )
    ]


def test_notification_manager_uses_saved_gotify_settings(monkeypatch):
    monkeypatch.setenv("ABSULLI_SECRET_KEY", "test-secret-key-that-is-long-enough-32")
    monkeypatch.delenv("GOTIFY_URL", raising=False)
    monkeypatch.delenv("GOTIFY_TOKEN", raising=False)
    get_settings.cache_clear()
    monkeypatch.setattr(setup_state, "get_setup_setting", lambda key, default="": {"gotify_url": "http://db-gotify", "gotify_token": "db-token"}.get(key, default))

    manager = NotificationManager(get_settings())
    agents = manager.agents()

    assert len(agents) == 1
    assert agents[0].base_url == "http://db-gotify"
    assert agents[0].token == "db-token"

    get_settings.cache_clear()


def test_settings_page_shows_all_notification_agent_tabs(monkeypatch):
    client, _store = make_client(monkeypatch)

    response = client.get("/settings?tab=notifications")

    assert response.status_code == 200
    for label in ["Email", "Discord", "Gotify", "ntfy.sh", "Pushbullet", "Pushover", "Slack", "Telegram", "Webhook"]:
        assert label in response.text
    assert "Coming soon" not in response.text


def test_notification_agent_tabs_render_service_icons(monkeypatch):
    client, _store = make_client(monkeypatch)

    response = client.get("/settings?tab=notifications")

    assert response.status_code == 200
    assert response.text.count('<span class="agent-tab-icon" aria-hidden="true">') == 9
    assert response.text.count("<svg") >= 9
    assert "/static/img/notifications/" not in response.text


def test_discord_agent_save_persists_webhook(monkeypatch):
    client, store = make_client(monkeypatch)

    response = client.post(
        "/settings/notifications/discord",
        data={
            "enabled": "on",
            "discord_webhook_url": " https://discord.example/webhook ",
            "csrf_token": "valid-token",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/settings?tab=notifications&saved=discord&agent=discord"
    assert store["discord_enabled"] == "true"
    assert store["discord_webhook_url"] == "https://discord.example/webhook"


def test_notification_agent_test_uses_unsaved_discord_value(monkeypatch):
    calls = []
    client, _store = make_client(monkeypatch)

    class FakeDiscordAgent:
        def __init__(self, webhook_url):
            self.webhook_url = webhook_url

        async def send(self, title, message, extra=None):
            calls.append((self.webhook_url, title, message, extra))

    monkeypatch.setattr(web_settings, "DiscordAgent", FakeDiscordAgent)

    response = client.post(
        "/settings/notifications/discord/test",
        data={
            "enabled": "on",
            "discord_webhook_url": "https://discord.example/webhook",
            "csrf_token": "valid-token",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "message": "Discord test notification sent."}
    assert calls == [
        (
            "https://discord.example/webhook",
            "ABSulli test notification",
            "Discord notifications are configured correctly.",
            {"event_type": "test"},
        )
    ]


def test_missing_agent_required_field_returns_error(monkeypatch):
    client, _store = make_client(monkeypatch)

    response = client.post(
        "/settings/notifications/slack/test",
        data={"enabled": "on", "slack_webhook_url": "", "csrf_token": "valid-token"},
    )

    assert response.status_code == 400
    assert response.json() == {"ok": False, "message": "Slack required fields are missing."}


def test_notification_events_are_global_and_visible_for_every_agent(monkeypatch):
    client, _store = make_client(monkeypatch)

    response = client.get('/settings?tab=notifications')

    assert response.status_code == 200
    for setting in [
        'notify_playback_started',
        'notify_playback_stopped',
        'notify_abs_connection_failed',
        'notify_abs_connection_restored',
        'notify_new_book',
        'notify_new_podcast',
        'notify_new_podcast_episode',
    ]:
        assert response.text.count(f'name="{setting}"') == 9
    assert response.text.count('Notification Events') == 9


def test_notification_event_settings_use_global_names_when_saved_from_any_agent(monkeypatch):
    client, store = make_client(monkeypatch)

    response = client.post(
        '/settings/notifications/discord',
        data={
            'enabled': 'on',
            'discord_webhook_url': 'https://discord.example/webhook',
            'notify_playback_started': 'on',
            'notify_abs_connection_restored': 'on',
            'csrf_token': 'valid-token',
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert store['notify_playback_started'] == 'true'
    assert store['notify_playback_stopped'] == 'false'
    assert store['notify_abs_connection_failed'] == 'false'
    assert store['notify_abs_connection_restored'] == 'true'
    assert store['notify_new_book'] == 'false'
    assert store['notify_new_podcast'] == 'false'
    assert store['notify_new_podcast_episode'] == 'false'
    assert 'gotify_notify_playback_started' not in store
    assert 'gotify_notify_playback_stopped' not in store


def test_notification_events_default_to_unchecked(monkeypatch):
    client, _store = make_client(monkeypatch)

    response = client.get('/settings?tab=notifications')

    assert response.status_code == 200
    for setting in [
        'notify_playback_started',
        'notify_playback_stopped',
        'notify_abs_connection_failed',
        'notify_abs_connection_restored',
        'notify_new_book',
        'notify_new_podcast',
        'notify_new_podcast_episode',
    ]:
        marker = f'name="{setting}"'
        start = response.text.index(marker)
        tag_start = response.text.rfind('<input', 0, start)
        tag_end = response.text.index('>', start)
        input_tag = response.text[tag_start:tag_end]
        assert 'checked' not in input_tag


def test_notification_manager_defaults_all_events_disabled(monkeypatch):
    client, _store = make_client(monkeypatch)
    assert client

    from absulli.notifiers.manager import event_enabled

    assert event_enabled('playback_start') is False
    assert event_enabled('playback_stop') is False
    assert event_enabled('abs_connection_failed') is False
    assert event_enabled('abs_connection_restored') is False
    assert event_enabled('new_book') is False
    assert event_enabled('new_podcast') is False
    assert event_enabled('new_podcast_episode') is False


def test_notification_manager_returns_true_for_stored_enabled_event(monkeypatch):
    client, _store = make_client(monkeypatch, {"notify_playback_started": "true", "notify_new_book": "true", "notify_new_podcast": "true", "notify_new_podcast_episode": "true"})
    assert client

    from absulli.notifiers.manager import event_enabled

    assert event_enabled("playback_start") is True
    assert event_enabled("playback_stop") is False
    assert event_enabled("new_book") is True
    assert event_enabled("new_podcast") is True
    assert event_enabled("new_podcast_episode") is True


def test_settings_general_tab_is_default(monkeypatch):
    client, _store = make_client(monkeypatch)

    response = client.get('/settings')

    assert response.status_code == 200
    assert 'href="/settings?tab=general"' in response.text
    assert 'href="/settings?tab=network"' in response.text
    assert 'href="/settings?tab=users"' in response.text
    assert 'href="/settings?tab=notifications"' in response.text
    assert 'href="/settings?tab=about"' in response.text
    assert '<h2>Audiobookshelf Connection</h2>' in response.text
    assert 'Notification Settings' not in response.text


def test_settings_notification_tab_renders_notification_agents(monkeypatch):
    client, _store = make_client(monkeypatch)

    response = client.get('/settings?tab=notifications')

    assert response.status_code == 200
    assert 'Notification Settings' in response.text
    assert 'data-agent-tab="gotify"' in response.text
    assert 'Notification Events' in response.text
    assert '<h2>Audiobookshelf Connection</h2>' not in response.text


def test_settings_tabs_render_network_users_and_about_sections(monkeypatch):
    client, _store = make_client(monkeypatch)

    network = client.get('/settings?tab=network')
    users = client.get('/settings?tab=users')
    about = client.get('/settings?tab=about')

    assert network.status_code == 200
    assert '<h2>Network Settings</h2>' in network.text
    assert 'Trust Reverse Proxy Headers' in network.text
    assert 'HTTPS Secure Cookies' in network.text

    assert users.status_code == 200
    assert '<h2>Users</h2>' in users.text
    assert 'Admin Username' in users.text
    assert 'Login Protection' in users.text

    assert about.status_code == 200
    assert '<h2>About</h2>' in about.text
    assert 'Database Path' in about.text
    assert 'Migration Version' in about.text
    assert 'Migrations' not in about.text
