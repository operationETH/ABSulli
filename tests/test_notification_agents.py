import asyncio
import json

import httpx
import pytest

import absulli.core.setup_state as setup_state
from absulli.core.config import get_settings
from absulli.notifiers.agents import (
    DiscordAgent,
    EmailAgent,
    GotifyAgent,
    NtfyAgent,
    PushbulletAgent,
    PushoverAgent,
    SlackAgent,
    TelegramAgent,
    WebhookAgent,
)
from absulli.notifiers.manager import NotificationManager


@pytest.fixture(autouse=True)
def clear_env(monkeypatch):
    monkeypatch.setenv("ABSULLI_SECRET_KEY", "test-secret-key-that-is-long-enough-32")
    for key in (
        "GOTIFY_URL",
        "GOTIFY_TOKEN",
        "NTFY_URL",
        "NTFY_TOKEN",
        "DISCORD_WEBHOOK_URL",
        "SLACK_WEBHOOK_URL",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "PUSHOVER_APP_TOKEN",
        "PUSHOVER_USER_KEY",
        "PUSHBULLET_TOKEN",
        "WEBHOOK_URL",
        "EMAIL_SMTP_HOST",
        "EMAIL_SMTP_PORT",
        "EMAIL_SMTP_USERNAME",
        "EMAIL_SMTP_PASSWORD",
        "EMAIL_FROM",
        "EMAIL_TO",
        "EMAIL_USE_TLS",
        "EMAIL_USE_STARTTLS",
    ):
        monkeypatch.delenv(key, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_notification_manager_builds_saved_agents(monkeypatch):
    values = {
        "ntfy_url": "https://ntfy.sh/absulli",
        "ntfy_token": "ntfy-token",
        "discord_webhook_url": "https://discord.example/webhook",
        "slack_webhook_url": "https://slack.example/webhook",
        "telegram_bot_token": "telegram-token",
        "telegram_chat_id": "12345",
        "pushover_app_token": "pushover-app",
        "pushover_user_key": "pushover-user",
        "pushbullet_token": "pushbullet-token",
        "webhook_url": "https://example.com/webhook",
    }
    monkeypatch.setattr(setup_state, "get_setup_setting", lambda key, default="": values.get(key, default))

    agents = NotificationManager(get_settings()).agents()

    assert [type(agent) for agent in agents] == [
        NtfyAgent,
        DiscordAgent,
        SlackAgent,
        TelegramAgent,
        PushoverAgent,
        PushbulletAgent,
        WebhookAgent,
    ]


def test_notification_manager_builds_saved_webhook_headers(monkeypatch):
    values = {
        "webhook_url": "https://example.com/webhook",
        "webhook_authorization_header": "Bearer saved-token",
        "webhook_custom_headers_json": '{"X-Source":"ABSulli"}',
        "webhook_headers_json": "",
    }
    monkeypatch.setattr(setup_state, "get_setup_setting", lambda key, default="": values.get(key, default))

    agents = NotificationManager(get_settings()).agents()

    assert len(agents) == 1
    assert isinstance(agents[0], WebhookAgent)
    assert agents[0].headers == {
        "X-Source": "ABSulli",
        "Authorization": "Bearer saved-token",
    }


def test_notification_manager_keeps_legacy_webhook_headers(monkeypatch):
    values = {
        "webhook_url": "https://example.com/webhook",
        "webhook_headers_json": '{"Authorization":"Bearer legacy-token","X-Legacy":"true"}',
    }
    monkeypatch.setattr(setup_state, "get_setup_setting", lambda key, default="": values.get(key, default))

    agents = NotificationManager(get_settings()).agents()

    assert len(agents) == 1
    assert isinstance(agents[0], WebhookAgent)
    assert agents[0].headers == {
        "Authorization": "Bearer legacy-token",
        "X-Legacy": "true",
    }


def test_notification_manager_env_values_win(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.example/env")
    monkeypatch.setattr(setup_state, "get_setup_setting", lambda key, default="": "https://discord.example/db" if key == "discord_webhook_url" else default)
    get_settings.cache_clear()

    agents = NotificationManager(get_settings()).agents()

    assert len(agents) == 1
    assert isinstance(agents[0], DiscordAgent)
    assert agents[0].webhook_url == "https://discord.example/env"


@pytest.mark.parametrize(
    ("agent", "expected_url", "expected_json"),
    [
        (DiscordAgent("https://discord.example/webhook"), "https://discord.example/webhook", {"username": "ABSulli", "allowed_mentions": {"parse": []}, "embeds": [{"title": "Title", "description": "Body", "color": 14006049}]}),
        (SlackAgent("https://slack.example/webhook"), "https://slack.example/webhook", {"text": "*Title*\nBody"}),
        (WebhookAgent("https://example.com/webhook"), "https://example.com/webhook", {"title": "Title", "message": "Body", "extra": {"event_type": "test"}}),
    ],
)
def test_webhook_style_agents_send_expected_payloads(monkeypatch, agent, expected_url, expected_json):
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            pass

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, **kwargs):
            calls.append((url, kwargs))
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    asyncio.run(agent.send("Title", "Body", {"event_type": "test"}))

    assert calls[0][0] == expected_url
    assert calls[0][1]["json"] == expected_json





def test_discord_agent_sends_rich_media_embed(monkeypatch):
    calls = []

    class FakeResponse:
        def __init__(self, content=b"cover-bytes", content_type="image/webp"):
            self.content = content
            self.headers = {"content-type": content_type}

        def raise_for_status(self):
            pass

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, **kwargs):
            calls.append(("GET", url, kwargs))
            return FakeResponse()

        async def post(self, url, **kwargs):
            calls.append(("POST", url, kwargs))
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    asyncio.run(
        DiscordAgent("https://discord.example/webhook").send(
            "New book: Bloody Jack",
            "Bloody Jack\nby L. A. Meyer",
            {
                "cover_url": "https://absulli.example/notification-covers/items/book-1?token=abc",
                "click_url": "https://abs.example/item/book-1",
            },
        )
    )

    assert calls[0][0:2] == ("GET", "https://absulli.example/notification-covers/items/book-1?token=abc")
    post = calls[1]
    assert post[0:2] == ("POST", "https://discord.example/webhook")
    payload = json.loads(post[2]["data"]["payload_json"])
    assert payload == {
        "username": "ABSulli",
        "allowed_mentions": {"parse": []},
        "embeds": [
            {
                "title": "New book: Bloody Jack",
                "description": "Bloody Jack\nby L. A. Meyer\n\n[Open in Audiobookshelf](https://abs.example/item/book-1)",
                "color": 14006049,
                "url": "https://abs.example/item/book-1",
                "thumbnail": {"url": "attachment://cover.webp"},
            }
        ],
    }
    assert post[2]["files"]["files[0]"] == ("cover.webp", b"cover-bytes", "image/webp")


