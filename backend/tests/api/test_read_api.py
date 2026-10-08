"""GET endpoints: file information, measurements, features, tiles, jobs, listing."""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from app.container import Container
from app.db.models import ProcessingJob
from tests.builders import (
    BOWTIE,
    kml_document,
    kml_line,
    kml_placemark,
    kml_point,
    kml_polygon,
    square,
)

SURVEY = kml_document(
    kml_placemark("small plot", kml_polygon(square(77.0, 28.0, 0.001)), {"owner": "A"}),
    kml_placemark("large plot", kml_polygon(square(77.1, 28.1, 0.01)), {"owner": "B"}),
    kml_placemark("bowtie", kml_polygon(BOWTIE)),
    kml_placemark("haul road", kml_line([(77, 28), (77.05, 28.05)])),
    kml_placemark("well", kml_point(77.2, 28.2)),
    kml_placemark(
        "mixed", "<MultiGeometry>" + kml_point(77, 28) + kml_line([(77, 28), (77.1, 28)]) + "</MultiGeometry>"
    ),
    kml_placemark("no geometry"),
)


@pytest.fixture
def processed(client: TestClient, run_worker: Callable[[], int]) -> str:
    file_id = client.post("/api/files/", files={"file": ("survey.kml", SURVEY)}).json()["id"]
    assert run_worker() == 1
    return str(file_id)


class TestFileInformation:
    def test_assignment_contract_fields(self, client: TestClient, processed: str) -> None:
        body = client.get(f"/api/files/{processed}/").json()
        assert {k: body[k] for k in ("id", "filename", "feature_count", "crs", "status")} == {
            "id": processed,
            "filename": "survey.kml",
            "feature_count": 7,
            "crs": "EPSG:4326",
            "status": "COMPLETED_WITH_ERRORS",
        }

    def test_summary_and_job_details(self, client: TestClient, processed: str) -> None:
        body = client.get(f"/api/files/{processed}/").json()
        summary = body["summary"]
        assert (summary["measured"], summary["not_applicable"], summary["unsupported"], summary["failed"]) == (
            4,
            1,
            1,
            1,
        )
        assert summary["repaired_features"] == 1 and summary["total_area_m2"] > 1_000_000
        assert summary["total_length_m"] == pytest.approx(7_340, rel=0.01)
        assert summary["bbox"] == [77.0, 28.0, 77.2, 28.2]
        assert body["job"]["status"] == "COMPLETED_WITH_ERRORS" and body["job"]["duration_ms"] is not None
        assert body["job"]["progress"] == {"processed_features": 7, "total_features": 7}
        assert body["crs_source"] == "FILE" and body["measurement_strategy"] == "local_equal_area"
        assert "UNSUPPORTED_FEATURES" in [w["code"] for w in body["warnings"]]

    def test_unknown_file(self, client: TestClient, container: Container) -> None:
        response = client.get(f"/api/files/{uuid.uuid4()}/")
        assert response.status_code == 404 and response.json()["error"]["code"] == "FILE_NOT_FOUND"

    def test_malformed_id(self, client: TestClient, container: Container) -> None:
        assert client.get("/api/files/not-a-uuid/").status_code == 422

    def test_list_files_newest_first_with_cursor(self, client: TestClient, container: Container) -> None:
        ids = [
            client.post("/api/files/", files={"file": (f"f{i}.kml", kml_document(kml_placemark(str(i))))}).json()["id"]
            for i in range(3)
        ]
        first = client.get("/api/files/", params={"limit": 2}).json()
        second = client.get("/api/files/", params={"limit": 2, "cursor": first["next_cursor"]}).json()
        assert [f["id"] for f in first["items"] + second["items"]] == ids[::-1]
        assert second["next_cursor"] is None


