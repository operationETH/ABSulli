from pathlib import Path

import absulli.web.update_check as update_check


class Settings:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir


def test_stable_update_available(tmp_path, monkeypatch):
    update_check.reset_update_cache()
    monkeypatch.setattr(
        update_check,
        "_fetch_latest_release",
        lambda: {"tag": "v0.2.12", "url": update_check.RELEASES_URL, "available": True},
    )

    update_check.refresh_update_status(Settings(tmp_path), "0.2.11")
    status = update_check.update_status(Settings(tmp_path), "0.2.11")

    assert status["channel"] == "stable"
    assert status["current_version"] == "v0.2.11"
    assert status["latest_version"] == "v0.2.12"
    assert status["badge_label"] == "Out of Date"
    assert status["badge_class"] == "outdated"
    assert status["header_label"] == "Update available"
    assert status["update_available"] is True


def test_stable_current_version_is_up_to_date(tmp_path, monkeypatch):
    update_check.reset_update_cache()
    monkeypatch.setattr(
        update_check,
        "_fetch_latest_release",
        lambda: {"tag": "v0.2.11", "url": update_check.RELEASES_URL, "available": True},
    )

    update_check.refresh_update_status(Settings(tmp_path), "0.2.11")
    status = update_check.update_status(Settings(tmp_path), "0.2.11")

    assert status["badge_label"] == "Up to Date"
    assert status["badge_class"] == "current"
    assert status["release_url"] == update_check.RELEASES_URL
    assert status["update_available"] is False


def test_current_nightly_has_no_update_warning(tmp_path, monkeypatch):
    update_check.reset_update_cache()
    monkeypatch.setattr(
        update_check,
        "_fetch_latest_nightly_run",
        lambda: {"sha": "4c94cb2abcdef1234567890", "url": update_check.NIGHTLY_WORKFLOW_URL, "available": True},
    )

    update_check.refresh_update_status(Settings(tmp_path), "0.0.0.dev0+nightly.4c94cb2")
    status = update_check.update_status(Settings(tmp_path), "0.0.0.dev0+nightly.4c94cb2")

    assert status["channel"] == "nightly"
    assert status["current_version"] == "sha-4c94cb2"
    assert status["latest_version"] == "sha-4c94cb2"
    assert status["badge_label"] == "Nightly"
    assert status["release_url"] == update_check.NIGHTLY_COMMITS_URL
    assert status["update_available"] is False


def test_old_nightly_shows_new_nightly_build(tmp_path, monkeypatch):
    update_check.reset_update_cache()
    monkeypatch.setattr(
        update_check,
        "_fetch_latest_nightly_run",
        lambda: {"sha": "abcdef1234567890", "url": "https://example.com/nightly", "available": True},
    )
    monkeypatch.setattr(
        update_check,
        "_fetch_commit_comparison",
        lambda base, head: "ahead",
    )

    update_check.refresh_update_status(Settings(tmp_path), "0.0.0.dev0+nightly.4c94cb2")
    status = update_check.update_status(Settings(tmp_path), "0.0.0.dev0+nightly.4c94cb2")

    assert status["channel"] == "nightly"
    assert status["current_version"] == "sha-4c94cb2"
    assert status["latest_version"] == "sha-abcdef1"
    assert status["badge_label"] == "Nightly Update Available"
    assert status["badge_class"] == "outdated"
    assert status["header_label"] == "Nightly Update Available"
    assert status["release_url"] == update_check.NIGHTLY_COMMITS_URL
    assert status["update_available"] is True


def test_newer_installed_nightly_ignores_stale_older_result(tmp_path, monkeypatch):
    update_check.reset_update_cache()
    monkeypatch.setattr(
        update_check,
        "_fetch_latest_nightly_run",
        lambda: {"sha": "e0e9ea5abcdef1234567890", "url": "https://example.com/nightly", "available": True},
    )
    monkeypatch.setattr(
        update_check,
        "_fetch_commit_comparison",
        lambda base, head: "behind",
    )

    update_check.refresh_update_status(Settings(tmp_path), "0.0.0.dev0+nightly.097e06b")
    status = update_check.update_status(Settings(tmp_path), "0.0.0.dev0+nightly.097e06b")

    assert status["channel"] == "nightly"
    assert status["current_version"] == "sha-097e06b"
    assert status["latest_version"] == "sha-097e06b"
    assert status["badge_label"] == "Nightly"
    assert status["badge_class"] == "nightly"
    assert status["release_url"] == update_check.NIGHTLY_COMMITS_URL
    assert status["update_available"] is False


def test_development_build_never_shows_update_warning(tmp_path, monkeypatch):
    update_check.reset_update_cache()
    monkeypatch.setattr(
        update_check,
        "_fetch_latest_release",
        lambda: (_ for _ in ()).throw(AssertionError("development should not check stable releases")),
    )
    monkeypatch.setattr(
        update_check,
        "_fetch_latest_nightly_run",
        lambda: (_ for _ in ()).throw(AssertionError("development should not check nightly builds")),
    )

    update_check.refresh_update_status(Settings(tmp_path), "0.0.0.dev0")
    status = update_check.update_status(Settings(tmp_path), "0.0.0.dev0")

    assert status["channel"] == "development"
    assert status["badge_label"] == "Development"
    assert status["update_available"] is False


def test_update_status_does_not_fetch_when_cache_is_empty(tmp_path, monkeypatch):
    update_check.reset_update_cache()
    monkeypatch.setattr(
        update_check,
        "_fetch_latest_release",
        lambda: (_ for _ in ()).throw(AssertionError("request path should not fetch releases")),
    )

    status = update_check.update_status(Settings(tmp_path), "0.2.11")

    assert status["channel"] == "stable"
    assert status["current_version"] == "v0.2.11"
    assert status["update_available"] is False
