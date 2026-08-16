import re

import absulli.core.security as security
import absulli.web.routes as web_routes
from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient

from absulli.core.config import get_settings
from absulli.core.security import (
    DEFAULT_PBKDF2_ITERATIONS,
    SecurityHeadersMiddleware,
    clamp_image_dimension,
    clean_image_format,
    password_hash,
    read_session_cookie,
    rotate_session_version,
    set_session_cookie,
    verify_password,
)
from absulli.web.routes import router as web_router
from absulli.web.api import router as api_router
from absulli.database.models import LoginAttempt, LoginLog
from absulli.database.session import SessionLocal


def test_clean_image_format_allowlist():
    assert clean_image_format("webp") == "webp"
    assert clean_image_format("JPEG") == "jpeg"
    assert clean_image_format("gif") == "webp"
    assert clean_image_format("") == "webp"


def test_clamp_image_dimension_bounds():
    assert clamp_image_dimension(None, default=300) == 300
    assert clamp_image_dimension(None) is None
    assert clamp_image_dimension(1) == 32
    assert clamp_image_dimension(300) == 300
    assert clamp_image_dimension(5000) == 1200


def test_password_hash_roundtrip():
    hashed = password_hash("correct horse battery staple", iterations=1000)
    assert verify_password("correct horse battery staple", hashed)
    assert not verify_password("wrong", hashed)


def test_default_pbkdf2_iterations_matches_tautulli_parity_target():
    assert DEFAULT_PBKDF2_ITERATIONS == 600_000


def test_browser_routes_redirect_to_login_while_api_stays_401(monkeypatch):
    monkeypatch.setenv("ABSULLI_AUTH_ENABLED", "true")
    monkeypatch.setenv("ABSULLI_AUTH_USERNAME", "admin")
    monkeypatch.setenv("ABSULLI_AUTH_PASSWORD", "test123")
    monkeypatch.setenv("ABSULLI_SECRET_KEY", "test-secret-key-that-is-long-enough-32")
    get_settings.cache_clear()

    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/")
    def home():
        return {"ok": True}

    @app.get("/api/status")
    def api_status():
        return {"ok": True}

    client = TestClient(app)

    browser_response = client.get("/", follow_redirects=False)
    assert browser_response.status_code == 303
    assert browser_response.headers["location"] == "/login?next=%2F"

    api_response = client.get("/api/status")
    assert api_response.status_code == 401
    assert api_response.text == "authentication required"
    assert api_response.headers["www-authenticate"] == "Bearer"

    csp = browser_response.headers["content-security-policy"]
    assert "style-src 'self' 'nonce-" in csp
    assert "unsafe-inline" not in csp
    assert "frame-ancestors 'none'" in csp
    assert "{nonce}" not in csp

    get_settings.cache_clear()


def test_api_and_metrics_tokens_still_work(monkeypatch):
    monkeypatch.setenv("ABSULLI_AUTH_ENABLED", "true")
    monkeypatch.setenv("ABSULLI_AUTH_USERNAME", "admin")
    monkeypatch.setenv("ABSULLI_AUTH_PASSWORD", "test123")
    monkeypatch.setenv("ABSULLI_SECRET_KEY", "test-secret-key-that-is-long-enough-32")
    monkeypatch.setenv("ABSULLI_API_ENABLED", "true")
    monkeypatch.setenv("ABSULLI_API_KEY", "api-token")
    monkeypatch.setenv("ABSULLI_METRICS_TOKEN", "metrics-token")
    get_settings.cache_clear()

    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/api/status")
    def api_status():
        return {"ok": True}

    @app.get("/metrics")
    def metrics():
        return Response("metric 1\n", media_type="text/plain")

    client = TestClient(app)

    assert client.get("/api/status").status_code == 401
    assert client.get("/metrics").status_code == 401

    api_response = client.get("/api/status", headers={"X-API-Key": "api-token"})
    assert api_response.status_code == 200

    metrics_response = client.get("/metrics", headers={"Authorization": "Bearer metrics-token"})
    assert metrics_response.status_code == 200
    assert metrics_response.text == "metric 1\n"

    wrong_metrics_response = client.get("/metrics", headers={"Authorization": "Bearer api-token"})
    assert wrong_metrics_response.status_code == 401

    empty_metrics_response = client.get(
        "/metrics",
        headers={"Authorization": "Bearer ", "X-Absulli-Metrics-Token": ""},
    )
    assert empty_metrics_response.status_code == 401

    get_settings.cache_clear()




