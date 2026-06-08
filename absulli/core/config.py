from functools import lru_cache
from pathlib import Path
import secrets

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = Field(default="ABSulli", alias="ABSULLI_APP_NAME")
    host: str = Field(default="0.0.0.0", alias="ABSULLI_HOST")
    port: int = Field(default=8272, alias="ABSULLI_PORT")
    public_url: str = Field(default="http://localhost:8272", alias="ABSULLI_PUBLIC_URL")
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
            "Authorization,Content-Type,X-Absulli-Api-Token,"
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

    cookie_secure: bool | None = Field(default=None, alias="ABSULLI_COOKIE_SECURE")
    auth_cookie_secure: bool = Field(default=False, alias="ABSULLI_AUTH_COOKIE_SECURE")

    auth_login_max_attempts: int = Field(default=8, alias="ABSULLI_AUTH_LOGIN_MAX_ATTEMPTS")
    auth_login_window_seconds: int = Field(default=900, alias="ABSULLI_AUTH_LOGIN_WINDOW_SECONDS")
    auth_login_lockout_seconds: int = Field(default=900, alias="ABSULLI_AUTH_LOGIN_LOCKOUT_SECONDS")

    api_token: SecretStr | None = Field(default=None, alias="ABSULLI_API_TOKEN")

    abs_url: str = Field(default="http://audiobookshelf:13378", alias="ABS_URL")
    abs_api_key: str = Field(default="", alias="ABS_API_KEY")
    abs_verify_ssl: bool = Field(default=True, alias="ABS_VERIFY_SSL")
    abs_request_timeout: int = Field(default=15, alias="ABS_REQUEST_TIMEOUT")
    abs_poll_interval: int = Field(default=15, alias="ABS_POLL_INTERVAL")
    abs_history_poll_interval: int = Field(default=300, alias="ABS_HISTORY_POLL_INTERVAL")
    abs_history_lookback_days: int = Field(default=30, alias="ABS_HISTORY_LOOKBACK_DAYS")

    gotify_url: str = Field(default="", alias="GOTIFY_URL")
    gotify_token: str = Field(default="", alias="GOTIFY_TOKEN")
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
        return _csv_list(self.cors_allowed_origins)

    @property
    def cors_allowed_methods_list(self) -> list[str]:
        return _csv_list(self.cors_allowed_methods) or ["GET", "POST", "OPTIONS"]

    @property
    def cors_allowed_headers_list(self) -> list[str]:
        return _csv_list(self.cors_allowed_headers) or ["Authorization", "Content-Type"]


    def field_configured(self, field_name: str) -> bool:
        return field_name in self.model_fields_set

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
        except Exception:  
            stored_url = ""

        return (stored_url or self.abs_url or "http://audiobookshelf:13378").rstrip("/")

    @property
    def effective_abs_api_key(self) -> str:
        if self.abs_api_key_from_env:
            return self.abs_api_key.strip()

        try:
            from absulli.core.setup_state import get_setup_setting

            stored_key = get_setup_setting("abs_api_key")
        except Exception:
            stored_key = ""

        return stored_key or (self.abs_api_key if self.abs_api_key != "change_me" else "")

    @property
    def has_abs_connection_secret(self) -> bool:
        return bool(self.effective_abs_api_key and self.effective_abs_api_key != "change_me")

    @property
    def database_url(self) -> str:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{self.data_dir / 'absulli.db'}"

    @property
    def auth_max_age_seconds(self) -> int:
        return max(5, min(10080, int(self.auth_session_minutes or 720))) * 60

    @property
    def session_cookie_secure(self) -> bool:
        return bool(self.cookie_secure if self.cookie_secure is not None else self.auth_cookie_secure)

    @property
    def has_login_secret(self) -> bool:
        has_hash = bool(
            self.auth_password_hash
            and self.auth_password_hash.get_secret_value().strip()
        )
        has_password = bool(
            self.auth_password
            and self.auth_password.get_secret_value().strip()
        )
        return has_hash or has_password


def _csv_list(value: str) -> list[str]:
    return [part.strip() for part in (value or "").split(",") if part.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()