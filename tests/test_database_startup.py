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
    assert payload["revision"] == "0004_add_notification_deliveries"
