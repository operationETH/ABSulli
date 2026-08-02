from __future__ import annotations

from datetime import datetime, timezone
import os
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def utcnow_iso() -> str:
    return utcnow().isoformat() + "Z"


def unix_seconds(value: datetime | None) -> float:
    if value is None:
        return 0.0
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()


def local_timezone() -> ZoneInfo:
    tz_name = os.environ.get("TZ", "").strip() or "UTC"
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def local_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(local_timezone())


def _strip_leading_zero_hour(value: str) -> str:
    if value.startswith("0"):
        return value[1:]
    return value.replace(" 0", " ", 1)


def format_local_datetime(value: datetime | None) -> str:
    local_value = local_datetime(value)
    if local_value is None:
        return ""
    return _strip_leading_zero_hour(local_value.strftime("%Y-%m-%d %I:%M %p"))


def format_local_date(value: datetime | None) -> str:
    local_value = local_datetime(value)
    if local_value is None:
        return ""
    return local_value.strftime("%Y-%m-%d")


def format_local_month_day(value: datetime | None) -> str:
    local_value = local_datetime(value)
    if local_value is None:
        return ""
    return local_value.strftime("%m/%d")


def format_local_time(value: datetime | None) -> str:
    local_value = local_datetime(value)
    if local_value is None:
        return ""
    return _strip_leading_zero_hour(local_value.strftime("%I:%M %p"))
