import asyncio
import inspect
from datetime import datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

import absulli.notifiers.agents as notifier_agents
import absulli.web.routes as web_routes
from absulli.database.models import Base, ListeningHistory
from absulli.database.session import get_db
from absulli.web.api import router as api_router


def make_memory_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return session_factory()


def test_api_history_uses_abs_session_id_for_session_key():
    db = make_memory_session()
    db.add(
        ListeningHistory(
            abs_session_id="abs-session-123",
            abs_user_id="user-1",
            username="kenny",
            abs_item_id="item-1",
            title="Test Book",
            imported_at=datetime(2026, 1, 1, 12, 0, 0),
        )
    )
    db.commit()

    app = FastAPI()

    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    app.include_router(api_router)

    response = TestClient(app).get("/api/history")

    assert response.status_code == 200
    assert response.json()[0]["session_key"] == "abs-session-123"

    db.close()


def test_gotify_agent_uses_header_token_not_query_param(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, **kwargs):
            captured["url"] = url
            captured["kwargs"] = kwargs
            return FakeResponse()

    monkeypatch.setattr(notifier_agents.httpx, "AsyncClient", FakeAsyncClient)

    agent = notifier_agents.GotifyAgent("https://gotify.example", "secret-token")
    asyncio.run(agent.send("Title", "Message"))

    assert captured["url"] == "https://gotify.example/message"
    assert captured["kwargs"]["headers"] == {"X-Gotify-Key": "secret-token"}
    assert "params" not in captured["kwargs"]


def test_search_query_is_capped_and_whitespace_normalized(monkeypatch):
    db = make_memory_session()
    captured = {}

    def fake_template_response(template_name, context):
        captured["template_name"] = template_name
        captured["context"] = context
        return context

    monkeypatch.setattr(web_routes.templates, "TemplateResponse", fake_template_response)

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/search",
            "headers": [],
            "query_string": b"",
        }
    )
    long_query = "   " + ("a" * 250) + "     extra words"

    web_routes.search(request, q=long_query, db=db)

    search_query = captured["context"]["search_query"]
    assert captured["template_name"] == "search.html"
    assert len(search_query) <= 200
    assert "  " not in search_query
    assert search_query == "a" * 197

    db.close()


def test_search_empty_query_does_not_return_all_rows(monkeypatch):
    db = make_memory_session()
    db.add(
        ListeningHistory(
            abs_session_id="abs-session-456",
            abs_user_id="user-1",
            username="kenny",
            abs_item_id="item-1",
            title="Should Not Leak Into Empty Search",
        )
    )
    db.commit()
    captured = {}

    def fake_template_response(template_name, context):
        captured["context"] = context
        return context

    monkeypatch.setattr(web_routes.templates, "TemplateResponse", fake_template_response)

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/search",
            "headers": [],
            "query_string": b"",
        }
    )

    web_routes.search(request, q="   ", db=db)

    assert captured["context"]["search_query"] == ""
    assert captured["context"]["media_items"] == []
    assert captured["context"]["users"] == []
    assert captured["context"]["libraries"] == []
    assert captured["context"]["authors"] == []
    assert captured["context"]["history_rows"] == []

    db.close()


def test_graph_date_label_helper_was_removed():
    assert not hasattr(web_routes, "graph_date_label")
    assert "def graph_date_label" not in inspect.getsource(web_routes)