def test_discord_agent_falls_back_to_cover_url_when_download_fails(monkeypatch):
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            pass

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, **kwargs):
            raise httpx.ConnectError("unreachable")

        async def post(self, url, **kwargs):
            calls.append((url, kwargs))
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    asyncio.run(
        DiscordAgent("https://discord.example/webhook").send(
            "Title",
            "Body",
            {"cover_url": "https://absulli.example/cover.webp"},
        )
    )

    assert calls[0][1]["json"]["embeds"][0]["thumbnail"] == {"url": "https://absulli.example/cover.webp"}

def test_gotify_agent_sends_rich_media_payload(monkeypatch):
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            pass

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, **kwargs):
            calls.append((url, kwargs))
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    asyncio.run(
        GotifyAgent("https://gotify.example", "token").send(
            "New book added",
            "A book was added.",
            {
                "cover_url": "https://absulli.example/notification-covers/items/book-1?token=abc",
                "click_url": "https://abs.example/item/book-1",
            },
        )
    )

    payload = calls[0][1]["json"]
    assert payload["message"] == (
        "A book was added.\n\n"
        "[Open in Audiobookshelf](https://abs.example/item/book-1)\n\n"
        "![](https://absulli.example/notification-covers/items/book-1?token=abc)"
    )
    assert payload["extras"] == {
        "client::notification": {
            "bigImageUrl": "https://absulli.example/notification-covers/items/book-1?token=abc",
            "click": {"url": "https://abs.example/item/book-1"},
        },
        "client::display": {"contentType": "text/markdown"},
    }


def test_gotify_agent_preserves_template_line_breaks_in_markdown(monkeypatch):
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            pass

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, **kwargs):
            calls.append((url, kwargs))
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    asyncio.run(
        GotifyAgent("https://gotify.example", "token").send(
            "Playback started",
            "The Spy and the Traitor\nby Ben Macintyre\n\nLibrary: Audiobooks",
            {"click_url": "https://abs.example/item/book-1"},
        )
    )

    assert calls[0][1]["json"]["message"] == (
        "The Spy and the Traitor  \n"
        "by Ben Macintyre\n\n"
        "Library: Audiobooks\n\n"
        "[Open in Audiobookshelf](https://abs.example/item/book-1)"
    )


