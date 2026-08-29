import pytest


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("ABSULLI_AUTH_ENABLED", "false")

    import absulli.core.config as cfg
    cfg.get_settings.cache_clear()

    from fastapi.testclient import TestClient
    from absulli.main import app
    try:
        yield TestClient(app)
    finally:
        cfg.get_settings.cache_clear()


V1_PATHS = ["/api/v1/status", "/api/v1/activity", "/api/v1/history", "/api/v1/users", "/api/v1/libraries"]


def test_v1_endpoints_available(client):
    for path in V1_PATHS:
        assert client.get(path).status_code == 200


def test_v1_has_no_deprecation_header(client):
    for path in V1_PATHS:
        assert "Deprecation" not in client.get(path).headers


def test_legacy_aliases_are_deprecated_and_point_to_v1(client):
    for path in ["/status", "/activity", "/history", "/users", "/libraries"]:
        resp = client.get(f"/api{path}")
        assert resp.status_code == 200
        assert resp.headers.get("Deprecation") == "true"
        assert f"/api/v1{path}" in resp.headers.get("Link", "")


def test_legacy_payload_matches_v1(client):
    for path in ["/activity", "/history", "/users", "/libraries"]:
        assert client.get(f"/api/v1{path}").json() == client.get(f"/api{path}").json()
    v1_status = client.get("/api/v1/status").json()
    legacy_status = client.get("/api/status").json()
    assert {k: v for k, v in v1_status.items() if k != "time"} == {
        k: v for k, v in legacy_status.items() if k != "time"
    }


def test_unversioned_paths_are_not_deprecated(client):
    health = client.get("/healthz")
    assert health.status_code == 200
    assert "Deprecation" not in health.headers


def test_openapi_documents_v1_and_hides_legacy(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/v1/status" in paths
    assert "/api/status" not in paths


def test_history_limit_validation(client):
    for base in ["/api/v1/history", "/api/history"]:
        assert client.get(base).status_code == 200
        assert client.get(f"{base}?limit=1").status_code == 200
        assert client.get(f"{base}?limit=500").status_code == 200
        for bad in ["0", "-1", "501", "1000", "abc"]:
            assert client.get(f"{base}?limit={bad}").status_code == 422, (base, bad)
