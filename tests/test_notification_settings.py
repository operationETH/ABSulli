import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.datastructures import FormData

import absulli.core.setup_state as setup_state
import absulli.web.routes as web_routes
import absulli.web.settings as web_settings
from absulli.core.config import Settings, get_settings
from absulli.database.models import Library, NotificationDelivery, NotificationEvent
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
            "gotify_include_cover_art": "on",
            "gotify_open_in_audiobookshelf": "on",
            "csrf_token": csrf_token,
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/settings?tab=notifications&saved=gotify&agent=gotify"
    assert store["gotify_url"] == "http://gotify.local"
    assert store["gotify_token"] == "app-token"
    assert store["gotify_include_cover_art"] == "true"
    assert store["gotify_open_in_audiobookshelf"] == "true"


def test_notification_placeholders_are_clean(monkeypatch):
    client, _store = make_client(monkeypatch)

    response = client.get("/settings?tab=notifications&agent=gotify")

    assert response.status_code == 200
    assert 'name="gotify_url"' in response.text
    assert 'name="gotify_token"' in response.text
    assert 'name="gotify_include_cover_art"' in response.text
    assert 'name="gotify_open_in_audiobookshelf"' in response.text
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


def test_email_settings_show_starttls_option(monkeypatch):
    client, _store = make_client(monkeypatch)

    response = client.get("/settings?tab=notifications&agent=email")

    assert response.status_code == 200
    assert 'name="email_use_starttls"' in response.text
    assert "Upgrade the connection to TLS, normally on port 587." in response.text


def test_email_settings_reject_multiple_tls_modes(monkeypatch):
    client, _store = make_client(monkeypatch)

    response = client.post(
        "/settings/notifications/email/test",
        data={
            "enabled": "on",
            "email_smtp_host": "smtp.example.com",
            "email_smtp_port": "587",
            "email_from": "absulli@example.com",
            "email_to": "user@example.com",
            "email_use_tls": "on",
            "email_use_starttls": "on",
            "csrf_token": "valid-token",
        },
    )

    assert response.status_code == 400
    assert response.json() == {"ok": False, "message": "Choose either SSL/TLS or STARTTLS, not both"}


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


def test_notification_events_use_agent_specific_names(monkeypatch):
    client, _store = make_client(monkeypatch)

    response = client.get('/settings?tab=notifications')

    assert response.status_code == 200
    for agent_id in ["email", "discord", "gotify", "ntfy", "pushbullet", "pushover", "slack", "telegram", "webhook"]:
        for setting in [
            'notify_playback_started',
            'notify_playback_stopped',
            'notify_abs_connection_failed',
            'notify_abs_connection_restored',
            'notify_book_added_to_collection',
            'notify_book_added_to_series',
            'notify_new_book',
            'notify_new_collection',
            'notify_new_podcast',
            'notify_new_podcast_episode',
            'notify_new_series',
        ]:
            assert response.text.count(f'name="{agent_id}_{setting}"') == 1
    assert response.text.count('Notification Events') == 9


def test_notification_events_and_templates_use_display_order(monkeypatch):
    client, _store = make_client(monkeypatch)
    assert client
    expected = [
        "Playback Started",
        "Playback Stopped",
        "New Book Added",
        "New Series Added",
        "Book Added to Series",
        "New Collection Added",
        "Book Added to Collection",
        "New Podcast Added",
        "New Podcast Episode Added",
        "Audiobookshelf Connection Failed",
        "Audiobookshelf Connection Restored",
    ]

    events = web_settings.notification_events_context("discord")
    templates = web_settings.notification_templates_context("discord")

    assert [event["label"] for event in events] == expected
    assert [template["label"] for template in templates] == expected


