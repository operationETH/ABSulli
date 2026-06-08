from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from absulli.core.config import get_settings
from absulli.database.models import Base

settings = get_settings()
engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


MISSING_COLUMN_SQL = {
    "media_items": {
        "library_name": "ALTER TABLE media_items ADD COLUMN library_name VARCHAR(255) DEFAULT ''",
        "author_id": "ALTER TABLE media_items ADD COLUMN author_id VARCHAR(128) DEFAULT ''",
        "series": "ALTER TABLE media_items ADD COLUMN series VARCHAR(512) DEFAULT ''",
        "year": "ALTER TABLE media_items ADD COLUMN year VARCHAR(64) DEFAULT ''",
        "size_bytes": "ALTER TABLE media_items ADD COLUMN size_bytes FLOAT DEFAULT 0",
        "added_at": "ALTER TABLE media_items ADD COLUMN added_at DATETIME",
        "updated_at": "ALTER TABLE media_items ADD COLUMN updated_at DATETIME",
    },
    "activity_sessions": {
        "author": "ALTER TABLE activity_sessions ADD COLUMN author VARCHAR(512) DEFAULT ''",
        "library_id": "ALTER TABLE activity_sessions ADD COLUMN library_id VARCHAR(128) DEFAULT ''",
        "library_name": "ALTER TABLE activity_sessions ADD COLUMN library_name VARCHAR(255) DEFAULT ''",
        "device_name": "ALTER TABLE activity_sessions ADD COLUMN device_name VARCHAR(255) DEFAULT ''",
        "model": "ALTER TABLE activity_sessions ADD COLUMN model VARCHAR(255) DEFAULT ''",
        "updated_at": "ALTER TABLE activity_sessions ADD COLUMN updated_at DATETIME",
        "time_listening": "ALTER TABLE activity_sessions ADD COLUMN time_listening FLOAT DEFAULT 0",
    },
    "listening_history": {
        "author": "ALTER TABLE listening_history ADD COLUMN author VARCHAR(512) DEFAULT ''",
        "library_id": "ALTER TABLE listening_history ADD COLUMN library_id VARCHAR(128) DEFAULT ''",
        "library_name": "ALTER TABLE listening_history ADD COLUMN library_name VARCHAR(255) DEFAULT ''",
        "progress": "ALTER TABLE listening_history ADD COLUMN progress FLOAT DEFAULT 0",
        "device_name": "ALTER TABLE listening_history ADD COLUMN device_name VARCHAR(255) DEFAULT ''",
        "model": "ALTER TABLE listening_history ADD COLUMN model VARCHAR(255) DEFAULT ''",
    },
}


APP_TABLES = set(Base.metadata.tables.keys())


def _alembic_config() -> Config:
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    config.set_main_option("sqlalchemy.url", settings.database_url)
    return config


def _has_existing_app_schema() -> bool:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    return bool(APP_TABLES.intersection(tables))


def _has_alembic_version() -> bool:
    inspector = inspect(engine)
    return "alembic_version" in set(inspector.get_table_names())


def _add_missing_columns() -> None:
    with engine.begin() as conn:
        inspector = inspect(conn)
        existing_tables = set(inspector.get_table_names())
        for table_name, columns in MISSING_COLUMN_SQL.items():
            if table_name not in existing_tables:
                continue
            existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
            for column_name, sql in columns.items():
                if column_name not in existing_columns:
                    conn.execute(text(sql))


def init_db() -> None:
    config = _alembic_config()

    if _has_existing_app_schema() and not _has_alembic_version():
        Base.metadata.create_all(bind=engine)
        _add_missing_columns()
        command.stamp(config, "head")
        return

    command.upgrade(config, "head")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
