from datetime import timedelta

from sqlalchemy.orm import Session

from absulli.database.models import ListeningHistory
from absulli.web.queries import (
    clamp_days,
    clean_stat_metric,
    compact_number,
    fmt_seconds,
    media_history_date,
)
from absulli.core.time import utcnow


def graph_metric_label(metric: str) -> str:
    return "Hours" if metric == "duration" else "Plays"


def clean_graph_user(value: str | None) -> str:
    value = (value or "all").strip()
    return value or "all"


def graph_row_value(row: ListeningHistory, metric: str) -> float:
    if metric == "duration":
        return float(row.duration_seconds or 0) / 3600
    return 1.0


def graph_value_formatter(metric: str):
    if metric == "duration":
        return lambda value: f"{float(value or 0):.1f}h"
    return lambda value: compact_number(value)


def history_rows_for_graphs(db: Session, days: int, user_key: str) -> list[ListeningHistory]:
    days = clamp_days(days)
    since = utcnow() - timedelta(days=days - 1)
    query = db.query(ListeningHistory).filter(media_history_date() >= since)
    if user_key != "all":
        query = query.filter(ListeningHistory.username == user_key)
    return query.order_by(media_history_date().asc()).all()


def make_line_chart(title: str, subtitle: str, labels: list[str], series: list[dict]) -> dict:
    return {"type": "line", "title": title, "subtitle": subtitle, "labels": labels, "series": series}


def make_bar_chart(title: str, subtitle: str, labels: list[str], values: list[float], metric: str) -> dict:
    return {"type": "bar", "title": title, "subtitle": subtitle, "labels": labels, "values": values, "unit": graph_metric_label(metric)}


def build_graphs(db: Session, metric: str = "count", days: int = 30, user_key: str = "all") -> dict:
    metric = clean_stat_metric(metric)
    days = clamp_days(days)
    user_key = clean_graph_user(user_key)
    rows = history_rows_for_graphs(db, days, user_key)
    value_format = graph_value_formatter(metric)
    now_date = utcnow().date()
    start_date = now_date - timedelta(days=days - 1)
    date_keys = [start_date + timedelta(days=i) for i in range(days)]
    labels = [day.strftime("%b %-d") if hasattr(day, "strftime") else str(day) for day in date_keys]

    def row_date(row: ListeningHistory):
        dt = row.started_at or row.updated_at or row.imported_at
        return dt.date() if dt else None

    def line_by(field_getter, fallback="Unknown", top_n=4):
        totals: dict[str, float] = {}
        bucket: dict[str, dict] = {}
        for row in rows:
            day = row_date(row)
            if day is None or day < start_date or day > now_date:
                continue
            name = (field_getter(row) or fallback or "Unknown").strip() or fallback
            value = graph_row_value(row, metric)
            totals[name] = totals.get(name, 0) + value
            bucket.setdefault(name, {}).setdefault(day, 0)
            bucket[name][day] += value
        ordered = [name for name, _ in sorted(totals.items(), key=lambda item: item[1], reverse=True)[:top_n]]
        series = []
        for name in ordered:
            series.append({"name": name, "data": [round(bucket.get(name, {}).get(day, 0), 2) for day in date_keys]})
        return series

    def bar_by(field_getter, fallback="Unknown", limit=12):
        totals: dict[str, float] = {}
        for row in rows:
            name = (field_getter(row) or fallback or "Unknown").strip() or fallback
            totals[name] = totals.get(name, 0) + graph_row_value(row, metric)
        ordered = sorted(totals.items(), key=lambda item: item[1], reverse=True)[:limit]
        return [name for name, _ in ordered], [round(value, 2) for _, value in ordered]

    hour_values = [0.0 for _ in range(24)]
    weekday_values = [0.0 for _ in range(7)]
    for row in rows:
        dt = row.started_at or row.updated_at or row.imported_at
        if not dt:
            continue
        value = graph_row_value(row, metric)
        hour_values[dt.hour] += value
        weekday_values[dt.weekday()] += value
    hour_values = [round(value, 2) for value in hour_values]
    weekday_values = [round(value, 2) for value in weekday_values]

    top_user_labels, top_user_values = bar_by(lambda row: row.username, "Unknown", 12)
    top_title_labels, top_title_values = bar_by(lambda row: row.title, "Unknown title", 12)
    top_library_labels, top_library_values = bar_by(lambda row: row.library_name or row.media_type, "Unknown", 10)
    top_platform_labels, top_platform_values = bar_by(lambda row: row.client or row.device or row.model, "Unknown", 10)

    subtitle = f"Last {days} days · {graph_metric_label(metric).lower()}"
    charts = [
        make_line_chart(
            "Daily listening by media type",
            subtitle,
            labels,
            line_by(lambda row: row.media_type or "unknown", "unknown", 4),
        ),
        make_line_chart(
            "Daily listening by library",
            subtitle,
            labels,
            line_by(lambda row: row.library_name or row.media_type or "Unknown", "Unknown", 5),
        ),
        make_bar_chart("Listening by hour of day", subtitle, [f"{hour:02d}:00" for hour in range(24)], hour_values, metric),
        make_bar_chart("Listening by weekday", subtitle, ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"], weekday_values, metric),
        make_bar_chart("Top users", subtitle, top_user_labels, top_user_values, metric),
        make_bar_chart("Top titles", subtitle, top_title_labels, top_title_values, metric),
        make_bar_chart("Top libraries", subtitle, top_library_labels, top_library_values, metric),
        make_bar_chart("Top platforms", subtitle, top_platform_labels, top_platform_values, metric),
    ]
    summary = {
        "sessions": len(rows),
        "duration": fmt_seconds(sum(row.duration_seconds or 0 for row in rows)),
        "users": len({row.username for row in rows if row.username}),
        "titles": len({row.abs_item_id or row.title for row in rows if row.abs_item_id or row.title}),
    }
    return {"charts": charts, "summary": summary, "metric_label": graph_metric_label(metric), "value_format": metric}
