from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import tempfile

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import absulli.web.routes as web_routes
from absulli.database.models import Base, Library, MediaItem
from absulli.database.session import get_db
from absulli.core.cover_cache import COVER_CACHE_CONTROL


class FakeCoverClient:
    calls: list[tuple[str, dict]] = []
    missing_items: set[str] = set()
    missing_authors: set[str] = set()
    find_author_payload = {"id": "author-1"}

    def __init__(self, settings):
        self.settings = settings

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get_item_cover(self, **kwargs):
        self.calls.append(("item", kwargs))
        if kwargs.get("item_id") in self.missing_items:
            request = httpx.Request("GET", "http://abs.example/covers/items/missing")
            response = httpx.Response(404, request=request)
            raise httpx.HTTPStatusError("not found", request=request, response=response)
        return b"item-image", "image/webp", "public, max-age=99"

    async def get_author_image(self, **kwargs):
        self.calls.append(("author", kwargs))
        if kwargs.get("author_id") in self.missing_authors:
            request = httpx.Request("GET", "http://abs.example/covers/authors/missing")
            response = httpx.Response(404, request=request)
            raise httpx.HTTPStatusError("not found", request=request, response=response)
        return b"author-image", "image/jpeg", "public, max-age=88"

    async def get_author(self, author_id):
        self.calls.append(("get_author", {"author_id": author_id}))
        return {"id": author_id, "description": "Known author"}

    async def find_author_in_libraries(self, author_name, library_ids):
        self.calls.append(("find_author", {"author_name": author_name, "library_ids": library_ids}))
        return self.find_author_payload


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
    data_dir = Path(tempfile.mkdtemp(prefix="absulli-cover-test-"))
    monkeypatch.setattr(web_routes, "get_settings", lambda: SimpleNamespace(data_dir=data_dir))
    FakeCoverClient.calls = []
    FakeCoverClient.missing_items = set()
    FakeCoverClient.missing_authors = set()
    FakeCoverClient.find_author_payload = {"id": "author-1"}
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
        assert response.headers["cache-control"] == COVER_CACHE_CONTROL
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
        assert response.headers["cache-control"] == COVER_CACHE_CONTROL
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


def test_item_cover_uses_disk_cache_on_second_request(monkeypatch):
    client, db = make_client(monkeypatch)
    try:
        db.add(MediaItem(abs_item_id="item-1", title="Known Book"))
        db.commit()

        first = client.get("/covers/items/item-1?width=260")
        second = client.get("/covers/items/item-1?width=260")

        assert first.status_code == 200
        assert second.status_code == 200
        assert second.headers["x-absulli-cover-cache"] == "HIT"
        assert FakeCoverClient.calls == [
            (
                "item",
                {
                    "item_id": "item-1",
                    "width": 260,
                    "height": None,
                    "image_format": "webp",
                },
            )
        ]
    finally:
        db.close()


def test_item_cover_negative_cache_prevents_repeated_abs_404(monkeypatch):
    client, db = make_client(monkeypatch)
    try:
        db.add(MediaItem(abs_item_id="item-missing-cover", title="Known Book Without Art"))
        db.commit()
        FakeCoverClient.missing_items = {"item-missing-cover"}

        first = client.get("/covers/items/item-missing-cover?width=260")
        second = client.get("/covers/items/item-missing-cover?width=260")

        assert first.status_code == 404
        assert second.status_code == 404
        assert second.headers["x-absulli-cover-cache"] == "HIT"
        assert FakeCoverClient.calls == [
            (
                "item",
                {
                    "item_id": "item-missing-cover",
                    "width": 260,
                    "height": None,
                    "image_format": "webp",
                },
            )
        ]
    finally:
        db.close()


def test_author_cover_by_name_negative_cache_prevents_repeated_lookup(monkeypatch):
    client, db = make_client(monkeypatch)
    try:
        db.add(Library(abs_library_id="lib-1", name="Audiobooks"))
        db.commit()
        FakeCoverClient.find_author_payload = {}

        first = client.get("/covers/authors/by-name/Missing%20Author?width=420")
        second = client.get("/covers/authors/by-name/Missing%20Author?width=420")

        assert first.status_code == 404
        assert second.status_code == 404
        assert second.headers["x-absulli-cover-cache"] == "HIT"
        assert FakeCoverClient.calls == [
            ("find_author", {"author_name": "Missing Author", "library_ids": ["lib-1"]})
        ]
    finally:
        db.close()


def test_author_detail_uses_book_cover_as_author_image_fallback(monkeypatch):
    client, db = make_client(monkeypatch)
    try:
        db.add(MediaItem(abs_item_id="book-1", title="Known Book", author="Lee Child", author_id="author-1"))
        db.commit()

        response = client.get("/authors/Lee%20Child")

        assert response.status_code == 200
        assert 'src="/covers/authors/by-name/Lee%20Child?width=420"' in response.text
        assert 'data-fallback-src="/covers/items/book-1?width=420"' in response.text
        assert 'data-fallback-media-bg="/covers/items/book-1?width=1000"' in response.text
    finally:
        db.close()


def test_notification_cover_requires_valid_token(monkeypatch):
    client, db = make_client(monkeypatch)
    try:
        db.add(MediaItem(abs_item_id="item-1", title="Known Book"))
        db.commit()
        monkeypatch.setattr(web_routes, "valid_notification_cover_token", lambda item_id, token: False)

        response = client.get("/notification-covers/items/item-1?token=invalid")

        assert response.status_code == 404
        assert FakeCoverClient.calls == []
    finally:
        db.close()


def test_notification_cover_proxies_existing_item_with_valid_token(monkeypatch):
    client, db = make_client(monkeypatch)
    try:
        db.add(MediaItem(abs_item_id="item-1", title="Known Book"))
        db.commit()
        monkeypatch.setattr(web_routes, "valid_notification_cover_token", lambda item_id, token: item_id == "item-1" and token == "valid")

        response = client.get("/notification-covers/items/item-1?token=valid&width=640")
        assert response.headers["cross-origin-resource-policy"] == "cross-origin"

        assert response.status_code == 200
        assert response.content == b"item-image"
        assert FakeCoverClient.calls == [("item", {"item_id": "item-1", "width": 640, "height": None, "image_format": "webp"})]
    finally:
        db.close()