def test_notification_event_settings_save_only_for_selected_agent(monkeypatch):
    client, store = make_client(monkeypatch)

    response = client.post(
        '/settings/notifications/discord',
        data={
            'enabled': 'on',
            'discord_webhook_url': 'https://discord.example/webhook',
            'discord_notify_playback_started': 'on',
            'discord_notify_abs_connection_restored': 'on',
            'csrf_token': 'valid-token',
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert store['discord_notify_playback_started'] == 'true'
    assert store['discord_notify_playback_stopped'] == 'false'
    assert store['discord_notify_abs_connection_failed'] == 'false'
    assert store['discord_notify_abs_connection_restored'] == 'true'
    assert store['discord_notify_book_added_to_collection'] == 'false'
    assert store['discord_notify_book_added_to_series'] == 'false'
    assert store['discord_notify_new_book'] == 'false'
    assert store['discord_notify_new_collection'] == 'false'
    assert store['discord_notify_new_podcast'] == 'false'
    assert store['discord_notify_new_podcast_episode'] == 'false'
    assert store['discord_notify_new_series'] == 'false'
    assert 'gotify_notify_playback_started' not in store
    assert 'notify_playback_started' not in store


def test_notification_events_preserve_legacy_global_values_until_agent_is_saved(monkeypatch):
    client, _store = make_client(monkeypatch, {'notify_new_book': 'true'})

    response = client.get('/settings?tab=notifications')

    assert response.status_code == 200
    for agent_id in ["email", "discord", "gotify", "ntfy", "pushbullet", "pushover", "slack", "telegram", "webhook"]:
        marker = f'name="{agent_id}_notify_new_book"'
        start = response.text.index(marker)
        tag_start = response.text.rfind('<input', 0, start)
        tag_end = response.text.index('>', start)
        assert 'checked' in response.text[tag_start:tag_end]


def test_notification_events_default_to_unchecked(monkeypatch):
    client, _store = make_client(monkeypatch)

    response = client.get('/settings?tab=notifications')

    assert response.status_code == 200
    for agent_id in ["email", "discord", "gotify", "ntfy", "pushbullet", "pushover", "slack", "telegram", "webhook"]:
        for setting in [
            'notify_playback_started',
            'notify_playback_stopped',
            'notify_abs_connection_failed',
            'notify_abs_connection_restored',
            'notify_book_added_to_collection',
            'notify_book_added_to_series',
            'notify_new_book',
            'notify_new_collection',
            'notify_new_podcast',
            'notify_new_podcast_episode',
            'notify_new_series',
        ]:
            marker = f'name="{agent_id}_{setting}"'
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
    assert event_enabled('book_added_to_collection') is False
    assert event_enabled('book_added_to_series') is False
    assert event_enabled('new_book') is False
    assert event_enabled('new_collection') is False
    assert event_enabled('new_podcast') is False
    assert event_enabled('new_podcast_episode') is False
    assert event_enabled('new_series') is False


def test_notification_manager_supports_agent_specific_events(monkeypatch):
    client, _store = make_client(
        monkeypatch,
        {
            'gotify_notify_playback_started': 'true',
            'discord_notify_playback_started': 'false',
            'discord_notify_new_book': 'true',
        },
    )
    assert client

    from absulli.notifiers.manager import agent_event_enabled, event_enabled

    assert agent_event_enabled('gotify', 'playback_start') is True
    assert agent_event_enabled('discord', 'playback_start') is False
    assert agent_event_enabled('discord', 'new_book') is True
    assert event_enabled('playback_start') is True
    assert event_enabled('new_book') is True
    assert event_enabled('playback_stop') is False


def test_notification_manager_sends_only_to_agents_enabled_for_event(monkeypatch):
    client, _store = make_client(
        monkeypatch,
        {
            'gotify_notify_new_book': 'true',
            'discord_notify_new_book': 'false',
        },
    )
    assert client
    calls = []

    class FakeAgent:
        def __init__(self, name):
            self.name = name

        async def send(self, title, message, extra=None):
            calls.append((self.name, title, message, extra))

    class FakeDb:
        def __init__(self):
            self.events = []
            self.deliveries = []

        def add(self, record):
            if isinstance(record, NotificationEvent):
                record.id = 42
                self.events.append(record)
            if isinstance(record, NotificationDelivery):
                self.deliveries.append(record)

        def commit(self):
            pass

    manager = NotificationManager(get_settings())
    monkeypatch.setattr(
        manager,
        'named_agents',
        lambda: [('gotify', FakeAgent('gotify')), ('discord', FakeAgent('discord'))],
    )
    db = FakeDb()

    asyncio.run(manager.notify(db, 'new_book', 'New book added', 'Example'))

    assert calls == [('gotify', 'New book added', 'Example', {'event_type': 'new_book'})]
    assert len(db.events) == 1
    assert db.events[0].delivered is True
    assert len(db.deliveries) == 1
    assert db.deliveries[0].event_id == 42
    assert db.deliveries[0].agent == 'gotify'
    assert db.deliveries[0].delivered is True
    assert db.deliveries[0].error == ''


def test_notification_manager_records_delivery_status_per_agent(monkeypatch):
    client, _store = make_client(
        monkeypatch,
        {
            'gotify_notify_new_book': 'true',
            'discord_notify_new_book': 'true',
        },
    )
    assert client

    class FakeAgent:
        def __init__(self, error=''):
            self.error = error

        async def send(self, title, message, extra=None):
            if self.error:
                raise RuntimeError(self.error)

    class FakeDb:
        def __init__(self):
            self.events = []
            self.deliveries = []

        def add(self, record):
            if isinstance(record, NotificationEvent):
                record.id = 42
                self.events.append(record)
            if isinstance(record, NotificationDelivery):
                self.deliveries.append(record)

        def commit(self):
            pass

    manager = NotificationManager(get_settings())
    monkeypatch.setattr(
        manager,
        'named_agents',
        lambda: [
            ('gotify', FakeAgent()),
            ('discord', FakeAgent('Discord rejected the webhook')),
        ],
    )
    db = FakeDb()

    asyncio.run(manager.notify(db, 'new_book', 'New book added', 'Example'))

    assert len(db.events) == 1
    assert db.events[0].delivered is True
    assert len(db.deliveries) == 2

    gotify = next(delivery for delivery in db.deliveries if delivery.agent == 'gotify')
    discord = next(delivery for delivery in db.deliveries if delivery.agent == 'discord')

    assert gotify.event_id == 42
    assert gotify.delivered is True
    assert gotify.error == ''
    assert discord.event_id == 42
    assert discord.delivered is False
    assert discord.error == 'Discord rejected the webhook'


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


def test_notification_agent_failure_log_redacts_secret_url(monkeypatch, caplog):
    client, _store = make_client(monkeypatch)

    class FakeDiscordAgent:
        def __init__(self, webhook_url):
            self.webhook_url = webhook_url

        async def send(self, title, message, extra=None):
            raise RuntimeError(
                "Client error '401 Unauthorized' for url "
                "'https://discord.com/api/webhooks/123/secret-token'"
            )

    monkeypatch.setattr(web_settings, "DiscordAgent", FakeDiscordAgent)

    response = client.post(
        "/settings/notifications/discord/test",
        data={
            "enabled": "on",
            "discord_webhook_url": "https://discord.com/api/webhooks/123/secret-token",
            "csrf_token": "valid-token",
        },
    )

    assert response.status_code == 502
    assert "401 Unauthorized" in caplog.text
    assert "secret-token" not in caplog.text


def test_notification_library_scope_defaults_to_all_libraries(monkeypatch):
    client, _store = make_client(monkeypatch)
    assert client
    libraries = [
        Library(abs_library_id="lib-books", name="Audiobooks", media_type="book"),
        Library(abs_library_id="lib-podcasts", name="Podcasts", media_type="podcast"),
    ]

    context = web_settings.notification_library_scope_context("discord", libraries)

    assert context["all_libraries"] is True
    assert [library["selected"] for library in context["libraries"]] == [False, False]


def test_notification_library_scope_reads_saved_library_ids(monkeypatch):
    client, _store = make_client(
        monkeypatch,
        {"discord_notification_libraries": '["lib-podcasts"]'},
    )
    assert client
    libraries = [
        Library(abs_library_id="lib-books", name="Audiobooks", media_type="book"),
        Library(abs_library_id="lib-podcasts", name="Podcasts", media_type="podcast"),
    ]

    context = web_settings.notification_library_scope_context("discord", libraries)

    assert context["all_libraries"] is False
    assert [library["selected"] for library in context["libraries"]] == [False, True]


def test_notification_library_value_from_form_keeps_only_detected_libraries(monkeypatch):
    client, _store = make_client(monkeypatch)
    assert client
    libraries = [
        Library(abs_library_id="lib-books", name="Audiobooks", media_type="book"),
        Library(abs_library_id="lib-podcasts", name="Podcasts", media_type="podcast"),
    ]
    form = FormData(
        [
            ("discord_library_scope_present", "1"),
            ("discord_library_ids", "lib-podcasts"),
            ("discord_library_ids", "unknown-library"),
        ]
    )

    value = web_settings.notification_library_value_from_form("discord", form, libraries)

    assert value == '["lib-podcasts"]'


def test_notification_library_value_from_form_all_libraries_uses_wildcard(monkeypatch):
    client, _store = make_client(monkeypatch)
    assert client
    form = FormData(
        [
            ("discord_library_scope_present", "1"),
            ("discord_all_libraries", "on"),
        ]
    )

    value = web_settings.notification_library_value_from_form("discord", form, [])

    assert value == "*"


def test_notification_manager_filters_agents_by_library(monkeypatch):
    client, _store = make_client(
        monkeypatch,
        {
            "gotify_notify_new_book": "true",
            "discord_notify_new_book": "true",
            "gotify_notification_libraries": '["lib-books"]',
            "discord_notification_libraries": '["lib-other"]',
        },
    )
    assert client
    calls = []

    class FakeAgent:
        def __init__(self, name):
            self.name = name

        async def send(self, title, message, extra=None):
            calls.append((self.name, title, message, extra))

    class FakeDb:
        def __init__(self):
            self.events = []
            self.deliveries = []

        def add(self, record):
            if isinstance(record, NotificationEvent):
                record.id = 42
                self.events.append(record)
            if isinstance(record, NotificationDelivery):
                self.deliveries.append(record)

        def commit(self):
            pass

    manager = NotificationManager(get_settings())
    monkeypatch.setattr(
        manager,
        "named_agents",
        lambda: [("gotify", FakeAgent("gotify")), ("discord", FakeAgent("discord"))],
    )
    db = FakeDb()

    asyncio.run(
        manager.notify(
            db,
            "new_book",
            "New book added",
            "Example",
            library_id="lib-books",
        )
    )

    assert calls == [
        (
            "gotify",
            "New book added",
            "Example",
            {"event_type": "new_book"},
        )
    ]
    assert [delivery.agent for delivery in db.deliveries] == ["gotify"]


def test_pushbullet_open_in_audiobookshelf_uses_configured_abs_url(monkeypatch):
    client, _store = make_client(
        monkeypatch,
        {
            "abs_url": "https://abs.example/audiobookshelf",
            "pushbullet_notify_new_book": "true",
            "pushbullet_open_in_audiobookshelf": "true",
        },
    )
    assert client
    calls = []

    class FakeAgent:
        async def send(self, title, message, extra=None):
            calls.append((title, message, extra))

    class FakeDb:
        def add(self, record):
            if isinstance(record, NotificationEvent):
                record.id = 42

        def commit(self):
            pass

    manager = NotificationManager(get_settings())
    monkeypatch.setattr(manager, "named_agents", lambda: [("pushbullet", FakeAgent())])

    asyncio.run(
        manager.notify(
            FakeDb(),
            "new_book",
            "New book added",
            "Example",
            context={"item_id": "book 1"},
        )
    )

    assert calls[0][2]["click_url"] == "https://abs.example/audiobookshelf/item/book%201"


def test_gotify_open_in_audiobookshelf_uses_configured_abs_url(monkeypatch):
    client, _store = make_client(
        monkeypatch,
        {
            "abs_url": "https://abs.example/audiobookshelf",
            "gotify_notify_new_book": "true",
            "gotify_open_in_audiobookshelf": "true",
        },
    )
    assert client
    calls = []

    class FakeAgent:
        async def send(self, title, message, extra=None):
            calls.append((title, message, extra))

    class FakeDb:
        def add(self, record):
            if isinstance(record, NotificationEvent):
                record.id = 42

        def commit(self):
            pass

    manager = NotificationManager(get_settings())
    monkeypatch.setattr(manager, "named_agents", lambda: [("gotify", FakeAgent())])

    asyncio.run(
        manager.notify(
            FakeDb(),
            "new_book",
            "New book added",
            "Example",
            context={"item_id": "book 1"},
        )
    )

    assert calls == [
        (
            "New book added",
            "Example",
            {
                "event_type": "new_book",
                "item_id": "book 1",
                "click_url": "https://abs.example/audiobookshelf/item/book%201",
            },
        )
    ]


def test_notification_manager_library_scope_does_not_filter_connection_events(monkeypatch):
    client, _store = make_client(
        monkeypatch,
        {
            "discord_notify_abs_connection_failed": "true",
            "discord_notification_libraries": "[]",
        },
    )
    assert client
    calls = []

    class FakeAgent:
        async def send(self, title, message, extra=None):
            calls.append((title, message, extra))

    class FakeDb:
        def add(self, record):
            if isinstance(record, NotificationEvent):
                record.id = 42

        def commit(self):
            pass

    manager = NotificationManager(get_settings())
    monkeypatch.setattr(manager, "named_agents", lambda: [("discord", FakeAgent())])

    asyncio.run(
        manager.notify(
            FakeDb(),
            "abs_connection_failed",
            "Audiobookshelf connection failed",
            "Connection failed",
        )
    )

    assert calls == [
        (
            "Audiobookshelf connection failed",
            "Connection failed",
            {"event_type": "abs_connection_failed"},
        )
    ]


def test_notification_manager_scoped_library_event_requires_library_id(monkeypatch):
    client, _store = make_client(
        monkeypatch,
        {
            "discord_notify_new_book": "true",
            "discord_notification_libraries": '["lib-books"]',
        },
    )
    assert client
    calls = []

    class FakeAgent:
        async def send(self, title, message, extra=None):
            calls.append((title, message, extra))

    manager = NotificationManager(get_settings())
    monkeypatch.setattr(manager, "named_agents", lambda: [("discord", FakeAgent())])

    class FakeDb:
        def add(self, record):
            raise AssertionError("No notification record should be created")

        def commit(self):
            raise AssertionError("No notification commit should occur")

    asyncio.run(manager.notify(FakeDb(), "new_book", "New book added", "Example"))

    assert calls == []


def test_notification_library_value_from_legacy_form_keeps_existing_scope(monkeypatch):
    client, _store = make_client(
        monkeypatch,
        {"discord_notification_libraries": '["lib-books"]'},
    )
    assert client

    value = web_settings.notification_library_value_from_form("discord", FormData(), [])

    assert value == '["lib-books"]'


def test_notification_template_save_persists_custom_event_templates(monkeypatch):
    client, store = make_client(monkeypatch)

    response = client.post(
        "/settings/notifications/gotify",
        data={
            "enabled": "on",
            "gotify_url": "http://gotify.local",
            "gotify_token": "app-token",
            "gotify_new_book_title_template": "New: {title}",
            "gotify_new_book_body_template": "{author} | {library}",
            "csrf_token": "valid-token",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert store["gotify_new_book_title_template"] == "New: {title}"
    assert store["gotify_new_book_body_template"] == "{author} | {library}"


def test_notification_template_save_rejects_unknown_variable(monkeypatch):
    client, store = make_client(monkeypatch)

    response = client.post(
        "/settings/notifications/gotify",
        data={
            "enabled": "on",
            "gotify_url": "http://gotify.local",
            "gotify_token": "app-token",
            "gotify_new_book_title_template": "New: {titel}",
            "csrf_token": "valid-token",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "Unknown%20notification%20variable" in response.headers["location"]
    assert "gotify_new_book_title_template" not in store


def test_notification_settings_page_shows_template_variables(monkeypatch):
    client, _store = make_client(monkeypatch)

    response = client.get("/settings?tab=notifications&agent=gotify")

    assert response.status_code == 200
    marker = 'data-agent-form="gotify"'
    form_start = response.text.index(marker)
    form_end = response.text.index("</form>", form_start)
    form_html = response.text[form_start:form_end]
    assert "Message Templates" in form_html
    assert "{audiobookshelf_url}" in form_html
    assert "{cover_url}" in form_html
    assert "{asin}" in form_html
    assert "{isbn}" in form_html
    assert "{isbn_url}" not in form_html
    assert "{audible_url}" not in form_html
    assert "{itunes_id}" in response.text
    assert "{apple_podcasts_url}" in response.text
    assert "Number of books in a collection or series" in response.text
    assert 'name="gotify_new_book_title_template"' in response.text
    assert 'name="gotify_new_book_body_template"' in response.text


def test_notification_manager_renders_custom_templates(monkeypatch):
    client, _store = make_client(
        monkeypatch,
        {
            "gotify_notify_new_book": "true",
            "gotify_new_book_title_template": "New: {title}",
            "gotify_new_book_body_template": "{author} added to {library} {series}",
        },
    )
    assert client
    calls = []

    class FakeAgent:
        async def send(self, title, message, extra=None):
            calls.append((title, message, extra))

    class FakeDb:
        def add(self, record):
            if isinstance(record, NotificationEvent):
                record.id = 42

        def commit(self):
            pass

    manager = NotificationManager(get_settings())
    monkeypatch.setattr(manager, "named_agents", lambda: [("gotify", FakeAgent())])

    asyncio.run(
        manager.notify(
            FakeDb(),
            "new_book",
            "New book added",
            "Default body",
            context={
                "item_id": "book-1",
                "title": "Example Book",
                "author": "Example Author",
                "series": "Example Series",
                "library_name": "Audiobooks",
            },
        )
    )

    assert calls[0][0] == "New: Example Book"
    assert calls[0][1] == "Example Author added to Audiobooks Example Series"


def test_notification_manager_builds_external_metadata_links(monkeypatch):
    client, _store = make_client(
        monkeypatch,
        {
            "discord_notify_new_book": "true",
            "discord_new_book_body_template": "{asin} {isbn} {audible_url} {isbn_url} {apple_podcasts_url}",
        },
    )
    assert client
    calls = []

    class FakeAgent:
        async def send(self, title, message, extra=None):
            calls.append((title, message, extra))

    class FakeDb:
        def add(self, record):
            if isinstance(record, NotificationEvent):
                record.id = 42

        def commit(self):
            pass

    manager = NotificationManager(get_settings())
    monkeypatch.setattr(manager, "named_agents", lambda: [("discord", FakeAgent())])

    asyncio.run(
        manager.notify(
            FakeDb(),
            "new_book",
            "New book added",
            "Default body",
            context={
                "item_id": "book-1",
                "title": "",
                "asin": "B002V5D2J6",
                "isbn": "9780743453998",
                "itunes_id": "1234567890",
            },
        )
    )

    assert calls[0][1] == (
        "[B002V5D2J6](https://www.audible.com/pd/B002V5D2J6) "
        "[9780743453998](https://libro.fm/referral?isbn=9780743453998) "
        "https://www.audible.com/pd/B002V5D2J6 "
        "https://libro.fm/referral?isbn=9780743453998 "
        "https://podcasts.apple.com/podcast/id1234567890"
    )



def test_gotify_templates_link_asin_and_isbn_automatically(monkeypatch):
    client, _store = make_client(
        monkeypatch,
        {
            "gotify_notify_new_book": "true",
            "gotify_new_book_body_template": "ASIN: {asin} ISBN: {isbn}",
        },
    )
    assert client
    calls = []

    class FakeAgent:
        async def send(self, title, message, extra=None):
            calls.append((title, message, extra))

    class FakeDb:
        def add(self, record):
            if isinstance(record, NotificationEvent):
                record.id = 42

        def commit(self):
            pass

    manager = NotificationManager(get_settings())
    monkeypatch.setattr(manager, "named_agents", lambda: [("gotify", FakeAgent())])

    asyncio.run(
        manager.notify(
            FakeDb(),
            "new_book",
            "New book added",
            "Default body",
            context={
                "title": "The Fourth Option",
                "asin": "B0FV3QTZM1",
                "isbn": "9781668116517",
            },
        )
    )

    assert calls[0][1] == (
        "ASIN: [B0FV3QTZM1](https://www.audible.com/pd/B0FV3QTZM1) "
        "ISBN: [9781668116517](https://libro.fm/audiobooks/9781668116517-the-fourth-option)"
    )

def test_notification_manager_shortens_description_variables(monkeypatch):
    client, _store = make_client(
        monkeypatch,
        {
            "discord_notify_new_podcast_episode": "true",
            "discord_new_podcast_episode_body_template": "{description}|{podcast_description}|{episode_description}|{description_full}",
        },
    )
    assert client
    calls = []

    class FakeAgent:
        async def send(self, title, message, extra=None):
            calls.append((title, message, extra))

    class FakeDb:
        def add(self, record):
            if isinstance(record, NotificationEvent):
                record.id = 42

        def commit(self):
            pass

    long_text = "word " * 100
    manager = NotificationManager(get_settings())
    monkeypatch.setattr(manager, "named_agents", lambda: [("discord", FakeAgent())])

    asyncio.run(
        manager.notify(
            FakeDb(),
            "new_podcast_episode",
            "New episode",
            "Default body",
            context={
                "description": long_text,
                "podcast_description": long_text,
                "episode_description": long_text,
            },
        )
    )

    description, podcast_description, episode_description, description_full = calls[0][1].split("|")
    assert description.endswith("...")
    assert podcast_description.endswith("...")
    assert episode_description.endswith("...")
    assert len(description) <= 283
    assert description_full == long_text.strip()


def test_notification_manager_renders_missing_valid_variable_as_blank(monkeypatch):
    client, _store = make_client(
        monkeypatch,
        {
            "discord_notify_new_book": "true",
            "discord_new_book_body_template": "{title} {narrator} {year}",
        },
    )
    assert client
    calls = []

    class FakeAgent:
        async def send(self, title, message, extra=None):
            calls.append((title, message, extra))

    class FakeDb:
        def add(self, record):
            if isinstance(record, NotificationEvent):
                record.id = 42

        def commit(self):
            pass

    manager = NotificationManager(get_settings())
    monkeypatch.setattr(manager, "named_agents", lambda: [("discord", FakeAgent())])

    asyncio.run(
        manager.notify(
            FakeDb(),
            "new_book",
            "New book added",
            "Default body",
            context={"title": "Example Book"},
        )
    )

    assert calls[0][1] == "Example Book  "


def test_notification_advanced_settings_are_hidden_by_default(monkeypatch):
    client, _store = make_client(monkeypatch)

    response = client.get("/settings?tab=notifications&agent=gotify")

    assert response.status_code == 200
    marker = 'data-agent-form="gotify"'
    form_start = response.text.index(marker)
    form_end = response.text.index('</form>', form_start)
    form_html = response.text[form_start:form_end]
    assert 'data-agent-advanced-toggle' in form_html
    assert 'data-agent-advanced-content hidden' in form_html
    assert 'name="gotify_advanced_open" value="false"' in form_html


def test_notification_advanced_settings_open_when_custom_template_exists(monkeypatch):
    client, _store = make_client(monkeypatch, {"gotify_new_book_body_template": "{title}"})

    response = client.get("/settings?tab=notifications&agent=gotify")

    assert response.status_code == 200
    marker = 'data-agent-form="gotify"'
    form_start = response.text.index(marker)
    form_end = response.text.index('</form>', form_start)
    form_html = response.text[form_start:form_end]
    assert 'data-agent-advanced-content hidden' not in form_html
    assert 'name="gotify_advanced_open" value="true"' in form_html


def test_notification_advanced_state_is_saved(monkeypatch):
    client, store = make_client(monkeypatch)

    response = client.post(
        "/settings/notifications/gotify",
        data={
            "enabled": "on",
            "gotify_url": "http://gotify.local",
            "gotify_token": "app-token",
            "gotify_advanced_open": "true",
            "csrf_token": "valid-token",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert store["gotify_advanced_open"] == "true"


def test_connection_event_templates_render(monkeypatch):
    client, _store = make_client(
        monkeypatch,
        {
            "gotify_notify_abs_connection_failed": "true",
            "gotify_abs_connection_failed_title_template": "Connection: {event_type}",
            "gotify_abs_connection_failed_body_template": "{notification_body}",
        },
    )
    assert client
    calls = []

    class FakeAgent:
        async def send(self, title, message, extra=None):
            calls.append((title, message, extra))

    class FakeDb:
        def add(self, record):
            if isinstance(record, NotificationEvent):
                record.id = 88

        def commit(self):
            pass

    manager = NotificationManager(get_settings())
    monkeypatch.setattr(manager, "named_agents", lambda: [("gotify", FakeAgent())])

    asyncio.run(
        manager.notify(
            FakeDb(),
            "abs_connection_failed",
            "Audiobookshelf connection failed",
            "ABSulli cannot reach Audiobookshelf.",
        )
    )

    assert calls[0][0] == "Connection: abs_connection_failed"
    assert calls[0][1] == "ABSulli cannot reach Audiobookshelf."


def test_discord_rich_options_are_available_on_settings_page(monkeypatch):
    client, _store = make_client(monkeypatch)
    response = client.get("/settings?tab=notifications&agent=discord")
    assert response.status_code == 200
    assert 'name="discord_include_cover_art"' in response.text
    assert 'name="discord_open_in_audiobookshelf"' in response.text


def test_pushbullet_rich_options_are_available_on_settings_page(monkeypatch):
    client, _store = make_client(monkeypatch)
    response = client.get("/settings?tab=notifications&agent=pushbullet")
    assert response.status_code == 200
    assert 'name="pushbullet_open_in_audiobookshelf"' in response.text


def test_webhook_advanced_payload_ui_is_available(monkeypatch):
    client, _store = make_client(monkeypatch)

    response = client.get("/settings?tab=notifications&agent=webhook")

    assert response.status_code == 200
    assert "Webhook Payload" in response.text
    assert 'name="webhook_playback_start_title_template"' not in response.text
    assert 'name="webhook_authorization_header"' in response.text
    assert 'name="webhook_custom_header_name"' not in response.text
    assert 'name="webhook_payload_template"' in response.text
    assert "Reset to Default" in response.text
    assert "Template Variable Help" not in response.text
    assert "&#34;media&#34;" in response.text
    assert '{event_type}' in response.text


def test_webhook_payload_settings_save(monkeypatch):
    client, store = make_client(monkeypatch)
    payload = '{"event":"{event_type}","title":"{title}","author":"{author}"}'

    response = client.post(
        "/settings/notifications/webhook",
        data={
            "enabled": "on",
            "webhook_url": "https://example.com/webhook",
            "webhook_authorization_header": "Bearer token",
            "webhook_custom_header_name": "X-Source",
            "webhook_custom_header_value": "ABSulli",
            "webhook_payload_template": payload,
            "csrf_token": "valid-token",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert store["webhook_authorization_header"] == "Bearer token"
    assert store["webhook_custom_headers_json"] == '{"X-Source":"ABSulli"}'
    assert store["webhook_payload_template"] == payload
    assert store["webhook_headers_json"] == ""
    assert store["webhook_send_full_context"] == "false"
    assert store["webhook_new_book_payload_template"] == ""
    assert store["webhook_new_book_title_template"] == ""
    assert store["webhook_new_book_body_template"] == ""


def test_webhook_invalid_payload_template_is_rejected(monkeypatch):
    client, _store = make_client(monkeypatch)

    response = client.post(
        "/settings/notifications/webhook",
        data={
            "enabled": "on",
            "webhook_url": "https://example.com/webhook",
            "webhook_payload_template": '{"title":"{unknown}"}',
            "csrf_token": "valid-token",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "Unknown%20notification%20variable" in response.headers["location"]


def test_webhook_incomplete_custom_header_is_rejected(monkeypatch):
    client, _store = make_client(monkeypatch)

    response = client.post(
        "/settings/notifications/webhook",
        data={
            "enabled": "on",
            "webhook_url": "https://example.com/webhook",
            "webhook_custom_header_name": "X-Source",
            "webhook_custom_header_value": "",
            "csrf_token": "valid-token",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "needs%20both%20a%20name%20and%20value" in response.headers["location"]


def test_webhook_single_payload_renders_context_for_every_event(monkeypatch):
    client, _store = make_client(
        monkeypatch,
        {
            "webhook_notify_new_book": "true",
            "webhook_payload_template": '{"event":"{event_type}","title":"{title}","author":"{author}","description":"{description_full}"}',
        },
    )
    assert client
    calls = []

    class FakeAgent:
        async def send(self, title, message, extra=None):
            calls.append((title, message, extra))

    class FakeDb:
        def add(self, record):
            if isinstance(record, NotificationEvent):
                record.id = 99

        def commit(self):
            pass

    manager = NotificationManager(get_settings())
    monkeypatch.setattr(manager, "named_agents", lambda: [("webhook", FakeAgent())])

    asyncio.run(
        manager.notify(
            FakeDb(),
            "new_book",
            "New book added",
            "Default body",
            context={"title": 'A "Quoted" Book', "author": "Author", "description": "Line one\nLine two"},
        )
    )

    assert calls[0][2]["webhook_payload"] == {
        "event": "new_book",
        "title": 'A "Quoted" Book',
        "author": "Author",
        "description": "Line one\nLine two",
    }
