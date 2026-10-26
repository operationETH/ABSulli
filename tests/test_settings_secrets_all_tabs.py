import json
import re

from fastapi import FastAPI
from fastapi.testclient import TestClient

import absulli.core.setup_state as setup_state
import absulli.web.routes as web_routes
from absulli.core.config import get_settings
from absulli.web.routes import router as web_router
from absulli.web.settings import SETTINGS_TAB_IDS


def test_saved_secrets_are_absent_from_every_settings_tab(monkeypatch):
    stored = {
        "abs_api_key": "fixture-secret-audiobookshelf-key-731",
        "api_token": "fixture-secret-absulli-token-732",
        "api_enabled": "true",
        "metrics_token": "fixture-secret-metrics-token-733",
        "rss_feed_token": "fixture-secret-rss-token-734",
        "email_smtp_password": "fixture-secret-email-password-735",
        "discord_webhook_url": "https://discord.example/fixture-secret-discord-736",
        "gotify_token": "fixture-secret-gotify-token-737",
        "ntfy_url": "https://ntfy.example/fixture-secret-ntfy-topic-738",
        "ntfy_token": "fixture-secret-ntfy-token-739",
        "pushbullet_token": "fixture-secret-pushbullet-token-740",
        "pushover_app_token": "fixture-secret-pushover-app-741",
        "pushover_user_key": "fixture-secret-pushover-user-742",
        "slack_webhook_url": "https://slack.example/fixture-secret-slack-743",
        "telegram_bot_token": "fixture-secret-telegram-token-744",
        "webhook_url": "https://webhook.example/fixture-secret-webhook-745",
        "webhook_authorization_header": "Bearer fixture-secret-webhook-authorization-746",
        "webhook_custom_headers_json": json.dumps({"X-Api-Key": "fixture-secret-webhook-header-747"}),
    }
    monkeypatch.setenv("ABSULLI_SECRET_KEY", "test-secret-key-that-is-long-enough-32")
    get_settings.cache_clear()
    monkeypatch.setattr(setup_state, "get_setup_setting", lambda name, default="": stored.get(name, default))
    monkeypatch.setattr(web_routes, "get_setup_setting", lambda name, default="": stored.get(name, default))
    app = FastAPI()
    app.include_router(web_router)
    client = TestClient(app)
    secrets = sorted({match for value in stored.values() for match in re.findall(r"fixture-secret-[a-z-]+-\d+", value)})
    assert len(secrets) == 17
    for tab in SETTINGS_TAB_IDS:
        response = client.get("/settings", params={"tab": tab})
        assert response.status_code == 200, tab
        assert response.headers["cache-control"] == "no-store", tab
        for secret in secrets:
            assert secret not in response.text, (tab, secret)
