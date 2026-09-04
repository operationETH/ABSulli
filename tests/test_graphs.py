from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from absulli.core.time import utcnow
from absulli.database.models import Base, ListeningHistory
import absulli.web.graphs as graphs
from absulli.web.graphs import build_graphs, clean_graph_user, graph_row_value


def make_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal()


def history_row(
    *,
    session_id: str,
    username: str = "Kenny",
    title: str = "Book One",
    item_id: str = "book-1",
    media_type: str = "book",
    library_name: str = "Audiobooks",
    client: str = "Web",
    duration_seconds: float = 60,
    started_delta: timedelta = timedelta(hours=1),
) -> ListeningHistory:
    started_at = utcnow() - started_delta
    return ListeningHistory(
        abs_session_id=session_id,
        abs_user_id=f"user-{username}",
        username=username,
        abs_item_id=item_id,
        title=title,
        media_type=media_type,
        library_name=library_name,
        started_at=started_at,
        updated_at=started_at,
        imported_at=started_at,
        duration_seconds=duration_seconds,
        client=client,
    )


def chart_by_title(graph_data: dict, title: str) -> dict:
    return next(chart for chart in graph_data["charts"] if chart["title"] == title)


def test_clean_graph_user_defaults_blank_values_to_all():
    assert clean_graph_user(None) == "all"
    assert clean_graph_user("") == "all"
    assert clean_graph_user("   ") == "all"
    assert clean_graph_user("Alice") == "Alice"


def test_graph_row_value_uses_count_or_duration_hours():
    row = history_row(session_id="s1", duration_seconds=7200)

    assert graph_row_value(row, "count") == 1.0
    assert graph_row_value(row, "duration") == 2.0


def test_build_graphs_returns_summary_and_top_bars_for_count_metric():
    db = make_db()
    db.add_all(
        [
            history_row(session_id="s1", username="Alice", title="Book One", client="Web"),
            history_row(session_id="s2", username="Alice", title="Book One", client="Web"),
            history_row(session_id="s3", username="Bob", title="Book Two", item_id="book-2", client="Mobile"),
            history_row(session_id="old", username="Old", title="Old Book", started_delta=timedelta(days=45)),
        ]
    )
    db.commit()

    graph_data = build_graphs(db, metric="count", days=30, user_key="all")

    assert graph_data["metric_label"] == "Plays"
    assert graph_data["value_format"] == "count"
    assert graph_data["summary"] == {
        "sessions": 3,
        "duration": "3m",
        "users": 2,
        "titles": 2,
    }

    top_users = chart_by_title(graph_data, "Top users")
    assert top_users["labels"][:2] == ["Alice", "Bob"]
    assert top_users["values"][:2] == [2.0, 1.0]
    assert top_users["unit"] == "Plays"

    top_platforms = chart_by_title(graph_data, "Top platforms")
    assert top_platforms["labels"][:2] == ["Web", "Mobile"]
    assert top_platforms["values"][:2] == [2.0, 1.0]
    db.close()


def test_build_graphs_filters_by_user_and_formats_duration_metric():
    db = make_db()
    db.add_all(
        [
            history_row(session_id="s1", username="Alice", title="Book One", duration_seconds=3600),
            history_row(session_id="s2", username="Bob", title="Book Two", item_id="book-2", duration_seconds=7200),
        ]
    )
    db.commit()

    graph_data = build_graphs(db, metric="duration", days=30, user_key="Alice")

    assert graph_data["metric_label"] == "Hours"
    assert graph_data["value_format"] == "duration"
    assert graph_data["summary"] == {
        "sessions": 1,
        "duration": "1h 0m",
        "users": 1,
        "titles": 1,
    }

    top_titles = chart_by_title(graph_data, "Top titles")
    assert top_titles["labels"] == ["Book One"]
    assert top_titles["values"] == [1.0]
    assert top_titles["unit"] == "Hours"
    db.close()


def test_graph_time_buckets_use_configured_timezone(monkeypatch):
    monkeypatch.setenv("TZ", "America/Phoenix")
    monkeypatch.setattr(graphs, "utcnow", lambda: datetime(2026, 8, 29, 2, 0))
    started_at = datetime(2026, 8, 29, 1, 30)
    db = make_db()
    db.add(
        ListeningHistory(
            abs_session_id="timezone-session",
            abs_user_id="user-kenny",
            username="Kenny",
            abs_item_id="book-1",
            title="Book One",
            media_type="book",
            library_name="Audiobooks",
            started_at=started_at,
            updated_at=started_at,
            imported_at=started_at,
            duration_seconds=60,
            client="Web",
        )
    )
    db.commit()

    graph_data = graphs.build_graphs(db, metric="count", days=30, user_key="all")
    heatmap = chart_by_title(graph_data, "Listening activity")
    cells = {
        cell["date"]: cell["value"]
        for column in heatmap["columns"]
        for cell in column["days"]
        if cell
    }

    assert cells["2026-08-28"] == 1.0
    assert "2026-08-29" not in cells

    daily = chart_by_title(graph_data, "Daily listening by media type")
    assert daily["labels"][-1] == "Aug 28"
    assert daily["series"][0]["data"][-1] == 1.0

    hourly = chart_by_title(graph_data, "Listening by hour of day")
    assert hourly["values"][18] == 1.0
    assert hourly["values"][1] == 0.0

    weekday = chart_by_title(graph_data, "Listening by weekday")
    assert weekday["values"][4] == 1.0
    assert weekday["values"][5] == 0.0
    db.close()
