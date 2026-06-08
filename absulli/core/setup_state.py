from __future__ import annotations

from absulli.core.time import utcnow
from absulli.database.models import Setting
from absulli.database.session import SessionLocal, engine

SETUP_COMPLETE_SETTING = "setup_complete"


def _ensure_settings_table() -> None:
    Setting.__table__.create(bind=engine, checkfirst=True)


def get_setup_setting(key: str, default: str = "") -> str:
    _ensure_settings_table()
    with SessionLocal() as db:
        row = db.query(Setting).filter(Setting.key == key).first()
        return row.value if row and row.value is not None else default


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


def is_setup_complete() -> bool:
    return get_setup_setting(SETUP_COMPLETE_SETTING).strip().lower() == "true"
