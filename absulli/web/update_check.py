import json
import re
import threading
import time
from pathlib import Path

import httpx

LATEST_RELEASE_API_URL = "https://api.github.com/repos/operationETH/ABSulli/releases/latest"
LATEST_NIGHTLY_RUN_API_URL = "https://api.github.com/repos/operationETH/ABSulli/actions/workflows/nightly.yml/runs?branch=nightly&status=success&per_page=1"
REPOSITORY_URL = "https://github.com/operationETH/ABSulli"
RELEASES_URL = f"{REPOSITORY_URL}/releases"
NIGHTLY_WORKFLOW_URL = "https://github.com/operationETH/ABSulli/actions/workflows/nightly.yml"
CACHE_TTL_SECONDS = 21600
FAILURE_CACHE_SECONDS = 900

_cache_lock = threading.Lock()
_memory_release: dict[str, object] | None = None
_memory_release_expires_at = 0.0
_memory_nightly: dict[str, object] | None = None
_memory_nightly_expires_at = 0.0


def _stable_version(value: str) -> tuple[int, int, int] | None:
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", str(value or "").strip())
    if not match:
        return None
    return tuple(int(match.group(index)) for index in range(1, 4))


def _nightly_sha(value: str) -> str:
    match = re.search(r"nightly[.+-]?([0-9a-f]{7,40})", str(value or ""), re.IGNORECASE)
    return match.group(1).lower() if match else ""


def _channel(value: str) -> str:
    version = str(value or "").strip().lower()
    if "nightly" in version:
        return "nightly"
    if _stable_version(version):
        return "stable"
    return "development"


def _display_version(value: str, channel: str) -> str:
    version = str(value or "").strip()
    if channel == "stable":
        return f"v{version.lstrip('v')}"
    if channel == "nightly":
        sha = _nightly_sha(version)
        if sha:
            return f"sha-{sha[:7]}"
    return version or "Unknown"


def _cache_path(data_dir: Path, name: str) -> Path:
    return data_dir / name


def _read_disk_cache(data_dir: Path, name: str, required_field: str) -> dict[str, object] | None:
    path = _cache_path(data_dir, name)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    expires_at = float(payload.get("expires_at") or 0)
    value = payload.get("value")
    if expires_at <= time.time() or not isinstance(value, dict):
        return None
    if not str(value.get(required_field) or "").strip():
        return None
    return value


def _write_disk_cache(data_dir: Path, name: str, value: dict[str, object], ttl: int) -> None:
    path = _cache_path(data_dir, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(".tmp")
    temp_path.write_text(
        json.dumps({"expires_at": time.time() + ttl, "value": value}),
        encoding="utf-8",
    )
    temp_path.replace(path)


def _github_client() -> httpx.Client:
    return httpx.Client(
        timeout=3.0,
        follow_redirects=True,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "ABSulli"},
    )


def _fetch_latest_release() -> dict[str, object]:
    with _github_client() as client:
        response = client.get(LATEST_RELEASE_API_URL)
        response.raise_for_status()
        payload = response.json()
    tag = str(payload.get("tag_name") or "").strip()
    if not tag:
        raise ValueError("Latest release did not include a tag")
    return {
        "tag": tag,
        "url": str(payload.get("html_url") or "").strip() or RELEASES_URL,
        "available": True,
    }


def _fetch_latest_nightly_run() -> dict[str, object]:
    with _github_client() as client:
        response = client.get(LATEST_NIGHTLY_RUN_API_URL)
        response.raise_for_status()
        payload = response.json()
    runs = payload.get("workflow_runs")
    if not isinstance(runs, list) or not runs:
        raise ValueError("Latest nightly workflow run was not available")
    run = runs[0]
    sha = str(run.get("head_sha") or "").strip().lower()
    if not sha:
        raise ValueError("Latest nightly workflow run did not include a commit SHA")
    return {
        "sha": sha,
        "url": str(run.get("html_url") or "").strip() or NIGHTLY_WORKFLOW_URL,
        "available": True,
    }


