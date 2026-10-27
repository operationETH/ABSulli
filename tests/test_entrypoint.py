import os
from pathlib import Path
import stat
import subprocess
import tempfile
import shutil
import pytest


needs_root = pytest.mark.skipif(os.geteuid() != 0, reason="requires root for ownership and uid tests")


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


def test_docker_image_hardening_preserves_runtime_layout():
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    ignored = Path(".dockerignore").read_text(encoding="utf-8").splitlines()
    assert "FROM python:3.14-slim@sha256:" in dockerfile
    assert "COPY --chmod=0755 . ." in dockerfile
    assert "COPY --chown=absulli:absulli . ." not in dockerfile
    assert "chmod -R a-w /app" not in dockerfile
    assert "python -m compileall -q /app/absulli" in dockerfile
    assert "python -m pip uninstall -y pip" in dockerfile
    assert "rm -rf /usr/local/lib/python3.14/ensurepip" in dockerfile
    assert "touch /config/DOCKER" not in dockerfile
    assert "curl ca-certificates gosu" not in dockerfile
    assert "os.environ.get(\"ABSULLI_PORT\", \"8272\")" in dockerfile
    assert "README.md" not in ignored
    for excluded in (
        "tests/",
        "screenshots/",
        ".github/",
        "scripts/",
        ".venv/",
        "venv/",
        "*.zip",
    ):
        assert excluded in ignored

def test_entrypoint_uses_numeric_identity_without_account_mutation():
    script = Path("docker-entrypoint.sh").read_text(encoding="utf-8")
    assert 'exec gosu "$PUID:$PGID" "$@"' in script
    assert "usermod" not in script
    assert "groupmod" not in script
    assert 'chown -R "$PUID:$PGID" "$ABSULLI_DATA_DIR"' in script
    assert r'find "$ABSULLI_DATA_DIR" \( ! -uid "$PUID" -o ! -gid "$PGID" \)' in script


