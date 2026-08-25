import os
from pathlib import Path
import stat
import subprocess


def test_entrypoint_intercepts_uvicorn_without_full_argument_string_match():
    script = Path("docker-entrypoint.sh").read_text(encoding="utf-8")

    assert 'if [ "$#" -ge 1 ] && [ "$1" = "uvicorn" ]; then' in script
    assert 'absulli.main:app --host 0.0.0.0 --port 8272 --no-server-header' not in script
    assert '--host "$ABSULLI_HOST"' in script
    assert '--port "$ABSULLI_PORT"' in script


def test_settings_has_login_secret_property_was_removed():
    config = Path("absulli/core/config.py").read_text(encoding="utf-8")
    security = Path("absulli/core/security.py").read_text(encoding="utf-8")

    assert "def has_login_secret(self)" not in config
    assert "def has_login_secret()" in security


def test_entrypoint_restricts_config_permissions_and_umask(tmp_path):
    data_dir = tmp_path / "config"
    data_dir.mkdir(mode=0o755)
    for name in ["absulli.db", "absulli.db-wal", "absulli.db-shm", "secret_key"]:
        path = data_dir / name
        path.write_text("test", encoding="utf-8")
        path.chmod(0o644)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_id = bin_dir / "id"
    fake_id.write_text("#!/bin/sh\nprintf '1000\\n'\n", encoding="utf-8")
    fake_id.chmod(0o700)
    umask_file = tmp_path / "umask"
    env = {
        **os.environ,
        "ABSULLI_DATA_DIR": str(data_dir),
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
    }

    subprocess.run(
        ["/bin/sh", "./docker-entrypoint.sh", "/bin/sh", "-c", f"umask > {umask_file}"],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )

    assert stat.S_IMODE(data_dir.stat().st_mode) == 0o700
    for name in ["absulli.db", "absulli.db-wal", "absulli.db-shm", "secret_key"]:
        assert stat.S_IMODE((data_dir / name).stat().st_mode) == 0o600
    assert umask_file.read_text(encoding="utf-8").strip() == "0077"
