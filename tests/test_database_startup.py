import json
import os
from pathlib import Path
import subprocess
import sys


def test_import_does_not_bootstrap_settings_before_migrations(tmp_path):
    env = os.environ.copy()
    env["ABSULLI_DATA_DIR"] = str(tmp_path)
    env["ABSULLI_SECRET_KEY"] = "test-secret-key-that-is-long-enough-32"
    env.pop("ABSULLI_CORS_ALLOWED_ORIGINS", None)

    code = """
import json
import sqlite3
from pathlib import Path

import absulli.main
from absulli.database.session import init_db

db_path = Path(absulli.main.settings.data_dir) / "absulli.db"
with sqlite3.connect(db_path) as connection:
    before = sorted(row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'"))

init_db()

with sqlite3.connect(db_path) as connection:
    after = sorted(row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'"))
    revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]

print(json.dumps({"before": before, "after": after, "revision": revision}))
"""

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(result.stdout.strip().splitlines()[-1])

    assert payload["before"] == []
    assert "settings" in payload["after"]
    assert "alembic_version" in payload["after"]
    assert payload["revision"] == "0006_add_rss_feed_metadata"


def test_models_match_migrated_database_schema(tmp_path):
    env = os.environ.copy()
    env["ABSULLI_DATA_DIR"] = str(tmp_path)
    env["ABSULLI_SECRET_KEY"] = "test-secret-key-that-is-long-enough-32"
    env.pop("ABSULLI_CORS_ALLOWED_ORIGINS", None)

    code = """
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from absulli.database.models import Base
from absulli.database.session import engine, init_db

init_db()

with engine.connect() as connection:
    context = MigrationContext.configure(connection, opts={"compare_type": True})
    differences = compare_metadata(context, Base.metadata)

assert differences == [], f"model and migrations disagree: {differences}"
"""

    subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )


def test_migrations_preserve_application_loggers_and_quiet_http_clients(monkeypatch):
    import logging

    from absulli.core.logging import configure_logging
    from absulli.database.session import init_db

    root_logger = logging.getLogger()
    monkeypatch.setattr(root_logger, "level", logging.INFO)
    configure_logging()
    init_db()

    assert root_logger.level == logging.INFO
    assert logging.getLogger("absulli.core.config").isEnabledFor(logging.INFO)
    assert logging.getLogger("absulli.core.config").disabled is False
    assert logging.getLogger("absulli.web.routes").disabled is False
    assert logging.getLogger("absulli.notifiers.manager").disabled is False
    assert logging.getLogger("httpx").disabled is False
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING


def test_migrations_preserve_stdout_logging_format(tmp_path):
    env = os.environ.copy()
    env["ABSULLI_DATA_DIR"] = str(tmp_path)
    env["ABSULLI_SECRET_KEY"] = "test-secret-key-that-is-long-enough-32"

    code = """
import logging
import sys

from absulli.core.logging import configure_logging
from absulli.database.session import init_db

configure_logging("INFO")
init_db()
root = logging.getLogger()
assert root.level == logging.INFO
assert logging.getLogger("absulli.core.config").isEnabledFor(logging.INFO)
assert len(root.handlers) == 1
assert root.handlers[0].stream is sys.stdout
assert root.handlers[0].formatter._fmt == "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
logging.getLogger("absulli.core.config").info("logging migration check")
"""

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "[INFO] absulli.core.config: logging migration check" in result.stdout