def test_gotify_agent_uses_plain_text_without_rich_media(monkeypatch):
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            pass

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, **kwargs):
            calls.append((url, kwargs))
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    asyncio.run(GotifyAgent("https://gotify.example", "token").send("Title", "Body", {"event_type": "test"}))

    assert calls[0][1]["json"]["message"] == "Body"
    assert calls[0][1]["json"]["extras"] == {"client::display": {"contentType": "text/plain"}}

def test_ntfy_agent_sends_topic_post_with_token(monkeypatch):
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            pass

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, **kwargs):
            calls.append((url, kwargs))
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    asyncio.run(NtfyAgent("https://ntfy.sh/absulli", "token").send("Title", "Body"))

    assert calls == [
        (
            "https://ntfy.sh/absulli",
            {"headers": {"Title": "Title", "Tags": "headphones", "Authorization": "Bearer token"}, "content": b"Body"},
        )
    ]


def test_push_services_send_expected_payloads(monkeypatch):
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            pass

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, **kwargs):
            calls.append((url, kwargs))
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    asyncio.run(TelegramAgent("bot-token", "chat-id").send("Title", "Body"))
    asyncio.run(PushoverAgent("app-token", "user-key").send("Title", "Body"))
    asyncio.run(PushbulletAgent("push-token").send("Title", "Body"))

    assert calls[0] == (
        "https://api.telegram.org/botbot-token/sendMessage",
        {"json": {"chat_id": "chat-id", "text": "Title\n\nBody"}},
    )
    assert calls[1] == (
        "https://api.pushover.net/1/messages.json",
        {"data": {"token": "app-token", "user": "user-key", "title": "Title", "message": "Body"}},
    )
    assert calls[2] == (
        "https://api.pushbullet.com/v2/pushes",
        {"headers": {"Access-Token": "push-token"}, "json": {"type": "note", "title": "Title", "body": "Body"}},
    )


def test_pushbullet_agent_uses_link_push_when_click_url_is_available(monkeypatch):
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            pass

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, **kwargs):
            calls.append((url, kwargs))
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    asyncio.run(
        PushbulletAgent("push-token").send(
            "Title",
            "Body",
            {"click_url": "https://abs.example/item/123"},
        )
    )

    assert calls == [
        (
            "https://api.pushbullet.com/v2/pushes",
            {
                "headers": {"Access-Token": "push-token"},
                "json": {
                    "type": "link",
                    "title": "Title",
                    "body": "Body",
                    "url": "https://abs.example/item/123",
                },
            },
        )
    ]


def test_email_agent_uses_smtp_ssl_and_sends_message(monkeypatch):
    calls = []

    class FakeSMTPSSL:
        def __init__(self, host, port, timeout):
            calls.append(("connect_ssl", host, port, timeout))

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def login(self, username, password):
            calls.append(("login", username, password))

        def send_message(self, email):
            calls.append(("send_message", email["From"], email["To"], email["Subject"], email.get_content().strip()))

    class UnexpectedSMTP:
        def __init__(self, *args, **kwargs):
            raise AssertionError("SMTP should not be used when SSL/TLS is enabled")

    import smtplib

    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTPSSL)
    monkeypatch.setattr(smtplib, "SMTP", UnexpectedSMTP)

    agent = EmailAgent(
        smtp_host="smtp.example.com",
        smtp_port=465,
        sender="absulli@example.com",
        recipient="user@example.com",
        username="smtp-user",
        password="smtp-pass",
        use_tls=True,
    )

    asyncio.run(agent.send("Title", "Body"))

    assert calls == [
        ("connect_ssl", "smtp.example.com", 465, 10),
        ("login", "smtp-user", "smtp-pass"),
        ("send_message", "absulli@example.com", "user@example.com", "Title", "Body"),
    ]


def test_email_agent_can_send_without_ssl_tls(monkeypatch):
    calls = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            calls.append(("connect_plain", host, port, timeout))

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def login(self, username, password):
            calls.append(("login", username, password))

        def send_message(self, email):
            calls.append(("send_message", email["Subject"]))

    class UnexpectedSMTPSSL:
        def __init__(self, *args, **kwargs):
            raise AssertionError("SMTP_SSL should not be used when SSL/TLS is disabled")

    import smtplib

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", UnexpectedSMTPSSL)

    agent = EmailAgent(
        smtp_host="smtp.example.com",
        smtp_port=25,
        sender="absulli@example.com",
        recipient="user@example.com",
        use_tls=False,
    )

    asyncio.run(agent.send("Title", "Body"))

    assert calls == [("connect_plain", "smtp.example.com", 25, 10), ("send_message", "Title")]