def test_verify_metrics_access_rejects_empty_provided_token(monkeypatch):
    monkeypatch.setenv("ABSULLI_AUTH_ENABLED", "false")
    monkeypatch.setenv("ABSULLI_METRICS_TOKEN", "metrics-token")
    get_settings.cache_clear()

    app = FastAPI()

    @app.get("/metrics")
    def metrics(request: Request):
        security.verify_metrics_access(request)
        return Response("metric 1\n", media_type="text/plain")

    client = TestClient(app)

    response = client.get(
        "/metrics",
        headers={"Authorization": "Bearer ", "X-Absulli-Metrics-Token": ""},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "metrics authentication required"

    get_settings.cache_clear()


def test_client_key_ignores_x_forwarded_for_until_trust_proxy_enabled(monkeypatch):
    app = FastAPI()

    @app.get("/client-key")
    def client_key_endpoint(request: Request):
        return {"client_key": security.client_key(request)}

    monkeypatch.setenv("ABSULLI_TRUST_PROXY", "false")
    get_settings.cache_clear()
    client = TestClient(app)
    untrusted = client.get("/client-key", headers={"X-Forwarded-For": "203.0.113.9"})
    assert untrusted.json()["client_key"] != "203.0.113.9"

    monkeypatch.setenv("ABSULLI_TRUST_PROXY", "true")
    get_settings.cache_clear()
    trusted = client.get("/client-key", headers={"X-Forwarded-For": "203.0.113.9, 10.0.0.1"})
    assert trusted.json()["client_key"] == "203.0.113.9"

    get_settings.cache_clear()


def test_cookie_secure_env_sets_secure_attribute(monkeypatch):
    monkeypatch.setenv("ABSULLI_COOKIE_SECURE", "true")
    monkeypatch.setenv("ABSULLI_SECRET_KEY", "test-secret-key-that-is-long-enough-32")
    get_settings.cache_clear()

    response = Response()
    set_session_cookie(response, "admin")

    assert "secure" in response.headers["set-cookie"].lower()

    get_settings.cache_clear()


def test_hsts_header_when_enabled(monkeypatch):
    monkeypatch.setenv("ABSULLI_SECURITY_HSTS_ENABLED", "true")
    monkeypatch.setenv("ABSULLI_SECURITY_HSTS_MAX_AGE_SECONDS", "31536000")
    monkeypatch.setenv("ABSULLI_SECURITY_HSTS_INCLUDE_SUBDOMAINS", "true")
    monkeypatch.setenv("ABSULLI_SECURITY_HSTS_PRELOAD", "false")
    get_settings.cache_clear()

    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    client = TestClient(app)
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.headers["strict-transport-security"] == (
        "max-age=31536000; includeSubDomains"
    )

    get_settings.cache_clear()


def test_login_csrf_required_and_valid_token_allows_login(monkeypatch):
    monkeypatch.setenv("ABSULLI_AUTH_ENABLED", "true")
    monkeypatch.setenv("ABSULLI_AUTH_USERNAME", "admin")
    monkeypatch.setenv("ABSULLI_AUTH_PASSWORD", "test123")
    monkeypatch.setenv("ABSULLI_SECRET_KEY", "test-secret-key-that-is-long-enough-32")
    get_settings.cache_clear()

    monkeypatch.setattr(web_routes, "record_login_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(security, "current_session_version", lambda: "test-session-version")

    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(web_router)

    client = TestClient(app)

    login_page = client.get("/login")
    assert login_page.status_code == 200
    assert "absulli_csrf" in login_page.cookies

    missing_csrf = client.post(
        "/login",
        data={"username": "admin", "password": "test123", "next": "/"},
        follow_redirects=False,
    )
    assert missing_csrf.status_code == 403
    assert "Your login form expired" in missing_csrf.text
    assert "absulli_csrf" in missing_csrf.headers["set-cookie"]
    assert "httponly" not in missing_csrf.headers["set-cookie"].lower()

    match = re.search(r'name="csrf_token" value="([^"]+)"', missing_csrf.text)
    assert match is not None
    csrf_token = match.group(1)

    valid_login = client.post(
        "/login",
        data={
            "username": "admin",
            "password": "test123",
            "next": "/",
            "csrf_token": csrf_token,
        },
        follow_redirects=False,
    )

    assert valid_login.status_code == 303
    assert valid_login.headers["location"] == "/"

    set_cookie = valid_login.headers["set-cookie"]
    assert "absulli_session" in set_cookie
    assert "absulli_csrf" in set_cookie
    assert "httponly" not in set_cookie.split("absulli_csrf", 1)[1].split(",", 1)[0].lower()

    get_settings.cache_clear()


def test_login_lockout_response_includes_retry_after(monkeypatch):
    monkeypatch.setenv("ABSULLI_AUTH_ENABLED", "true")
    monkeypatch.setenv("ABSULLI_AUTH_USERNAME", "admin")
    monkeypatch.setenv("ABSULLI_AUTH_PASSWORD", "test123")
    monkeypatch.setenv("ABSULLI_SECRET_KEY", "test-secret-key-that-is-long-enough-32")
    get_settings.cache_clear()

    monkeypatch.setattr(web_routes, "validate_csrf_token", lambda request, token: True)
    monkeypatch.setattr(web_routes, "record_login_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(web_routes, "is_login_limited", lambda request: True)
    monkeypatch.setattr(web_routes, "login_retry_after_seconds", lambda request: 123)

    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(web_router)

    client = TestClient(app)
    response = client.post(
        "/login",
        data={
            "username": "admin",
            "password": "wrong",
            "next": "/",
            "csrf_token": "test-csrf-token",
        },
        follow_redirects=False,
    )

    assert response.status_code == 429
    assert response.headers["retry-after"] == "123"
    assert "Too many failed login attempts" in response.text

    get_settings.cache_clear()


def test_favicon_request_does_not_redirect_or_rotate_csrf(monkeypatch):
    monkeypatch.setenv("ABSULLI_AUTH_ENABLED", "true")
    monkeypatch.setenv("ABSULLI_AUTH_USERNAME", "admin")
    monkeypatch.setenv("ABSULLI_AUTH_PASSWORD", "test123")
    monkeypatch.setenv("ABSULLI_SECRET_KEY", "test-secret-key-that-is-long-enough-32")
    get_settings.cache_clear()

    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(web_router)

    client = TestClient(app)

    login_page = client.get("/login")
    assert login_page.status_code == 200
    original_csrf = login_page.cookies.get("absulli_csrf")
    assert original_csrf

    favicon_response = client.get("/favicon.ico", follow_redirects=False)
    assert favicon_response.status_code == 204
    assert "location" not in favicon_response.headers
    assert "set-cookie" not in favicon_response.headers
    assert client.cookies.get("absulli_csrf") == original_csrf

    get_settings.cache_clear()


def test_login_log_model_captures_audit_fields():
    columns = LoginLog.__table__.columns

    assert "client_key" in columns
    assert "username" in columns
    assert "success" in columns
    assert "reason" in columns
    assert "ip_address" in columns
    assert "user_agent" in columns
    assert "host" in columns
    assert "created_at" in columns


def test_cors_allowlist_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ABSULLI_CORS_ALLOWED_ORIGINS", raising=False)
    monkeypatch.setenv("ABSULLI_SECRET_KEY", "test-secret-key-that-is-long-enough-32")
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.cors_allowed_origins_list == []

    get_settings.cache_clear()


def test_cors_allowlist_parses_csv_values(monkeypatch):
    monkeypatch.setenv(
        "ABSULLI_CORS_ALLOWED_ORIGINS",
        "https://absulli.example.com, https://dashboard.example.com",
    )
    monkeypatch.setenv("ABSULLI_CORS_ALLOWED_METHODS", "GET, POST")
    monkeypatch.setenv("ABSULLI_CORS_ALLOWED_HEADERS", "Authorization, X-CSRF-Token")
    monkeypatch.setenv("ABSULLI_SECRET_KEY", "test-secret-key-that-is-long-enough-32")
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.cors_allowed_origins_list == [
        "https://absulli.example.com",
        "https://dashboard.example.com",
    ]
    assert settings.cors_allowed_methods_list == ["GET", "POST"]
    assert settings.cors_allowed_headers_list == ["Authorization", "X-CSRF-Token"]

    get_settings.cache_clear()



def test_session_cookie_requires_current_token_version(monkeypatch):
    monkeypatch.setenv("ABSULLI_AUTH_ENABLED", "true")
    monkeypatch.setenv("ABSULLI_AUTH_USERNAME", "admin")
    monkeypatch.setenv("ABSULLI_AUTH_PASSWORD", "test123")
    monkeypatch.setenv("ABSULLI_SECRET_KEY", "test-secret-key-that-is-long-enough-32")
    get_settings.cache_clear()

    import absulli.core.security as security

    monkeypatch.setattr(security, "current_session_version", lambda: "version-one")
    response = Response()
    set_session_cookie(response, "admin")
    session_cookie = response.headers["set-cookie"].split(";", 1)[0].split("=", 1)[1]

    class DummyURL:
        path = "/"
        query = ""

    class DummyRequest:
        cookies = {get_settings().auth_cookie_name: session_cookie}
        url = DummyURL()

    assert read_session_cookie(DummyRequest()) == "admin"

    monkeypatch.setattr(security, "current_session_version", lambda: "version-two")
    assert read_session_cookie(DummyRequest()) == ""

    get_settings.cache_clear()


def test_revoke_sessions_endpoint_requires_api_token(monkeypatch):
    monkeypatch.setenv("ABSULLI_AUTH_ENABLED", "true")
    monkeypatch.setenv("ABSULLI_AUTH_USERNAME", "admin")
    monkeypatch.setenv("ABSULLI_AUTH_PASSWORD", "test123")
    monkeypatch.setenv("ABSULLI_SECRET_KEY", "test-secret-key-that-is-long-enough-32")
    monkeypatch.setenv("ABSULLI_API_ENABLED", "true")
    monkeypatch.setenv("ABSULLI_API_KEY", "api-token")
    get_settings.cache_clear()

    import absulli.web.api as api

    called = {"rotated": False}

    def fake_rotate_session_version():
        called["rotated"] = True
        return "new-version"

    monkeypatch.setattr(api, "rotate_session_version", fake_rotate_session_version)

    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(api_router)

    client = TestClient(app)

    no_token = client.post("/api/auth/revoke-sessions")
    assert no_token.status_code == 401
    assert not called["rotated"]

    response = client.post(
        "/api/auth/revoke-sessions",
        headers={"X-API-Key": "api-token"},
    )
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "revoked_sessions": True}
    assert called["rotated"]

    get_settings.cache_clear()


def test_setup_mode_allows_favicon_and_healthz_without_redirect_or_csrf_rotation(monkeypatch):
    monkeypatch.setenv("ABSULLI_AUTH_ENABLED", "true")
    monkeypatch.setenv("ABSULLI_SECRET_KEY", "test-secret-key-that-is-long-enough-32")
    monkeypatch.delenv("ABSULLI_AUTH_PASSWORD", raising=False)
    monkeypatch.delenv("ABSULLI_AUTH_PASSWORD_HASH", raising=False)
    get_settings.cache_clear()

    import absulli.core.security as security

    monkeypatch.setattr(security, "is_setup_complete", lambda: False)
    monkeypatch.setattr(security, "has_login_secret", lambda: False)

    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(web_router)

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    client = TestClient(app)

    setup_page = client.get("/setup")
    assert setup_page.status_code == 200
    original_csrf = setup_page.cookies.get("absulli_csrf")
    assert original_csrf

    favicon_response = client.get("/favicon.ico", follow_redirects=False)
    assert favicon_response.status_code == 204
    assert "location" not in favicon_response.headers
    assert "set-cookie" not in favicon_response.headers
    assert client.cookies.get("absulli_csrf") == original_csrf

    healthz_response = client.get("/healthz", follow_redirects=False)
    assert healthz_response.status_code == 200
    assert "location" not in healthz_response.headers

    get_settings.cache_clear()


def test_login_rate_limit_uses_forwarded_for_when_trust_proxy_enabled(monkeypatch):
    monkeypatch.setenv("ABSULLI_AUTH_ENABLED", "true")
    monkeypatch.setenv("ABSULLI_AUTH_USERNAME", "admin")
    monkeypatch.setenv("ABSULLI_AUTH_PASSWORD", "test123")
    monkeypatch.setenv("ABSULLI_SECRET_KEY", "test-secret-key-that-is-long-enough-32")
    monkeypatch.setenv("ABSULLI_TRUST_PROXY", "true")
    monkeypatch.setenv("ABSULLI_AUTH_LOGIN_MAX_ATTEMPTS", "2")
    monkeypatch.setenv("ABSULLI_AUTH_LOGIN_WINDOW_SECONDS", "900")
    monkeypatch.setenv("ABSULLI_AUTH_LOGIN_LOCKOUT_SECONDS", "900")
    get_settings.cache_clear()

    forwarded_ip = "198.51.100.44"
    other_forwarded_ip = "198.51.100.45"
    with SessionLocal() as db:
        db.query(LoginAttempt).filter(LoginAttempt.client_key.in_([forwarded_ip, other_forwarded_ip])).delete(
            synchronize_session=False
        )
        db.commit()

    monkeypatch.setattr(web_routes, "validate_csrf_token", lambda request, token: True)
    monkeypatch.setattr(web_routes, "record_login_event", lambda *args, **kwargs: None)

    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(web_router)
    client = TestClient(app)

    for _ in range(2):
        response = client.post(
            "/login",
            headers={"X-Forwarded-For": f"{forwarded_ip}, 10.0.0.10"},
            data={"username": "admin", "password": "wrong", "next": "/", "csrf_token": "ok"},
            follow_redirects=False,
        )
        assert response.status_code == 401

    limited = client.post(
        "/login",
        headers={"X-Forwarded-For": f"{forwarded_ip}, 10.0.0.10"},
        data={"username": "admin", "password": "wrong", "next": "/", "csrf_token": "ok"},
        follow_redirects=False,
    )
    assert limited.status_code == 429

    other_ip = client.post(
        "/login",
        headers={"X-Forwarded-For": f"{other_forwarded_ip}, 10.0.0.10"},
        data={"username": "admin", "password": "wrong", "next": "/", "csrf_token": "ok"},
        follow_redirects=False,
    )
    assert other_ip.status_code == 401

    with SessionLocal() as db:
        attempt = db.query(LoginAttempt).filter_by(client_key=forwarded_ip).one()
        assert attempt.failed_count == 2
        assert attempt.locked_until is not None
        assert db.query(LoginAttempt).filter_by(client_key=other_forwarded_ip).one().failed_count == 1
        db.query(LoginAttempt).filter(LoginAttempt.client_key.in_([forwarded_ip, other_forwarded_ip])).delete(
            synchronize_session=False
        )
        db.commit()

    get_settings.cache_clear()
