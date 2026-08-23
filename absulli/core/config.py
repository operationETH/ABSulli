from functools import lru_cache
from pathlib import Path
import logging
import secrets

from pydantic import AliasChoices, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


log = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = Field(default="ABSulli", alias="ABSULLI_APP_NAME")
    host: str = Field(default="0.0.0.0", alias="ABSULLI_HOST")
    port: int = Field(default=8272, alias="ABSULLI_PORT")
    data_dir: Path = Field(default=Path("/config"), alias="ABSULLI_DATA_DIR")
    log_level: str = Field(default="INFO", alias="ABSULLI_LOG_LEVEL")
    secret_key: SecretStr = Field(default=SecretStr("change_me"), alias="ABSULLI_SECRET_KEY")

    security_csp_enabled: bool = Field(default=True, alias="ABSULLI_SECURITY_CSP_ENABLED")
    security_hsts_enabled: bool = Field(default=False, alias="ABSULLI_SECURITY_HSTS_ENABLED")
    security_hsts_max_age_seconds: int = Field(
        default=31_536_000,
        alias="ABSULLI_SECURITY_HSTS_MAX_AGE_SECONDS",
    )
    security_hsts_include_subdomains: bool = Field(
        default=True,
        alias="ABSULLI_SECURITY_HSTS_INCLUDE_SUBDOMAINS",
    )
    security_hsts_preload: bool = Field(default=False, alias="ABSULLI_SECURITY_HSTS_PRELOAD")
    content_security_policy: str = Field(
        default=(
            "default-src 'self'; "
            "img-src 'self' data:; "
            "script-src 'self' 'nonce-{nonce}'; "
            "style-src 'self' 'nonce-{nonce}'; "
            "font-src 'self' data:; "
            "connect-src 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'"
        ),
        alias="ABSULLI_CONTENT_SECURITY_POLICY",
    )

    cors_allowed_origins: str = Field(default="", alias="ABSULLI_CORS_ALLOWED_ORIGINS")
    cors_allow_credentials: bool = Field(default=False, alias="ABSULLI_CORS_ALLOW_CREDENTIALS")
    cors_allowed_methods: str = Field(
        default="GET,POST,OPTIONS",
        alias="ABSULLI_CORS_ALLOWED_METHODS",
    )
    cors_allowed_headers: str = Field(
        default=(
            "Authorization,Content-Type,X-API-Key,X-Absulli-Api-Token,"
            "X-Absulli-Metrics-Token,X-CSRF-Token"
        ),
        alias="ABSULLI_CORS_ALLOWED_HEADERS",
    )

    metrics_token: SecretStr | None = Field(default=None, alias="ABSULLI_METRICS_TOKEN")

    auth_enabled: bool = Field(default=True, alias="ABSULLI_AUTH_ENABLED")
    auth_username: str = Field(default="admin", alias="ABSULLI_AUTH_USERNAME")
    auth_password: SecretStr | None = Field(default=None, alias="ABSULLI_AUTH_PASSWORD")
    auth_password_hash: SecretStr | None = Field(default=None, alias="ABSULLI_AUTH_PASSWORD_HASH")
    auth_session_minutes: int = Field(default=720, alias="ABSULLI_AUTH_SESSION_MINUTES")
    auth_cookie_name: str = Field(default="absulli_session", alias="ABSULLI_AUTH_COOKIE_NAME")
    csrf_cookie_name: str = Field(default="absulli_csrf", alias="ABSULLI_CSRF_COOKIE_NAME")

    cookie_secure: bool = Field(default=False, alias="ABSULLI_COOKIE_SECURE")
    trust_proxy: bool = Field(default=False, alias="ABSULLI_TRUST_PROXY")
    public_url: str = Field(default="", alias="ABSULLI_PUBLIC_URL")

    auth_login_max_attempts: int = Field(default=8, alias="ABSULLI_AUTH_LOGIN_MAX_ATTEMPTS")
    auth_login_window_seconds: int = Field(default=900, alias="ABSULLI_AUTH_LOGIN_WINDOW_SECONDS")
    auth_login_lockout_seconds: int = Field(default=900, alias="ABSULLI_AUTH_LOGIN_LOCKOUT_SECONDS")

    api_enabled: bool = Field(default=False, alias="ABSULLI_API_ENABLED")
    api_token: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("ABSULLI_API_KEY", "ABSULLI_API_TOKEN"),
    )

    abs_url: str = Field(default="http://audiobookshelf:13378", alias="ABS_URL")
    abs_api_key: str = Field(default="", alias="ABS_API_KEY")
    abs_verify_ssl: bool = Field(default=True, alias="ABS_VERIFY_SSL")
    abs_request_timeout: int = Field(default=15, alias="ABS_REQUEST_TIMEOUT")
    abs_poll_interval: int = Field(default=15, alias="ABS_POLL_INTERVAL")
    abs_history_poll_interval: int = Field(default=300, alias="ABS_HISTORY_POLL_INTERVAL")

    gotify_url: str = Field(default="", alias="GOTIFY_URL")
    gotify_token: str = Field(default="", alias="GOTIFY_TOKEN")
    ntfy_url: str = Field(default="", alias="NTFY_URL")
    ntfy_token: str = Field(default="", alias="NTFY_TOKEN")
    discord_webhook_url: str = Field(default="", alias="DISCORD_WEBHOOK_URL")
    slack_webhook_url: str = Field(default="", alias="SLACK_WEBHOOK_URL")
    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str = Field(default="", alias="TELEGRAM_CHAT_ID")
    pushover_app_token: str = Field(default="", alias="PUSHOVER_APP_TOKEN")
    pushover_user_key: str = Field(default="", alias="PUSHOVER_USER_KEY")
    pushbullet_token: str = Field(default="", alias="PUSHBULLET_TOKEN")
    email_smtp_host: str = Field(default="", alias="EMAIL_SMTP_HOST")
    email_smtp_port: int = Field(default=465, alias="EMAIL_SMTP_PORT")
    email_smtp_username: str = Field(default="", alias="EMAIL_SMTP_USERNAME")
    email_smtp_password: str = Field(default="", alias="EMAIL_SMTP_PASSWORD")
    email_from: str = Field(default="", alias="EMAIL_FROM")
    email_to: str = Field(default="", alias="EMAIL_TO")
    email_use_tls: bool = Field(default=True, alias="EMAIL_USE_TLS")
    webhook_url: str = Field(default="", alias="WEBHOOK_URL")

    @model_validator(mode="after")
    def ensure_strong_secret_key(self) -> "Settings":
        """Use a strong session-signing key without forcing new users to edit .env.

        If ABSULLI_SECRET_KEY is unset or left at a known placeholder, ABSulli
        generates a random key once and persists it in /config/secret_key. This
        keeps the easy first-run GUI flow safe while still allowing power users
        to provide their own secret through the environment.
        """
        key = self.secret_key.get_secret_value().strip()
        weak_defaults = {"", "change_me", "change_me_dev_secret"}

        if key not in weak_defaults and len(key) >= 32:
            return self

        self.data_dir.mkdir(parents=True, exist_ok=True)
        secret_file = self.data_dir / "secret_key"
        if secret_file.exists():
            generated_key = secret_file.read_text(encoding="utf-8").strip()
        else:
            generated_key = secrets.token_hex(32)
            secret_file.write_text(generated_key + "\n", encoding="utf-8")
            try:
                secret_file.chmod(0o600)
            except OSError:
                pass

        if len(generated_key) < 32:
            generated_key = secrets.token_hex(32)
            secret_file.write_text(generated_key + "\n", encoding="utf-8")

        self.secret_key = SecretStr(generated_key)
        return self

    @property
    def cors_allowed_origins_list(self) -> list[str]:
        return _csv_list(self.effective_cors_allowed_origins)

    @property
    def cors_allowed_methods_list(self) -> list[str]:
        return _csv_list(self.effective_cors_allowed_methods) or ["GET", "POST", "OPTIONS"]

    @property
    def cors_allowed_headers_list(self) -> list[str]:
        return _csv_list(self.effective_cors_allowed_headers) or ["Authorization", "Content-Type"]

    def field_configured(self, field_name: str) -> bool:
        return field_name in self.model_fields_set

    def env_string(self, field_name: str) -> str:
        value = getattr(self, field_name, "")
        return str(value or "").strip()

    def env_bool(self, field_name: str) -> bool:
        return bool(self.field_configured(field_name) and self.env_string(field_name))

    def effective_setting(self, field_name: str, setting_name: str | None = None) -> str:
        env_value = self.env_string(field_name)
        if self.field_configured(field_name) and env_value:
            return env_value

        try:
            from absulli.core.setup_state import get_setup_setting

            stored_value = get_setup_setting(setting_name or field_name)
        except Exception as exc:
            log.debug("Unable to read stored setting %s: %s", setting_name or field_name, exc)
            stored_value = ""

        return str(stored_value or "").strip()

    def effective_bool_setting(self, field_name: str, setting_name: str | None = None, default: bool = False) -> bool:
        if self.field_configured(field_name):
            value = getattr(self, field_name)
            if isinstance(value, bool):
                return value
            return str(value or "").strip().lower() in {"1", "true", "yes", "on"}

        try:
            from absulli.core.setup_state import get_setup_setting

            stored_value = get_setup_setting(setting_name or field_name, "")
        except Exception as exc:
            log.debug("Unable to read stored setting %s: %s", setting_name or field_name, exc)
            stored_value = ""

        if stored_value == "":
            return default
        return str(stored_value).strip().lower() in {"1", "true", "yes", "on"}

    def effective_int_setting(
        self,
        field_name: str,
        setting_name: str | None = None,
        default: int = 0,
        minimum: int | None = None,
        maximum: int | None = None,
    ) -> int:
        raw_value = getattr(self, field_name, default) if self.field_configured(field_name) else None
        if raw_value is None:
            try:
                from absulli.core.setup_state import get_setup_setting

                raw_value = get_setup_setting(setting_name or field_name, "")
            except Exception as exc:
                log.debug("Unable to read stored setting %s: %s", setting_name or field_name, exc)
                raw_value = ""

        try:
            value = int(str(raw_value).strip()) if str(raw_value or "").strip() else int(default)
        except (TypeError, ValueError):
            value = int(default)

        if minimum is not None:
            value = max(int(minimum), value)
        if maximum is not None:
            value = min(int(maximum), value)
        return value

    @property
    def abs_url_from_env(self) -> bool:
        return self.field_configured("abs_url") and bool((self.abs_url or "").strip())

    @property
    def abs_api_key_from_env(self) -> bool:
        return (
            self.field_configured("abs_api_key")
            and bool((self.abs_api_key or "").strip())
            and self.abs_api_key.strip() != "change_me"
        )

    @property
    def auth_username_from_env(self) -> bool:
        username = (self.auth_username or "").strip()
        return bool(
            self.field_configured("auth_username")
            and username
            and username != "admin"
        )

    @property
    def auth_password_from_env(self) -> bool:
        return bool(
            self.field_configured("auth_password")
            and self.auth_password
            and self.auth_password.get_secret_value().strip()
        )

    @property
    def auth_password_hash_from_env(self) -> bool:
        return bool(
            self.field_configured("auth_password_hash")
            and self.auth_password_hash
            and self.auth_password_hash.get_secret_value().strip()
        )

    @property
    def effective_abs_url(self) -> str:
        if self.abs_url_from_env:
            return self.abs_url.rstrip("/")

        try:
            from absulli.core.setup_state import get_setup_setting

            stored_url = get_setup_setting("abs_url")
        except Exception as exc:
            log.debug("Unable to read stored Audiobookshelf URL from setup state: %s", exc)
            stored_url = ""

        return (stored_url or self.abs_url or "http://audiobookshelf:13378").rstrip("/")

    @property
    def effective_abs_api_key(self) -> str:
        if self.abs_api_key_from_env:
            return self.abs_api_key.strip()

        try:
            from absulli.core.setup_state import get_setup_setting

            stored_key = get_setup_setting("abs_api_key")
        except Exception as exc:
            log.debug("Unable to read stored Audiobookshelf API key from setup state: %s", exc)
            stored_key = ""

        return stored_key or (self.abs_api_key if self.abs_api_key != "change_me" else "")



    @property
    def effective_abs_verify_ssl(self) -> bool:
        return self.effective_bool_setting("abs_verify_ssl", default=bool(self.abs_verify_ssl))

    @property
    def effective_abs_request_timeout(self) -> int:
        return self.effective_int_setting("abs_request_timeout", default=15, minimum=1, maximum=300)

    @property
    def effective_abs_poll_interval(self) -> int:
        return self.effective_int_setting("abs_poll_interval", default=15, minimum=3, maximum=3600)

    @property
    def effective_abs_history_poll_interval(self) -> int:
        return self.effective_int_setting("abs_history_poll_interval", default=300, minimum=60, maximum=86400)

    @property
    def gotify_url_from_env(self) -> bool:
        return self.env_bool("gotify_url")

    @property
    def gotify_token_from_env(self) -> bool:
        return self.env_bool("gotify_token")

    @property
    def effective_gotify_url(self) -> str:
        return self.effective_setting("gotify_url").rstrip("/")

    @property
    def effective_gotify_token(self) -> str:
        return self.effective_setting("gotify_token")

    @property
    def effective_trust_proxy(self) -> bool:
        return self.effective_bool_setting("trust_proxy", default=bool(self.trust_proxy))

    @property
    def effective_cookie_secure(self) -> bool:
        return self.effective_bool_setting("cookie_secure", default=bool(self.cookie_secure))

    @property
    def effective_security_csp_enabled(self) -> bool:
        return self.effective_bool_setting("security_csp_enabled", default=bool(self.security_csp_enabled))

    @property
    def effective_security_hsts_enabled(self) -> bool:
        return self.effective_bool_setting("security_hsts_enabled", default=bool(self.security_hsts_enabled))

    @property
    def effective_security_hsts_max_age_seconds(self) -> int:
        if self.field_configured("security_hsts_max_age_seconds"):
            return self.effective_int_setting(
                "security_hsts_max_age_seconds",
                default=31_536_000,
                minimum=0,
                maximum=63_072_000,
            )
        return int(self.security_hsts_max_age_seconds)

    @property
    def effective_security_hsts_include_subdomains(self) -> bool:
        if self.field_configured("security_hsts_include_subdomains"):
            return self.effective_bool_setting(
                "security_hsts_include_subdomains",
                default=bool(self.security_hsts_include_subdomains),
            )
        return bool(self.security_hsts_include_subdomains)

    @property
    def effective_security_hsts_preload(self) -> bool:
        if self.field_configured("security_hsts_preload"):
            return self.effective_bool_setting(
                "security_hsts_preload",
                default=bool(self.security_hsts_preload),
            )
        return bool(self.security_hsts_preload)

    @property
    def effective_cors_allowed_origins(self) -> str:
        if self.field_configured("cors_allowed_origins"):
            return self.cors_allowed_origins

        try:
            from absulli.core.setup_state import get_setup_setting_if_available

            stored_value = get_setup_setting_if_available("cors_allowed_origins")
        except Exception as exc:
            log.debug("Unable to read stored setting cors_allowed_origins: %s", exc)
            stored_value = ""

        return str(stored_value or "").strip()

    @property
    def effective_cors_allow_credentials(self) -> bool:
        if self.field_configured("cors_allow_credentials"):
            return self.effective_bool_setting(
                "cors_allow_credentials",
                default=bool(self.cors_allow_credentials),
            )
        return bool(self.cors_allow_credentials)

    @property
    def effective_cors_allowed_methods(self) -> str:
        if self.field_configured("cors_allowed_methods"):
            return self.cors_allowed_methods
        return self.cors_allowed_methods

    @property
    def effective_cors_allowed_headers(self) -> str:
        if self.field_configured("cors_allowed_headers"):
            return self.cors_allowed_headers
        return self.cors_allowed_headers

    @property
    def effective_metrics_token(self) -> str:
        if self.metrics_token and self.field_configured("metrics_token"):
            return self.metrics_token.get_secret_value().strip()
        return self.effective_setting("metrics_token")

    @property
    def api_enabled_from_env(self) -> bool:
        return self.field_configured("api_enabled")

    @property
    def effective_api_enabled(self) -> bool:
        return self.effective_bool_setting("api_enabled", default=False)

    @property
    def api_token_from_env(self) -> bool:
        return bool(
            self.field_configured("api_token")
            and self.api_token
            and self.api_token.get_secret_value().strip()
        )

    @property
    def effective_api_token(self) -> str:
        if self.api_token_from_env:
            return self.api_token.get_secret_value().strip() if self.api_token else ""
        return self.effective_setting("api_token")

    @property
    def effective_auth_session_minutes(self) -> int:
        return self.effective_int_setting("auth_session_minutes", default=720, minimum=5, maximum=10080)

    @property
    def effective_auth_login_max_attempts(self) -> int:
        return self.effective_int_setting("auth_login_max_attempts", default=8, minimum=1, maximum=100)

    @property
    def effective_auth_login_window_seconds(self) -> int:
        return self.effective_int_setting("auth_login_window_seconds", default=900, minimum=60, maximum=86400)

    @property
    def effective_auth_login_lockout_seconds(self) -> int:
        return self.effective_int_setting("auth_login_lockout_seconds", default=900, minimum=60, maximum=86400)

    @property
    def database_url(self) -> str:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{self.data_dir / 'absulli.db'}"

    @property
    def auth_max_age_seconds(self) -> int:
        return self.effective_auth_session_minutes * 60

    @property
    def session_cookie_secure(self) -> bool:
        return self.effective_cookie_secure


def _csv_list(value: str) -> list[str]:
    return [part.strip() for part in (value or "").split(",") if part.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()