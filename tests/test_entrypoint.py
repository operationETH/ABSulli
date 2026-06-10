from pathlib import Path


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
