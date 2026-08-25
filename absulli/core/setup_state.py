from __future__ import annotations

import secrets
import threading
import time

from sqlalchemy import inspect

from absulli.core.time import utcnow
from absulli.database.models import Setting
from absulli.database.session import SessionLocal, engine

SETUP_COMPLETE_SETTING = "setup_complete"
_SETUP_CACHE_TTL_SECONDS = 5.0

_settings_table_ready = False
_settings_table_lock = threading.Lock()
_setup_cache: dict[str, tuple[str, float]] = {}
_setup_cache_lock = threading.Lock()


def _ensure_settings_table() -> None:
    global _settings_table_ready

    if _settings_table_ready:
        return

    with _settings_table_lock:
        if _settings_table_ready:
            return
        Setting.__table__.create(bind=engine, checkfirst=True)
        _settings_table_ready = True


def _cache_get(key: str) -> str | None:
    now = time.monotonic()
    with _setup_cache_lock:
        cached = _setup_cache.get(key)
        if not cached:
            return None
        value, expires_at = cached
        if now >= expires_at:
            _setup_cache.pop(key, None)
            return None
        return value


def _cache_set(key: str, value: str) -> None:
    expires_at = time.monotonic() + _SETUP_CACHE_TTL_SECONDS
    with _setup_cache_lock:
        _setup_cache[key] = (value, expires_at)


def _cache_invalidate(*keys: str) -> None:
    with _setup_cache_lock:
        if keys:
            for key in keys:
                _setup_cache.pop(key, None)
        else:
            _setup_cache.clear()


def warm_setup_state_cache() -> None:
    """Ensure the setup settings table exists during startup, not during a request."""
    _ensure_settings_table()
    for key in (SETUP_COMPLETE_SETTING, "auth_username", "auth_password_hash", "api_enabled", "api_token"):
        get_setup_setting(key)


def ensure_api_token() -> str:
    token = get_setup_setting("api_token", "").strip()
    if token:
        return token
    token = secrets.token_urlsafe(32)
    set_setup_setting("api_token", token)
    return token


def get_setup_setting_if_available(key: str, default: str = "") -> str:
    cached = _cache_get(key)
    if cached is not None:
        return cached

    if "settings" not in set(inspect(engine).get_table_names()):
        return default

    with SessionLocal() as db:
        row = db.query(Setting).filter(Setting.key == key).first()
        value = row.value if row and row.value is not None else default

    _cache_set(key, value)
    return value


def get_setup_setting(key: str, default: str = "") -> str:
    cached = _cache_get(key)
    if cached is not None:
        return cached

    _ensure_settings_table()
    with SessionLocal() as db:
        row = db.query(Setting).filter(Setting.key == key).first()
        value = row.value if row and row.value is not None else default

    _cache_set(key, value)
    return value


def set_setup_setting(key: str, value: str) -> None:
    _ensure_settings_table()
    now = utcnow()
    with SessionLocal() as db:
        row = db.query(Setting).filter(Setting.key == key).first()
        if row:
            row.value = value
            row.updated_at = now
        else:
            db.add(Setting(key=key, value=value, updated_at=now))
        db.commit()

    _cache_set(key, value)


def set_setup_settings(values: dict[str, str]) -> None:
    _ensure_settings_table()
    now = utcnow()
    with SessionLocal() as db:
        existing = {row.key: row for row in db.query(Setting).filter(Setting.key.in_(values)).all()}
        for key, value in values.items():
            row = existing.get(key)
            if row:
                row.value = value
                row.updated_at = now
            else:
                db.add(Setting(key=key, value=value, updated_at=now))
        db.commit()

    for key, value in values.items():
        _cache_set(key, value)


def is_setup_complete() -> bool:
    return get_setup_setting(SETUP_COMPLETE_SETTING).strip().lower() == "true"