def test_email_agent_uses_starttls_before_login(monkeypatch):
    calls = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            calls.append(("connect", host, port, timeout))

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def starttls(self, context):
            calls.append(("starttls", type(context).__name__))

        def login(self, username, password):
            calls.append(("login", username, password))

        def send_message(self, email):
            calls.append(("send_message", email["Subject"]))

    class UnexpectedSMTPSSL:
        def __init__(self, *args, **kwargs):
            raise AssertionError("SMTP_SSL should not be used with STARTTLS")

    import smtplib

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", UnexpectedSMTPSSL)

    agent = EmailAgent(
        smtp_host="smtp.example.com",
        smtp_port=587,
        sender="absulli@example.com",
        recipient="user@example.com",
        username="smtp-user",
        password="smtp-pass",
        use_tls=False,
        use_starttls=True,
    )

    asyncio.run(agent.send("Title", "Body"))

    assert calls == [
        ("connect", "smtp.example.com", 587, 10),
        ("starttls", "SSLContext"),
        ("login", "smtp-user", "smtp-pass"),
        ("send_message", "Title"),
    ]


def test_email_agent_rejects_multiple_tls_modes():
    with pytest.raises(ValueError, match="cannot both be enabled"):
        EmailAgent(
            smtp_host="smtp.example.com",
            smtp_port=465,
            sender="absulli@example.com",
            recipient="user@example.com",
            use_tls=True,
            use_starttls=True,
        )


def test_notification_manager_builds_saved_email_agent(monkeypatch):
    values = {
        "email_smtp_host": "smtp.example.com",
        "email_smtp_port": "465",
        "email_smtp_username": "smtp-user",
        "email_smtp_password": "smtp-pass",
        "email_from": "absulli@example.com",
        "email_to": "user@example.com",
        "email_use_tls": "true",
        "email_use_starttls": "false",
    }
    monkeypatch.setattr(setup_state, "get_setup_setting", lambda key, default="": values.get(key, default))

    agents = NotificationManager(get_settings()).agents()

    assert len(agents) == 1
    assert isinstance(agents[0], EmailAgent)
    assert agents[0].smtp_host == "smtp.example.com"
    assert agents[0].smtp_port == 465
    assert agents[0].sender == "absulli@example.com"
    assert agents[0].recipient == "user@example.com"
    assert agents[0].username == "smtp-user"
    assert agents[0].password == "smtp-pass"
    assert agents[0].use_tls is True
    assert agents[0].use_starttls is False


def test_discord_agent_truncates_embed_title_and_description(monkeypatch):
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            pass

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, **kwargs):
            calls.append((url, kwargs))
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    asyncio.run(
        DiscordAgent("https://discord.example/webhook").send(
            "T" * 300,
            "B" * 5000,
            {"click_url": "https://abs.example/item/book-1"},
        )
    )

    payload = calls[0][1]["json"]
    embed = payload["embeds"][0]
    assert len(embed["title"]) == 256
    assert embed["title"].endswith("...")
    assert len(embed["description"]) <= 4096
    assert embed["description"].endswith("[Open in Audiobookshelf](https://abs.example/item/book-1)")
    assert payload["allowed_mentions"] == {"parse": []}


def test_discord_agent_disables_mentions(monkeypatch):
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            pass

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, **kwargs):
            calls.append((url, kwargs))
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    asyncio.run(DiscordAgent("https://discord.example/webhook").send("@everyone", "<@123> @here"))

    payload = calls[0][1]["json"]
    assert payload["allowed_mentions"] == {"parse": []}
    assert payload["embeds"][0]["title"] == "@everyone"
    assert payload["embeds"][0]["description"] == "<@123> @here"


def test_webhook_agent_sends_custom_payload_and_headers(monkeypatch):
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            pass

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, **kwargs):
            calls.append((url, kwargs))
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    agent = WebhookAgent("https://example.com/webhook", {"Authorization": "Bearer secret"})
    payload = {"event": "new_book", "media": {"title": "Example"}}

    asyncio.run(agent.send("Title", "Body", {"event_type": "new_book", "webhook_payload": payload}))

    assert calls == [
        (
            "https://example.com/webhook",
            {
                "headers": {"Authorization": "Bearer secret"},
                "json": payload,
            },
        )
    ]