class TestMeasurements:
    def test_not_ready_yet(self, client: TestClient) -> None:
        file_id = client.post("/api/files/", files={"file": ("s.kml", SURVEY)}).json()["id"]
        response = client.get(f"/api/files/{file_id}/measurements/")
        assert response.status_code == 409 and response.json()["error"]["code"] == "RESULTS_NOT_READY"
        assert response.headers["retry-after"] == "2"

    def test_failed_processing(self, client: TestClient, container: Container) -> None:
        file_id = client.post("/api/files/", files={"file": ("s.kml", SURVEY)}).json()["id"]
        with container.session_factory.begin() as session:
            session.execute(update(ProcessingJob).values(status="FAILED", error_code="INVALID_KML", error_message="x"))
        response = client.get(f"/api/files/{file_id}/measurements/")
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "PROCESSING_FAILED"
        assert response.json()["error"]["details"]["error_code"] == "INVALID_KML"

    def test_items_have_explicit_status_and_units(self, client: TestClient, processed: str) -> None:
        body = client.get(f"/api/files/{processed}/measurements/").json()
        assert body["units"] == {"area": "m2", "length": "m"}
        assert body["page"]["total"] == 7 and body["page"]["next_cursor"] is None
        by_name = {item["name"]: item for item in body["items"]}
        large = by_name["large plot"]
        assert large["geometry_type"] == "Polygon" and large["measurement_status"] == "MEASURED"
        assert large["measurement"]["area_m2"] == pytest.approx(1_088_260, rel=1e-3)
        assert large["measurement"]["length_m"] is None and large["measurement"]["perimeter_m"] > 4000
        assert abs(large["measurement"]["relative_difference"]) < 1e-6
        assert large["measurement"]["method"] == "LOCAL_EQUAL_AREA"
        assert large["measurement"]["projected_crs"].startswith("+proj=laea")
        road = by_name["haul road"]
        assert road["measurement"]["length_m"] == pytest.approx(7_340, rel=0.01)
        assert road["measurement"]["area_m2"] is None
        assert by_name["well"] == {**by_name["well"], "measurement_status": "NOT_APPLICABLE", "measurement": None}
        assert by_name["mixed"]["measurement_status"] == "UNSUPPORTED" and by_name["mixed"]["measurement"] is None
        assert by_name["mixed"]["error"]["code"] == "MIXED_GEOMETRY_COLLECTION"
        assert by_name["no geometry"]["measurement_status"] == "FAILED"
        bowtie = by_name["bowtie"]
        assert bowtie["geometry_repaired"] and bowtie["validity_reason"].startswith("Self-intersection")
        assert [i["code"] for i in bowtie["issues"]] == ["GEOMETRY_REPAIRED"]

    def test_values_are_rounded_to_centimetres(self, client: TestClient, processed: str) -> None:
        items = client.get(f"/api/files/{processed}/measurements/", params={"status": "MEASURED"}).json()["items"]
        for item in items:
            for key in ("area_m2", "length_m", "perimeter_m"):
                value = item["measurement"][key]
                assert value is None or round(value, 2) == value

    def test_keyset_pagination_visits_every_item_once(self, client: TestClient, processed: str) -> None:
        seen, cursor = [], None
        while True:
            params = {"limit": 2} | ({"cursor": cursor} if cursor else {})
            page = client.get(f"/api/files/{processed}/measurements/", params=params).json()
            seen += [i["feature_id"] for i in page["items"]]
            cursor = page["page"]["next_cursor"]
            if cursor is None:
                break
        assert seen == list(range(7))

    def test_sort_by_area_descending_with_nulls_last_across_pages(self, client: TestClient, processed: str) -> None:
        url = f"/api/files/{processed}/measurements/"
        first = client.get(url, params={"sort": "-area_m2", "limit": 3}).json()
        rest = client.get(url, params={"sort": "-area_m2", "limit": 10, "cursor": first["page"]["next_cursor"]}).json()
        areas = [i["measurement"] and i["measurement"]["area_m2"] for i in first["items"] + rest["items"]]
        numeric = [a for a in areas if a is not None]
        assert numeric == sorted(numeric, reverse=True) and len(numeric) == 3
        assert areas[:3] == numeric and all(a is None for a in areas[3:])
        assert len(areas) == 7

    def test_filters(self, client: TestClient, processed: str) -> None:
        url = f"/api/files/{processed}/measurements/"
        polygons = client.get(url, params={"geometry_type": "Polygon"}).json()
        assert {i["name"] for i in polygons["items"]} == {"small plot", "large plot", "bowtie"}
        problems = client.get(url, params=[("status", "FAILED"), ("status", "UNSUPPORTED")]).json()
        assert {i["name"] for i in problems["items"]} == {"no geometry", "mixed"} and problems["page"]["total"] == 2

    def test_invalid_query_parameters(self, client: TestClient, processed: str) -> None:
        url = f"/api/files/{processed}/measurements/"
        assert client.get(url, params={"sort": "name"}).status_code == 422
        assert client.get(url, params={"status": "WHATEVER"}).status_code == 422
        bad_cursor = client.get(url, params={"cursor": "garbage!"})
        assert bad_cursor.status_code == 400 and bad_cursor.json()["error"]["code"] == "INVALID_CURSOR"

    def test_page_size_is_capped(self, client: TestClient, processed: str) -> None:
        assert (
            client.get(f"/api/files/{processed}/measurements/", params={"limit": 10_000}).json()["page"]["limit"] == 500
        )