@needs_root
def test_entrypoint_numeric_ids_and_invalid_fallback(tmp_path):
    data_dir = tmp_path / "config"
    data_dir.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gosu = bin_dir / "gosu"
    gosu.write_text('#!/bin/sh\nprintf "identity=%s\\n" "$1"\n', encoding="utf-8")
    gosu.chmod(0o755)
    env = {**os.environ, "ABSULLI_DATA_DIR": str(data_dir), "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    for puid, pgid, expected in (("1000", "1000", "1000:1000"), ("99", "100", "99:100"), ("0", "0", "1000:1000"), ("invalid", "", "1000:1000")):
        result = subprocess.run(["/bin/sh", "./docker-entrypoint.sh", "true"], env={**env, "PUID": puid, "PGID": pgid}, text=True, capture_output=True, check=True)
        assert f"identity={expected}" in result.stdout
        if puid in ("0", "invalid"):
            assert "WARNING" in result.stderr


@needs_root
def test_entrypoint_repairs_mixed_ownership(tmp_path):
    data_dir = tmp_path / "config"
    data_dir.mkdir()
    owned = data_dir / "absulli.db"
    owned.write_text("db", encoding="utf-8")
    nested = data_dir / "covers"
    nested.mkdir()
    (nested / "one.bin").write_bytes(b"image")
    os.chown(data_dir, 1000, 1000)
    os.chown(owned, 0, 0)
    os.chown(nested, 1000, 1000)
    os.chown(nested / "one.bin", 1000, 1000)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gosu = bin_dir / "gosu"
    gosu.write_text('#!/bin/sh\nexit 0\n', encoding="utf-8")
    gosu.chmod(0o755)
    subprocess.run(["/bin/sh", "./docker-entrypoint.sh", "true"], env={**os.environ, "ABSULLI_DATA_DIR": str(data_dir), "PATH": f"{bin_dir}:{os.environ['PATH']}", "PUID": "1000", "PGID": "1000"}, check=True, capture_output=True)
    assert owned.stat().st_uid == 1000
    assert owned.stat().st_gid == 1000
    assert stat.S_IMODE(data_dir.stat().st_mode) == 0o700


@needs_root
def test_entrypoint_preserves_direct_nonroot_execution():
    root = Path(tempfile.mkdtemp(prefix="absulli-uid-test-", dir="/tmp"))
    try:
        root.chmod(0o755)
        entrypoint = root / "docker-entrypoint.sh"
        shutil.copyfile("docker-entrypoint.sh", entrypoint)
        entrypoint.chmod(0o644)
        data_dir = root / "config"
        data_dir.mkdir()
        os.chown(data_dir, 1234, 1234)
        os.chmod(data_dir, 0o700)
        def drop_privileges():
            os.setgid(1234)
            os.setuid(1234)
        result = subprocess.run(
            ["/bin/sh", str(entrypoint), "id", "-u"],
            env={**os.environ, "ABSULLI_DATA_DIR": str(data_dir)},
            preexec_fn=drop_privileges,
            text=True,
            capture_output=True,
            check=True,
        )
        assert result.stdout.strip().endswith("1234")
    finally:
        shutil.rmtree(root)


@needs_root
def test_entrypoint_explains_unwritable_config_for_direct_nonroot_user():
    root = Path(tempfile.mkdtemp(prefix="absulli-permission-test-", dir="/tmp"))
    try:
        root.chmod(0o755)
        entrypoint = root / "docker-entrypoint.sh"
        shutil.copyfile("docker-entrypoint.sh", entrypoint)
        entrypoint.chmod(0o644)
        data_dir = root / "config"
        data_dir.mkdir(mode=0o755)
        os.chown(data_dir, 1000, 1000)
        def drop_privileges():
            os.setgid(1234)
            os.setuid(1234)
        result = subprocess.run(
            ["/bin/sh", str(entrypoint), "true"],
            env={**os.environ, "ABSULLI_DATA_DIR": str(data_dir)},
            preexec_fn=drop_privileges,
            text=True,
            capture_output=True,
        )
        assert result.returncode != 0
        assert "ERROR: Cannot set permissions" in result.stderr
        assert "uid=1234 gid=1234" in result.stderr
        assert "directory owned by 1000:1000" in result.stderr
        assert "configure PUID/PGID instead" in result.stderr
    finally:
        shutil.rmtree(root)


@needs_root
def test_entrypoint_direct_nonroot_matching_ownership_works():
    root = Path(tempfile.mkdtemp(prefix="absulli-permission-test-", dir="/tmp"))
    try:
        root.chmod(0o755)
        entrypoint = root / "docker-entrypoint.sh"
        shutil.copyfile("docker-entrypoint.sh", entrypoint)
        entrypoint.chmod(0o644)
        data_dir = root / "config"
        data_dir.mkdir(mode=0o755)
        os.chown(data_dir, 1234, 1234)
        secret = data_dir / "secret_key"
        secret.write_text("placeholder", encoding="utf-8")
        os.chown(secret, 1234, 1234)
        secret.chmod(0o644)
        def drop_privileges():
            os.setgid(1234)
            os.setuid(1234)
        result = subprocess.run(
            ["/bin/sh", str(entrypoint), "true"],
            env={**os.environ, "ABSULLI_DATA_DIR": str(data_dir)},
            preexec_fn=drop_privileges,
            text=True,
            capture_output=True,
        )
        assert result.returncode == 0, result.stderr
        assert stat.S_IMODE(data_dir.stat().st_mode) == 0o700
        assert stat.S_IMODE(secret.stat().st_mode) == 0o600
    finally:
        shutil.rmtree(root)


@needs_root
def test_entrypoint_explains_root_owned_database_for_direct_nonroot_user():
    root = Path(tempfile.mkdtemp(prefix="absulli-permission-test-", dir="/tmp"))
    try:
        root.chmod(0o755)
        entrypoint = root / "docker-entrypoint.sh"
        shutil.copyfile("docker-entrypoint.sh", entrypoint)
        entrypoint.chmod(0o644)
        data_dir = root / "config"
        data_dir.mkdir(mode=0o700)
        os.chown(data_dir, 1234, 1234)
        database = data_dir / "absulli.db"
        database.write_text("placeholder", encoding="utf-8")
        os.chown(database, 0, 0)
        database.chmod(0o644)
        def drop_privileges():
            os.setgid(1234)
            os.setuid(1234)
        result = subprocess.run(
            ["/bin/sh", str(entrypoint), "true"],
            env={**os.environ, "ABSULLI_DATA_DIR": str(data_dir)},
            preexec_fn=drop_privileges,
            text=True,
            capture_output=True,
        )
        assert result.returncode != 0
        assert "ERROR: Cannot set permissions in" in result.stderr
        assert "uid=1234 gid=1234" in result.stderr
        assert "configure PUID/PGID instead" in result.stderr
        assert stat.S_IMODE(database.stat().st_mode) == 0o644
    finally:
        shutil.rmtree(root)
