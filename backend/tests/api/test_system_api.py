from __future__ import annotations

from fastapi.testclient import TestClient

from app.container import Container


def test_health_has_no_dependencies(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_ready_checks_database_and_storage(client: TestClient) -> None:
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "checks": {"database": "ok", "storage": "ok"}}


def test_ready_reports_broken_storage(client: TestClient, container: Container, monkeypatch: object) -> None:
    from app.storage import StorageError

    def broken() -> None:
        raise StorageError("down")

    monkeypatch.setattr(container.storage, "check", broken)  # type: ignore[attr-defined]
    response = client.get("/ready")
    assert response.status_code == 503 and response.json()["checks"]["storage"] == "unavailable"


def test_capabilities(client: TestClient) -> None:
    body = client.get("/api/capabilities").json()
    assert body["extensions"] == [".kml", ".zip"] and body["formats"] == ["KML", "SHAPEFILE"]
    assert body["measurements"]["Polygon"] == "area_m2" and body["measurements"]["Point"] == "none"


def test_openapi_documents_the_assignment_endpoints(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert {"/api/files/", "/api/files/{file_id}/", "/api/files/{file_id}/measurements/"} <= set(paths)
    assert "post" in paths["/api/files/"]


def test_cors_allows_configured_origin_only(client: TestClient) -> None:
    allowed = client.options(
        "/api/files/", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"}
    )
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
    denied = client.options(
        "/api/files/", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"}
    )
    assert "access-control-allow-origin" not in denied.headers


def test_every_response_carries_request_id_and_security_headers(client: TestClient) -> None:
    response = client.get("/health", headers={"X-Request-ID": "trace-123"})
    assert response.headers["x-request-id"] == "trace-123"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_security_headers_also_on_cors_preflight(client: TestClient) -> None:
    preflight = client.options(
        "/api/files/", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"}
    )
    assert preflight.headers["x-content-type-options"] == "nosniff"
    assert "x-request-id" in preflight.headers
