from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import timedelta
from urllib.parse import quote

from fastapi import HTTPException, Request, status
from fastapi.responses import RedirectResponse, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from absulli.core.config import get_settings
from absulli.core.setup_state import get_setup_setting, is_setup_complete
from absulli.core.time import utcnow
from absulli.database.models import LoginAttempt, LoginLog, Setting
from absulli.database.session import SessionLocal


PUBLIC_PATH_PREFIXES = (
    "/static/",
)

PUBLIC_PATHS = {
    "/favicon.ico",
    "/healthz",
    "/login",
    "/logout",
}

PBKDF2_PREFIX = "pbkdf2_sha256"
DEFAULT_PBKDF2_ITERATIONS = 600_000
SESSION_VERSION_SETTING = "auth_session_version"
_session_version_cache: str = ""

SECURITY_HEADER_KEYS = {
    b"x-content-type-options",
    b"x-frame-options",
    b"referrer-policy",
    b"permissions-policy",
    b"cross-origin-opener-policy",
    b"cross-origin-resource-policy",
    b"content-security-policy",
    b"strict-transport-security",
    b"x-absulli-debug-csp",
}


def _content_security_policy(nonce: str | None = None) -> str:
    settings = get_settings()
    policy = settings.content_security_policy
    if nonce:
        policy = policy.replace("{nonce}", nonce)
    return policy.replace("'nonce-{nonce}'", "'none'")


def _security_raw_headers(nonce: str | None = None) -> list[tuple[bytes, bytes]]:
    settings = get_settings()

    raw_headers: list[tuple[bytes, bytes]] = [
        (b"x-content-type-options", b"nosniff"),
        (b"x-frame-options", b"DENY"),
        (b"referrer-policy", b"strict-origin-when-cross-origin"),
        (
            b"permissions-policy",
            b"camera=(), microphone=(), geolocation=(), payment=(), usb=()",
        ),
        (b"cross-origin-opener-policy", b"same-origin"),
        (b"cross-origin-resource-policy", b"same-origin"),
    ]

    if settings.effective_security_csp_enabled:
        raw_headers.append(
            (
                b"content-security-policy",
                _content_security_policy(nonce).encode("latin-1"),
            )
        )

    if settings.effective_security_hsts_enabled:
        hsts_parts = [
            f"max-age={max(0, int(settings.effective_security_hsts_max_age_seconds or 0))}"
        ]
        if settings.effective_security_hsts_include_subdomains:
            hsts_parts.append("includeSubDomains")
        if settings.effective_security_hsts_preload:
            hsts_parts.append("preload")
        raw_headers.append(
            (
                b"strict-transport-security",
                "; ".join(hsts_parts).encode("latin-1"),
            )
        )

    return raw_headers


def _replace_security_headers(
    raw_headers: list[tuple[bytes, bytes]],
    nonce: str | None = None,
) -> list[tuple[bytes, bytes]]:
    return [
        (key, value)
        for key, value in raw_headers
        if key.lower() not in SECURITY_HEADER_KEYS
    ] + _security_raw_headers(nonce)


class SecurityHeadersMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        request.state.absulli_authenticated = False
        request.state.absulli_auth_method = "none"
        csp_nonce = secrets.token_urlsafe(16)
        request.state.absulli_csp_nonce = csp_nonce

        auth_response = self._authenticate_request(request)
        if auth_response is not None:
            auth_response = self._with_security_headers(auth_response, csp_nonce)
            await auth_response(scope, receive, send)
            return

        async def send_with_security_headers(message):
            if message["type"] == "http.response.start":
                message["headers"] = _replace_security_headers(message.get("headers", []), csp_nonce)

            await send(message)

        await self.app(scope, receive, send_with_security_headers)

    def _authenticate_request(self, request: Request) -> Response | None:
        settings = get_settings()
        if not settings.auth_enabled:
            return None

        path = request.url.path
        is_public_path = path in PUBLIC_PATHS or any(
            path.startswith(prefix) for prefix in PUBLIC_PATH_PREFIXES
        )

        # Never hit SQLite from middleware for public/static paths. The route itself
        # can decide whether to redirect, show setup, or return health status.
        if is_public_path or path == "/setup":
            return None

        if setup_required():
            if path == "/setup/test-connection":
                return None
            if not path.startswith("/api/") and path != "/metrics":
                return RedirectResponse(url="/setup", status_code=status.HTTP_303_SEE_OTHER)
            return Response(
                "ABSulli setup is required",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                media_type="text/plain",
            )

        if path == "/metrics" and _valid_metrics_token(request):
            request.state.absulli_authenticated = True
            request.state.absulli_auth_method = "metrics_token"
            return None

        if path != "/metrics" and _valid_api_token(request):
            request.state.absulli_authenticated = True
            request.state.absulli_auth_method = "api_token"
            return None

        username = read_session_cookie(request)
        if username:
            request.state.absulli_authenticated = True
            request.state.absulli_auth_method = "session"
            request.state.absulli_username = username
            return None

        if not path.startswith("/api/") and path != "/metrics":
            next_path = quote(
                str(request.url.path) + (f"?{request.url.query}" if request.url.query else ""),
                safe="",
            )
            return RedirectResponse(
                url=f"/login?next={next_path}",
                status_code=status.HTTP_303_SEE_OTHER,
            )

        return Response(
            "authentication required",
            status_code=status.HTTP_401_UNAUTHORIZED,
            media_type="text/plain",
            headers={"WWW-Authenticate": "Bearer"},
        )

    def _with_security_headers(self, response: Response, nonce: str | None = None) -> Response:
        response.raw_headers = _replace_security_headers(response.raw_headers, nonce)
        return response


def csrf_serializer() -> URLSafeTimedSerializer:
    settings = get_settings()
    return URLSafeTimedSerializer(
        settings.secret_key.get_secret_value(),
        salt="absulli-csrf-token-v1",
    )


def create_csrf_token() -> str:
    return csrf_serializer().dumps({"token": secrets.token_urlsafe(32)})


def validate_csrf_token(request: Request, submitted_token: str | None) -> bool:
    settings = get_settings()
    cookie_token = request.cookies.get(settings.csrf_cookie_name, "")
    submitted_token = (submitted_token or "").strip()

    if not cookie_token or not submitted_token:
        return False
    if not secrets.compare_digest(cookie_token, submitted_token):
        return False

    try:
        csrf_serializer().loads(submitted_token, max_age=settings.auth_max_age_seconds)
    except (BadSignature, SignatureExpired):
        return False

    return True


def set_csrf_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        settings.csrf_cookie_name,
        token,
        max_age=settings.auth_max_age_seconds,
        httponly=False,
        secure=settings.session_cookie_secure,
        samesite="lax",
        path="/",
    )


