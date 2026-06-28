from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import logging
import os
from pathlib import Path
import time

log = logging.getLogger(__name__)

DEFAULT_BROWSER_MAX_AGE_SECONDS = 86_400
DEFAULT_BROWSER_STALE_SECONDS = 86_400
DEFAULT_DISK_TTL_SECONDS = 7 * 86_400
DEFAULT_NEGATIVE_BROWSER_MAX_AGE_SECONDS = 300
DEFAULT_NEGATIVE_DISK_TTL_SECONDS = 6 * 3_600

COVER_CACHE_CONTROL = (
    f"public, max-age={DEFAULT_BROWSER_MAX_AGE_SECONDS}, "
    f"stale-while-revalidate={DEFAULT_BROWSER_STALE_SECONDS}"
)
NEGATIVE_COVER_CACHE_CONTROL = f"public, max-age={DEFAULT_NEGATIVE_BROWSER_MAX_AGE_SECONDS}"


@dataclass(frozen=True)
class CoverCacheEntry:
    content: bytes
    content_type: str
    cache_control: str = COVER_CACHE_CONTROL
    status_code: int = 200

    @property
    def found(self) -> bool:
        return self.status_code < 400


def cover_cache_key(*parts: object) -> str:
    normalized = "\x1f".join("" if part is None else str(part) for part in parts)
    return sha256(normalized.encode("utf-8")).hexdigest()


def cover_cache_dir(data_dir: Path) -> Path:
    return Path(data_dir) / "cache" / "covers"


def _paths(cache_dir: Path, key: str) -> tuple[Path, Path]:
    bucket = key[:2]
    directory = cache_dir / bucket
    return directory / f"{key}.bin", directory / f"{key}.json"


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        log.debug("Unable to remove cover cache file %s: %s", path, exc)


def _ttl_for_status(status_code: int) -> int:
    return DEFAULT_NEGATIVE_DISK_TTL_SECONDS if status_code == 404 else DEFAULT_DISK_TTL_SECONDS


def _remove_entry(body_path: Path, meta_path: Path) -> None:
    _safe_unlink(body_path)
    _safe_unlink(meta_path)


def read_cover_cache(data_dir: Path, key: str, ttl_seconds: int | None = None) -> CoverCacheEntry | None:
    cache_dir = cover_cache_dir(data_dir)
    body_path, meta_path = _paths(cache_dir, key)
    try:
        if not body_path.exists() or not meta_path.exists():
            return None

        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        status_code = int(metadata.get("status_code") or 200)
        effective_ttl = _ttl_for_status(status_code) if ttl_seconds is None else ttl_seconds
        if effective_ttl > 0 and time.time() - body_path.stat().st_mtime > effective_ttl:
            _remove_entry(body_path, meta_path)
            return None

        content_type = str(metadata.get("content_type") or "image/webp")
        cache_control = str(metadata.get("cache_control") or COVER_CACHE_CONTROL)
        content = body_path.read_bytes()
        if not content and status_code < 400:
            _remove_entry(body_path, meta_path)
            return None
        return CoverCacheEntry(
            content=content,
            content_type=content_type,
            cache_control=cache_control,
            status_code=status_code,
        )
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        log.debug("Unable to read cover cache entry %s: %s", key, exc)
        _remove_entry(body_path, meta_path)
        return None


def write_cover_cache(
    data_dir: Path,
    key: str,
    content: bytes,
    content_type: str,
    cache_control: str = COVER_CACHE_CONTROL,
    status_code: int = 200,
) -> None:
    if status_code < 400 and not content:
        return

    cache_dir = cover_cache_dir(data_dir)
    body_path, meta_path = _paths(cache_dir, key)
    metadata = {
        "content_type": content_type or "image/webp",
        "cache_control": cache_control or COVER_CACHE_CONTROL,
        "created_at": int(time.time()),
        "status_code": int(status_code),
    }

    tmp_body = body_path.with_suffix(".bin.tmp")
    tmp_meta = meta_path.with_suffix(".json.tmp")
    try:
        body_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_body.write_bytes(content)
        tmp_meta.write_text(json.dumps(metadata, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp_body, body_path)
        os.replace(tmp_meta, meta_path)
    except OSError as exc:
        _safe_unlink(tmp_body)
        _safe_unlink(tmp_meta)
        log.debug("Unable to write cover cache entry %s: %s", key, exc)


def write_negative_cover_cache(
    data_dir: Path,
    key: str,
    cache_control: str = NEGATIVE_COVER_CACHE_CONTROL,
) -> None:
    write_cover_cache(
        data_dir=data_dir,
        key=key,
        content=b"",
        content_type="application/octet-stream",
        cache_control=cache_control,
        status_code=404,
    )


def prune_cover_cache(data_dir: Path) -> int:
    cache_dir = cover_cache_dir(data_dir)
    removed = 0
    if not cache_dir.exists():
        return removed

    for tmp_path in cache_dir.rglob("*.tmp"):
        _safe_unlink(tmp_path)
        removed += 1

    now = time.time()
    for meta_path in cache_dir.rglob("*.json"):
        key = meta_path.stem
        body_path, expected_meta_path = _paths(cache_dir, key)
        if expected_meta_path != meta_path:
            continue
        try:
            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
            status_code = int(metadata.get("status_code") or 200)
            ttl_seconds = _ttl_for_status(status_code)
            if not body_path.exists() or (ttl_seconds > 0 and now - body_path.stat().st_mtime > ttl_seconds):
                _remove_entry(body_path, meta_path)
                removed += 1
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            log.debug("Unable to inspect cover cache entry %s: %s", key, exc)
            _remove_entry(body_path, meta_path)
            removed += 1

    return removed


def cover_response_headers(cache_control: str = COVER_CACHE_CONTROL, hit: bool | None = None) -> dict[str, str]:
    headers = {"Cache-Control": cache_control or COVER_CACHE_CONTROL}
    if hit is not None:
        headers["X-ABSulli-Cover-Cache"] = "HIT" if hit else "MISS"
    return headers