def _latest_release(data_dir: Path) -> dict[str, object]:
    global _memory_release, _memory_release_expires_at

    now = time.time()
    with _cache_lock:
        if _memory_release is not None and _memory_release_expires_at > now:
            return dict(_memory_release)

        disk_value = _read_disk_cache(data_dir, "update_status_cache.json", "tag")
        if disk_value:
            _memory_release = dict(disk_value)
            _memory_release_expires_at = now + CACHE_TTL_SECONDS
            return dict(disk_value)

        try:
            value = _fetch_latest_release()
            ttl = CACHE_TTL_SECONDS
        except Exception:
            value = {"tag": "", "url": RELEASES_URL, "available": False}
            ttl = FAILURE_CACHE_SECONDS

        _memory_release = dict(value)
        _memory_release_expires_at = now + ttl
        if value.get("available"):
            try:
                _write_disk_cache(data_dir, "update_status_cache.json", value, ttl)
            except OSError:
                pass
        return dict(value)


def _latest_nightly(data_dir: Path) -> dict[str, object]:
    global _memory_nightly, _memory_nightly_expires_at

    now = time.time()
    with _cache_lock:
        if _memory_nightly is not None and _memory_nightly_expires_at > now:
            return dict(_memory_nightly)

        disk_value = _read_disk_cache(data_dir, "nightly_update_status_cache.json", "sha")
        if disk_value:
            _memory_nightly = dict(disk_value)
            _memory_nightly_expires_at = now + CACHE_TTL_SECONDS
            return dict(disk_value)

        try:
            value = _fetch_latest_nightly_run()
            ttl = CACHE_TTL_SECONDS
        except Exception:
            value = {"sha": "", "url": NIGHTLY_WORKFLOW_URL, "available": False}
            ttl = FAILURE_CACHE_SECONDS

        _memory_nightly = dict(value)
        _memory_nightly_expires_at = now + ttl
        if value.get("available"):
            try:
                _write_disk_cache(data_dir, "nightly_update_status_cache.json", value, ttl)
            except OSError:
                pass
        return dict(value)


def update_status(settings, current_version: str) -> dict[str, object]:
    channel = _channel(current_version)
    current_display = _display_version(current_version, channel)
    status = {
        "channel": channel,
        "current_version": current_display,
        "latest_version": "",
        "badge_label": "Development" if channel == "development" else "Nightly" if channel == "nightly" else "Unknown",
        "badge_class": "development" if channel == "development" else "nightly" if channel == "nightly" else "unknown",
        "header_label": "Update available",
        "update_available": False,
        "release_url": RELEASES_URL,
    }

    if channel == "development":
        return status

    if channel == "nightly":
        nightly = _latest_nightly(settings.data_dir)
        status["release_url"] = str(nightly.get("url") or NIGHTLY_WORKFLOW_URL)
        current_sha = _nightly_sha(current_version)
        latest_sha = str(nightly.get("sha") or "").strip().lower()
        if not nightly.get("available") or not current_sha or not latest_sha:
            return status
        status["latest_version"] = f"sha-{latest_sha[:7]}"
        status["release_url"] = f"{REPOSITORY_URL}/commit/{latest_sha}"
        if current_sha[:7] != latest_sha[:7]:
            status["badge_label"] = "Nightly Update Available"
            status["badge_class"] = "outdated"
            status["header_label"] = "New nightly build"
            status["release_url"] = (
                f"{REPOSITORY_URL}/compare/{current_sha[:7]}...{latest_sha[:7]}"
            )
            status["update_available"] = True
        return status

    release = _latest_release(settings.data_dir)
    status["release_url"] = str(release.get("url") or RELEASES_URL)
    latest = _stable_version(str(release.get("tag") or ""))
    current = _stable_version(current_version)
    if not release.get("available") or latest is None or current is None:
        return status

    latest_display = f"v{latest[0]}.{latest[1]}.{latest[2]}"
    status["latest_version"] = latest_display
    if latest > current:
        status["badge_label"] = "Out of Date"
        status["badge_class"] = "outdated"
        status["update_available"] = True
    else:
        status["badge_label"] = "Up to Date"
        status["badge_class"] = "current"
    return status


def reset_update_cache() -> None:
    global _memory_release, _memory_release_expires_at, _memory_nightly, _memory_nightly_expires_at
    with _cache_lock:
        _memory_release = None
        _memory_release_expires_at = 0.0
        _memory_nightly = None
        _memory_nightly_expires_at = 0.0