class TestFeatures:
    def test_geojson_feature_collection(self, client: TestClient, processed: str) -> None:
        body = client.get(f"/api/files/{processed}/features/", params={"limit": 3}).json()
        assert body["type"] == "FeatureCollection" and body["source_crs"] == "EPSG:4326"
        feature = body["features"][0]
        assert feature["type"] == "Feature" and feature["id"] == 0
        assert feature["geometry"]["type"] == "Polygon" and feature["geometry_crs"] == "EPSG:4326"
        assert feature["properties"] == {"Name": "small plot", "owner": "A"}
        assert feature["geometry"]["coordinates"][0][0] == [77.0, 28.0]  # lon, lat order (RFC 7946)
        assert body["page"]["next_cursor"] is not None

    def test_single_feature_includes_repaired_geometry(self, client: TestClient, processed: str) -> None:
        bowtie = client.get(f"/api/files/{processed}/features/2/").json()
        assert bowtie["geometry"]["type"] == "Polygon"  # as submitted
        assert bowtie["repaired_geometry"]["type"] == "MultiPolygon"  # what was measured
        no_geometry = client.get(f"/api/files/{processed}/features/6/").json()
        assert no_geometry["geometry"] is None and no_geometry["error"]["code"] == "GEOMETRY_MISSING"

    def test_unknown_feature(self, client: TestClient, processed: str) -> None:
        response = client.get(f"/api/files/{processed}/features/99/")
        assert response.status_code == 404 and response.json()["error"]["code"] == "FEATURE_NOT_FOUND"

    def test_unreferenced_coordinates_are_not_presented_as_wgs84(
        self, client: TestClient, run_worker: Callable[[], int], workdir: object
    ) -> None:
        from shapely.geometry import box

        from tests.builders import shapefile_zip

        archive = shapefile_zip([box(10, 10, 20, 20)], drop={".prj"}, workdir=workdir)  # type: ignore[arg-type]
        file_id = client.post("/api/files/", files={"file": ("noprj.zip", archive)}).json()["id"]
        run_worker()
        feature = client.get(f"/api/files/{file_id}/features/0/").json()
        assert feature["geometry"] is None and feature["geometry_crs"] is None
        assert feature["raw_geometry"]["type"] == "Polygon"
        assert feature["error"]["code"] == "CRS_MISSING"


class TestTilesAndJobs:
    def test_vector_tile(self, client: TestClient, processed: str) -> None:
        response = client.get(f"/api/files/{processed}/tiles/8/182/107.mvt")
        assert response.status_code == 200 and len(response.content) > 50
        assert response.headers["content-type"] == "application/vnd.mapbox-vector-tile"
        assert "max-age" in response.headers["cache-control"] and response.headers["etag"]

    def test_tile_revalidation_returns_304(self, client: TestClient, processed: str) -> None:
        url = f"/api/files/{processed}/tiles/8/182/107.mvt"
        etag = client.get(url).headers["etag"]
        response = client.get(url, headers={"If-None-Match": etag})
        assert response.status_code == 304 and response.content == b""
        assert client.get(url, headers={"If-None-Match": '"something-else"'}).status_code == 200

    def test_empty_tile(self, client: TestClient, processed: str) -> None:
        assert client.get(f"/api/files/{processed}/tiles/8/0/0.mvt").status_code == 204

    @pytest.mark.parametrize("path", ["23/0/0", "2/4/0", "2/0/-1"])
    def test_out_of_range_tile(self, client: TestClient, processed: str, path: str) -> None:
        assert client.get(f"/api/files/{processed}/tiles/{path}.mvt").status_code in (400, 422)

    def test_job_endpoint(self, client: TestClient, processed: str) -> None:
        job_url = client.get(f"/api/files/{processed}/").json()["links"]["job"]
        body = client.get(job_url).json()
        assert body["status"] == "COMPLETED_WITH_ERRORS" and body["attempts"] == 1 and body["error"] is None
        assert client.get(f"/api/jobs/{uuid.uuid4()}/").status_code == 404
