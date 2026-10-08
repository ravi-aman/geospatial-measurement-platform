"""POST /api/files/ - the upload contract, including every rejection path."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from shapely.geometry import box

from app.config import Settings
from app.container import Container
from app.db.session import create_session_factory
from app.main import create_app
from app.storage.local import LocalStorage
from tests.builders import (
    kml_document,
    kml_placemark,
    kml_point,
    kml_polygon,
    shapefile_zip,
    square,
    zip_bytes,
    zip_with_symlink,
)

KML = kml_document(kml_placemark("plot", kml_polygon(square(77, 28, 0.01))), kml_placemark("w", kml_point(77, 28)))


def post(client: TestClient, content: bytes, name: str = "survey.kml", **kwargs: object):  # type: ignore[no-untyped-def]
    return client.post("/api/files/", files={"file": (name, content, "application/octet-stream")}, **kwargs)  # type: ignore[arg-type]


def error_code(response) -> str:  # type: ignore[no-untyped-def]
    return str(response.json()["error"]["code"])


class TestAccepted:
    def test_kml_upload_is_accepted_for_processing(self, client: TestClient) -> None:
        response = post(client, KML)
        body = response.json()
        assert response.status_code == 202
        assert response.headers["location"] == f"/api/files/{body['id']}/"
        assert response.headers["retry-after"] == "1"
        assert body["filename"] == "survey.kml" and body["format"] == "KML" and body["status"] == "PENDING"
        assert body["feature_count"] is None and body["size_bytes"] == len(KML) and len(body["sha256"]) == 64
        assert body["links"]["measurements"] == f"/api/files/{body['id']}/measurements/"

    def test_zipped_shapefile_upload(self, client: TestClient, workdir: Path) -> None:
        archive = shapefile_zip([box(77, 28, 77.01, 28.01)], crs="EPSG:4326", workdir=workdir)
        response = post(client, archive, "parcels.zip")
        assert response.status_code == 202 and response.json()["format"] == "SHAPEFILE"

    def test_path_in_filename_is_stripped(self, client: TestClient) -> None:
        assert post(client, KML, "../../etc/survey.kml").json()["filename"] == "survey.kml"

    def test_trailing_slash_redirect_preserves_post(self, client: TestClient) -> None:
        response = client.post("/api/files", files={"file": ("a.kml", KML)})
        assert response.status_code == 202

    def test_prefer_wait_returns_final_result(self, client: TestClient, container: Container) -> None:
        container.start_embedded_worker()
        response = post(client, KML, headers={"Prefer": "wait=20"})
        body = response.json()
        assert response.status_code == 201, body
        assert body["status"] == "COMPLETED" and body["feature_count"] == 2 and body["crs"] == "EPSG:4326"
        assert response.headers["preference-applied"] == "wait=20"

    def test_prefer_respond_async_does_not_wait(self, client: TestClient) -> None:
        assert post(client, KML, headers={"Prefer": "respond-async, wait=20"}).status_code == 202

    def test_idempotent_replay(self, client: TestClient) -> None:
        first = post(client, KML, headers={"Idempotency-Key": "upload-attempt-1"})
        again = post(client, KML, headers={"Idempotency-Key": "upload-attempt-1"})
        assert again.status_code == 200 and again.headers["idempotent-replayed"] == "true"
        assert again.json()["id"] == first.json()["id"]

    def test_idempotency_key_reuse_with_other_content(self, client: TestClient) -> None:
        post(client, KML, headers={"Idempotency-Key": "upload-attempt-2"})
        other = kml_document(kml_placemark("x", kml_point(1, 1)))
        response = post(client, other, headers={"Idempotency-Key": "upload-attempt-2"})
        assert response.status_code == 422 and error_code(response) == "IDEMPOTENCY_KEY_REUSED"

    def test_crs_override_is_recorded(self, client: TestClient, run_worker: Callable[[], int], workdir: Path) -> None:
        archive = shapefile_zip([box(500000, 3100000, 500100, 3100100)], crs=None, workdir=workdir)
        file_id = post(client, archive, "noprj.zip", data={"crs": "EPSG:32643"}).json()["id"]
        run_worker()
        body = client.get(f"/api/files/{file_id}/").json()
        assert (body["crs"], body["crs_source"], body["status"]) == ("EPSG:32643", "USER_OVERRIDE", "COMPLETED")


class TestRejected:
    @pytest.mark.parametrize("name", ["survey.geojson", "data.kmz", "parcels.shp", "malware.exe", "noext"])
    def test_unsupported_extension(self, client: TestClient, name: str) -> None:
        response = post(client, KML, name)
        assert response.status_code == 415 and error_code(response) == "UNSUPPORTED_FILE_TYPE"

    def test_empty_file(self, client: TestClient) -> None:
        response = post(client, b"")
        assert response.status_code == 422 and error_code(response) == "EMPTY_FILE"

    def test_spoofed_kml(self, client: TestClient) -> None:
        response = post(client, b"MZ\x90\x00 definitely an executable", "survey.kml")
        assert response.status_code == 422 and error_code(response) == "INVALID_KML"

    def test_spoofed_zip(self, client: TestClient) -> None:
        response = post(client, KML, "survey.zip")
        assert response.status_code == 422 and error_code(response) == "CONTENT_MISMATCH"

    def test_corrupt_zip(self, client: TestClient) -> None:
        response = post(client, b"PK\x03\x04" + b"\x00" * 64, "broken.zip")
        assert response.status_code == 422 and error_code(response) == "INVALID_ZIP"

    def test_incomplete_shapefile(self, client: TestClient, workdir: Path) -> None:
        archive = shapefile_zip([box(77, 28, 77.01, 28.01)], drop={".shx"}, workdir=workdir)
        response = post(client, archive, "parcels.zip")
        assert response.status_code == 422 and error_code(response) == "SHAPEFILE_INCOMPLETE"
        assert response.json()["error"]["details"]["missing"] == [".shx"]

    def test_zip_without_shapefile(self, client: TestClient) -> None:
        response = post(client, zip_bytes({"doc.kml": KML}), "kmz-like.zip")
        assert response.status_code == 422 and error_code(response) == "SHAPEFILE_MISSING"

    def test_zip_traversal(self, client: TestClient) -> None:
        archive = zip_bytes({"../../evil.shp": b"x", "evil.shx": b"x", "evil.dbf": b"x"})
        response = post(client, archive, "evil.zip")
        assert response.status_code == 422 and error_code(response) == "ZIP_UNSAFE_PATH"

    def test_zip_symlink(self, client: TestClient) -> None:
        response = post(client, zip_with_symlink(), "link.zip")
        assert response.status_code == 422 and error_code(response) == "ZIP_UNSAFE_ENTRY"

    @pytest.mark.parametrize("crs", ["WGS84", "EPSG:999999", "+proj=merc"])
    def test_invalid_crs_override(self, client: TestClient, crs: str) -> None:
        response = post(client, KML, data={"crs": crs})
        assert response.status_code == 422 and error_code(response) == "INVALID_CRS"

    def test_missing_file_field(self, client: TestClient) -> None:
        response = client.post("/api/files/", data={"crs": "EPSG:4326"})
        assert response.status_code == 422 and error_code(response) == "VALIDATION_ERROR"

    def test_malformed_idempotency_key(self, client: TestClient) -> None:
        response = post(client, KML, headers={"Idempotency-Key": "no spaces allowed"})
        assert response.status_code == 400 and error_code(response) == "INVALID_IDEMPOTENCY_KEY"


@pytest.fixture
def small_limit_client(settings_factory: Callable[..., Settings], db_engine: object, clean_db: None, tmp_path: Path):  # type: ignore[no-untyped-def]
    settings = settings_factory(max_upload_bytes=2048)
    container = Container(settings, db_engine, create_session_factory(db_engine), LocalStorage(tmp_path / "s"))  # type: ignore[arg-type]
    with TestClient(create_app(settings, container)) as test_client:
        yield test_client


class TestSizeLimits:
    def test_file_just_over_the_limit_is_rejected(self, small_limit_client: TestClient) -> None:
        big = kml_document(*[kml_placemark(f"p{i}", kml_point(77, 28)) for i in range(40)])
        assert len(big) > 2048
        response = post(small_limit_client, big)
        assert response.status_code == 413 and error_code(response) == "FILE_TOO_LARGE"

    def test_body_far_over_the_limit_is_cut_off_early(self, small_limit_client: TestClient) -> None:
        response = post(small_limit_client, b"<kml>" + b" " * 200_000)
        assert response.status_code == 413 and error_code(response) == "FILE_TOO_LARGE"
