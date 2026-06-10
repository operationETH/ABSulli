from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import absulli.web.routes as web_routes
from absulli.database.models import Base, Library, MediaItem
from absulli.database.session import get_db


class FakeCoverClient:
    calls: list[tuple[str, dict]] = []

    def __init__(self, settings):
        self.settings = settings

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get_item_cover(self, **kwargs):
        self.calls.append(("item", kwargs))
        return b"item-image", "image/webp", "public, max-age=99"

    async def get_author_image(self, **kwargs):
        self.calls.append(("author", kwargs))
        return b"author-image", "image/jpeg", "public, max-age=88"

    async def find_author_in_libraries(self, author_name, library_ids):
        self.calls.append(("find_author", {"author_name": author_name, "library_ids": library_ids}))
        return {"id": "author-1"}


def make_client(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = session_factory()

    app = FastAPI()

    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    app.include_router(web_routes.router)
    monkeypatch.setattr(web_routes, "AudiobookshelfClient", FakeCoverClient)
    FakeCoverClient.calls = []
    return TestClient(app), db


def test_item_cover_rejects_invalid_id_before_proxying(monkeypatch):
    client, db = make_client(monkeypatch)
    try:
        response = client.get("/covers/items/../../etc/passwd")
        assert response.status_code == 404

        response = client.get("/covers/items/bad$id")
        assert response.status_code == 400
        assert FakeCoverClient.calls == []
    finally:
        db.close()


def test_item_cover_requires_known_media_item_before_proxying(monkeypatch):
    client, db = make_client(monkeypatch)
    try:
        response = client.get("/covers/items/missing-item")

        assert response.status_code == 404
        assert FakeCoverClient.calls == []
    finally:
        db.close()


def test_item_cover_proxies_existing_item_with_sanitized_options(monkeypatch):
    client, db = make_client(monkeypatch)
    try:
        db.add(MediaItem(abs_item_id="item-1", title="Known Book"))
        db.commit()

        response = client.get("/covers/items/item-1?width=99999&height=-5&fmt=gif")

        assert response.status_code == 200
        assert response.content == b"item-image"
        assert response.headers["content-type"] == "image/webp"
        assert response.headers["cache-control"] == "public, max-age=99"
        assert FakeCoverClient.calls == [
            (
                "item",
                {
                    "item_id": "item-1",
                    "width": 1200,
                    "height": 32,
                    "image_format": "webp",
                },
            )
        ]
    finally:
        db.close()


def test_author_cover_rejects_unknown_author_before_proxying(monkeypatch):
    client, db = make_client(monkeypatch)
    try:
        response = client.get("/covers/authors/author-1")

        assert response.status_code == 404
        assert FakeCoverClient.calls == []
    finally:
        db.close()


def test_author_cover_proxies_known_author_with_sanitized_options(monkeypatch):
    client, db = make_client(monkeypatch)
    try:
        db.add(MediaItem(abs_item_id="item-1", title="Known Book", author_id="author-1", author="Known Author"))
        db.commit()

        response = client.get("/covers/authors/author-1?width=0&height=800&fmt=jpg")

        assert response.status_code == 200
        assert response.content == b"author-image"
        assert response.headers["content-type"] == "image/jpeg"
        assert response.headers["cache-control"] == "public, max-age=88"
        assert FakeCoverClient.calls == [
            (
                "author",
                {
                    "author_id": "author-1",
                    "width": 32,
                    "height": 800,
                    "image_format": "jpg",
                },
            )
        ]
    finally:
        db.close()


def test_author_cover_by_name_requires_non_blank_name(monkeypatch):
    client, db = make_client(monkeypatch)
    try:
        response = client.get("/covers/authors/by-name/%20%20%20")

        assert response.status_code == 404
        assert FakeCoverClient.calls == []
    finally:
        db.close()


def test_author_cover_by_name_finds_author_from_local_libraries(monkeypatch):
    client, db = make_client(monkeypatch)
    try:
        db.add(Library(abs_library_id="lib-1", name="Audiobooks"))
        db.commit()

        response = client.get("/covers/authors/by-name/Known%20Author?width=600&fmt=png")

        assert response.status_code == 200
        assert response.content == b"author-image"
        assert FakeCoverClient.calls == [
            ("find_author", {"author_name": "Known Author", "library_ids": ["lib-1"]}),
            (
                "author",
                {
                    "author_id": "author-1",
                    "width": 600,
                    "height": None,
                    "image_format": "png",
                },
            ),
        ]
    finally:
        db.close()
