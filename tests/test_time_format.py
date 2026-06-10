from datetime import datetime

from absulli.core.time import format_local_date, format_local_datetime, format_local_time


def test_format_local_datetime_uses_tz_env(monkeypatch):
    monkeypatch.setenv("TZ", "America/Phoenix")

    value = datetime(2026, 6, 10, 20, 52, 36)

    assert format_local_datetime(value) == "2026-06-10 1:52 PM"
    assert format_local_date(value) == "2026-06-10"
    assert format_local_time(value) == "1:52 PM"


def test_format_local_datetime_falls_back_to_utc_for_bad_tz(monkeypatch):
    monkeypatch.setenv("TZ", "Not/AZone")

    assert format_local_datetime(datetime(2026, 6, 10, 20, 52, 36)) == "2026-06-10 8:52 PM"