def clear_csrf_cookie(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(settings.csrf_cookie_name, path="/")


def _extract_bearer_token(value: str | None) -> str:
    if not value:
        return ""
    scheme, _, token = value.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return ""
    return token.strip()


def _valid_api_token(request: Request) -> bool:
    settings = get_settings()
    if not settings.effective_api_enabled:
        return False

    expected = settings.effective_api_token
    if not expected:
        return False

    provided = request.headers.get("X-API-Key", "").strip()
    if not provided:
        provided = _extract_bearer_token(request.headers.get("Authorization"))
    if not provided:
        provided = request.headers.get("X-Absulli-Api-Token", "").strip()
    return bool(provided and secrets.compare_digest(provided, expected))


def _valid_metrics_token(request: Request) -> bool:
    settings = get_settings()
    expected = settings.effective_metrics_token
    if not expected:
        return False

    provided = _extract_bearer_token(request.headers.get("Authorization"))
    if not provided:
        provided = request.headers.get("X-Absulli-Metrics-Token", "").strip()
    return bool(provided and secrets.compare_digest(provided, expected))


def verify_metrics_access(request: Request) -> None:
    settings = get_settings()
    expected = settings.effective_metrics_token
    if not expected:
        return

    provided = _extract_bearer_token(request.headers.get("Authorization"))
    if not provided:
        provided = request.headers.get("X-Absulli-Metrics-Token", "").strip()

    if not provided or not secrets.compare_digest(provided, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="metrics authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )


def password_hash(password: str, *, iterations: int = DEFAULT_PBKDF2_ITERATIONS) -> str:
    password = password or ""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "$".join(
        [
            PBKDF2_PREFIX,
            str(iterations),
            base64.urlsafe_b64encode(salt).decode("ascii").rstrip("="),
            base64.urlsafe_b64encode(digest).decode("ascii").rstrip("="),
        ]
    )


def verify_password(password: str, expected_hash: str) -> bool:
    password = password or ""
    expected_hash = (expected_hash or "").strip()
    try:
        algorithm, iterations_raw, salt_raw, digest_raw = expected_hash.split("$", 3)
        if algorithm != PBKDF2_PREFIX:
            return False
        iterations = int(iterations_raw)
        salt = _b64decode(salt_raw)
        expected_digest = _b64decode(digest_raw)
    except (ValueError, TypeError):
        return False

    actual_digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual_digest, expected_digest)


def auth_username() -> str:
    settings = get_settings()
    if settings.auth_username and settings.auth_username != "admin":
        return settings.auth_username
    return get_setup_setting("auth_username") or settings.auth_username


def auth_password_hash() -> str:
    settings = get_settings()
    if settings.auth_password_hash and settings.auth_password_hash.get_secret_value().strip():
        return settings.auth_password_hash.get_secret_value().strip()
    return get_setup_setting("auth_password_hash")


def has_login_secret() -> bool:
    settings = get_settings()
    has_hash = bool(auth_password_hash())
    has_password = bool(settings.auth_password and settings.auth_password.get_secret_value().strip())
    return has_hash or has_password


def setup_required() -> bool:
    settings = get_settings()
    return bool(settings.auth_enabled and not has_login_secret() and not is_setup_complete())


def verify_login(username: str, password: str) -> bool:
    settings = get_settings()
    if not settings.auth_enabled or not has_login_secret():
        return False
    if not secrets.compare_digest((username or "").strip(), auth_username()):
        return False

    stored_hash = auth_password_hash()
    if stored_hash:
        return verify_password(password, stored_hash)

    expected_password = settings.auth_password.get_secret_value() if settings.auth_password else ""
    return bool(expected_password and secrets.compare_digest(password or "", expected_password))


def session_serializer() -> URLSafeTimedSerializer:
    settings = get_settings()
    return URLSafeTimedSerializer(
        settings.secret_key.get_secret_value(),
        salt="absulli-auth-session-v1",
    )


def current_session_version() -> str:
    global _session_version_cache

    if _session_version_cache:
        return _session_version_cache

    now = utcnow()

    with SessionLocal() as db:
        setting = db.query(Setting).filter(Setting.key == SESSION_VERSION_SETTING).first()
        if setting and setting.value:
            _session_version_cache = setting.value
            return _session_version_cache

        version = secrets.token_urlsafe(24)
        if setting:
            setting.value = version
            setting.updated_at = now
        else:
            db.add(Setting(key=SESSION_VERSION_SETTING, value=version, updated_at=now))
        db.commit()
        _session_version_cache = version
        return _session_version_cache


def rotate_session_version() -> str:
    global _session_version_cache

    version = secrets.token_urlsafe(24)
    now = utcnow()

    with SessionLocal() as db:
        setting = db.query(Setting).filter(Setting.key == SESSION_VERSION_SETTING).first()
        if setting:
            setting.value = version
            setting.updated_at = now
        else:
            db.add(Setting(key=SESSION_VERSION_SETTING, value=version, updated_at=now))
        db.commit()

    _session_version_cache = version
    return version


def create_session_token(username: str) -> str:
    return session_serializer().dumps(
        {
            "username": username,
            "session_version": current_session_version(),
        }
    )


def read_session_cookie(request: Request) -> str:
    settings = get_settings()
    token = request.cookies.get(settings.auth_cookie_name)
    if not token:
        return ""
    try:
        payload = session_serializer().loads(token, max_age=settings.auth_max_age_seconds)
    except (BadSignature, SignatureExpired):
        return ""
    if not isinstance(payload, dict):
        return ""

    username = str(payload.get("username") or "")
    session_version = str(payload.get("session_version") or "")
    if username != auth_username():
        return ""
    if not session_version or not secrets.compare_digest(session_version, current_session_version()):
        return ""
    return username


def set_session_cookie(response: Response, username: str) -> None:
    settings = get_settings()
    response.set_cookie(
        settings.auth_cookie_name,
        create_session_token(username),
        max_age=settings.auth_max_age_seconds,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(settings.auth_cookie_name, path="/")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def client_key(request: Request) -> str:
    settings = get_settings()
    if settings.effective_trust_proxy:
        forwarded = request.headers.get("X-Forwarded-For", "").split(",", 1)[0].strip()
        if forwarded:
            return forwarded
    return request.client.host if request.client else "unknown"


def record_login_event(
    request: Request,
    *,
    username: str = "",
    success: bool,
    reason: str = "",
) -> None:
    username = (username or "").strip()[:255]
    reason = (reason or "").strip()[:64]
    key = client_key(request)
    ip_address = key[:128]
    user_agent = request.headers.get("User-Agent", "")[:4000]
    host = request.headers.get("Host", "")[:255]

    with SessionLocal() as db:
        db.add(
            LoginLog(
                client_key=key[:128],
                username=username,
                success=bool(success),
                reason=reason,
                ip_address=ip_address,
                user_agent=user_agent,
                host=host,
            )
        )
        db.commit()


def _login_window_seconds() -> int:
    settings = get_settings()
    return settings.effective_auth_login_window_seconds


def _login_max_attempts() -> int:
    settings = get_settings()
    return settings.effective_auth_login_max_attempts


def _login_lockout_seconds() -> int:
    settings = get_settings()
    return settings.effective_auth_login_lockout_seconds


def is_login_limited(request: Request) -> bool:
    key = client_key(request)
    now = utcnow()
    window_start = now - timedelta(seconds=_login_window_seconds())

    with SessionLocal() as db:
        attempt = db.query(LoginAttempt).filter(LoginAttempt.client_key == key).first()
        if not attempt:
            return False

        if attempt.locked_until and attempt.locked_until > now:
            return True

        if attempt.locked_until or not attempt.first_failed_at or attempt.first_failed_at < window_start:
            attempt.failed_count = 0
            attempt.first_failed_at = None
            attempt.last_failed_at = None
            attempt.locked_until = None
            attempt.updated_at = now
            db.commit()
            return False

        return attempt.failed_count >= _login_max_attempts()


def login_retry_after_seconds(request: Request) -> int:
    key = client_key(request)
    now = utcnow()

    with SessionLocal() as db:
        attempt = db.query(LoginAttempt).filter(LoginAttempt.client_key == key).first()
        if not attempt or not attempt.locked_until or attempt.locked_until <= now:
            return 0

        return max(1, int((attempt.locked_until - now).total_seconds()))


def record_login_failure(request: Request) -> None:
    key = client_key(request)
    now = utcnow()
    window_start = now - timedelta(seconds=_login_window_seconds())

    with SessionLocal() as db:
        attempt = db.query(LoginAttempt).filter(LoginAttempt.client_key == key).first()
        if not attempt:
            attempt = LoginAttempt(client_key=key)
            db.add(attempt)

        if not attempt.first_failed_at or attempt.first_failed_at < window_start:
            attempt.failed_count = 0
            attempt.first_failed_at = now
            attempt.locked_until = None

        attempt.failed_count += 1
        attempt.last_failed_at = now
        attempt.updated_at = now

        if attempt.failed_count >= _login_max_attempts():
            attempt.locked_until = now + timedelta(seconds=_login_lockout_seconds())

        db.commit()


def clear_login_failures(request: Request) -> None:
    key = client_key(request)
    with SessionLocal() as db:
        attempt = db.query(LoginAttempt).filter(LoginAttempt.client_key == key).first()
        if attempt:
            db.delete(attempt)
            db.commit()


def clamp_image_dimension(value: int | None, *, default: int | None = None) -> int | None:
    if value is None:
        return default
    return max(32, min(1200, int(value)))


def clean_image_format(value: str | None) -> str:
    value = (value or "webp").strip().lower()
    return value if value in {"webp", "jpeg", "jpg", "png"} else "webp"