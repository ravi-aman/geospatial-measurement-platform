"""Batch processor (per-feature failure isolation) and dataset pipeline on real files read through GDAL."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
import shapely
from pyproj import CRS
from shapely.geometry import GeometryCollection, LineString, MultiLineString, MultiPolygon, Point, Polygon, box

from app.domain.enums import DatasetWarningCode, FeatureCode, MeasurementStatus, ProjectionMethod, SourceFormat
from app.domain.errors import DatasetError
from app.geoprocessing.crs import resolve_crs
from app.geoprocessing.models import FeatureRecord
from app.geoprocessing.pipeline import process_dataset
from app.geoprocessing.processor import FeatureProcessor, LayerContext, ProcessingOptions
from app.geoprocessing.projection import LocalEqualAreaStrategy, UtmStrategy
from app.geoprocessing.reader import RawBatch
from tests.builders import (
    BOWTIE,
    kml_document,
    kml_line,
    kml_placemark,
    kml_point,
    kml_polygon,
    shapefile_zip,
    square,
)


def ctx(declared: str | None = "EPSG:4326", *, is_kml: bool = False) -> LayerContext:
    crs, _ = resolve_crs(declared, None)
    return LayerContext.create("layer", is_kml=is_kml, crs=crs)


def run(geoms: list[object], context: LayerContext | None = None, **options: int) -> list[FeatureRecord]:
    wkb = [shapely.to_wkb(g) if isinstance(g, shapely.Geometry) else g for g in geoms]
    batch = RawBatch(fids=list(range(len(geoms))), wkb=wkb, properties=[{"i": i} for i in range(len(geoms))])
    processor = FeatureProcessor(LocalEqualAreaStrategy(), ProcessingOptions(**options))
    return processor.process(context or ctx(), 0, batch)


class TestFeatureProcessor:
    def test_polygon_line_point(self) -> None:
        poly, line, point = run([box(77, 28, 77.01, 28.01), LineString([(77, 28), (77.01, 28)]), Point(77, 28)])
        assert poly.status is MeasurementStatus.MEASURED and poly.area_m2 == pytest.approx(1.09e6, rel=0.01)
        assert poly.perimeter_m and poly.length_m is None
        assert poly.projection_method is ProjectionMethod.LOCAL_EQUAL_AREA
        assert poly.geodesic_area_m2 == pytest.approx(poly.area_m2, rel=1e-6)
        assert line.status is MeasurementStatus.MEASURED and line.length_m == pytest.approx(983.6, rel=1e-3)
        assert line.area_m2 is None
        assert point.status is MeasurementStatus.NOT_APPLICABLE and point.area_m2 is None
        assert all(r.geom_wgs84 is not None and r.geom_raw is None for r in (poly, line, point))

    def test_multi_geometries_are_measured_as_sums(self) -> None:
        a, b = box(77, 28, 77.01, 28.01), box(77.02, 28, 77.03, 28.01)
        multi, single_a, single_b = run([MultiPolygon([a, b]), a, b])
        assert multi.area_m2 == pytest.approx(single_a.area_m2 + single_b.area_m2, rel=1e-9)  # type: ignore[operator]
        (mls,) = run([MultiLineString([[(77, 28), (77.01, 28)], [(77, 28.1), (77.01, 28.1)]])])
        assert mls.status is MeasurementStatus.MEASURED and mls.length_m == pytest.approx(1967, rel=1e-3)

    def test_one_bad_feature_does_not_affect_the_others(self) -> None:
        records = run([box(77, 28, 77.01, 28.01), None, b"\x00garbage", Polygon(), LineString([(77, 28), (77.1, 28)])])
        statuses = [(r.status, r.error_code) for r in records]
        assert statuses == [
            (MeasurementStatus.MEASURED, None),
            (MeasurementStatus.FAILED, FeatureCode.GEOMETRY_MISSING),
            (MeasurementStatus.FAILED, FeatureCode.GEOMETRY_MALFORMED),
            (MeasurementStatus.FAILED, FeatureCode.GEOMETRY_EMPTY),
            (MeasurementStatus.MEASURED, None),
        ]
        assert [r.feature_index for r in records] == [0, 1, 2, 3, 4]
        assert [r.properties for r in records] == [{"i": i} for i in range(5)]  # attributes kept even on failure

    def test_unsupported_and_normalized_collections(self) -> None:
        mixed, polys = run(
            [
                GeometryCollection([Point(77, 28), LineString([(77, 28), (77.1, 28)])]),
                GeometryCollection([box(77, 28, 77.01, 28.01), box(77.02, 28, 77.03, 28.01)]),
            ]
        )
        assert mixed.status is MeasurementStatus.UNSUPPORTED
        assert mixed.error_code == FeatureCode.MIXED_GEOMETRY_COLLECTION
        assert mixed.geom_wgs84 is not None  # still stored and displayable
        assert polys.status is MeasurementStatus.MEASURED
        assert [i.code for i in polys.issues] == [FeatureCode.COLLECTION_NORMALIZED]

    def test_invalid_polygon_is_repaired_and_flagged(self) -> None:
        (record,) = run([Polygon(BOWTIE)])
        assert record.status is MeasurementStatus.MEASURED and record.repaired
        assert record.is_valid is False and record.validity_reason.startswith("Self-intersection")  # type: ignore[union-attr]
        assert [i.code for i in record.issues] == [FeatureCode.GEOMETRY_REPAIRED]
        original = shapely.from_wkb(record.geom_wgs84)
        assert original.geom_type == "Polygon" and not original.is_valid  # original preserved as submitted

    def test_vertex_limit(self) -> None:
        (record,) = run([box(77, 28, 77.01, 28.01).segmentize(0.0001)], max_vertices=50)
        assert record.status is MeasurementStatus.FAILED and record.error_code == FeatureCode.GEOMETRY_TOO_COMPLEX

    def test_missing_crs_keeps_raw_geometry_and_refuses_to_measure(self) -> None:
        poly, point = run([box(10, 10, 20, 20), Point(1, 2)], ctx(None))
        assert poly.status is MeasurementStatus.FAILED and poly.error_code == FeatureCode.CRS_MISSING
        assert poly.geom_raw is not None and poly.geom_wgs84 is None and poly.area_m2 is None
        assert point.status is MeasurementStatus.NOT_APPLICABLE and point.geom_raw is not None

    def test_projected_coordinates_mislabelled_as_wgs84_are_caught(self) -> None:
        (record,) = run([box(500000, 3100000, 500100, 3100100)], ctx("EPSG:4326"))
        assert record.status is MeasurementStatus.FAILED
        assert record.error_code == FeatureCode.COORDINATES_OUT_OF_RANGE
        assert record.geom_raw is not None and record.geom_wgs84 is None

    def test_projected_source_is_transformed_then_measured_locally(self) -> None:
        (record,) = run([box(500000, 3100000, 500100, 3100100)], ctx("EPSG:32643"))  # 1 ha drawn in UTM
        assert record.status is MeasurementStatus.MEASURED
        # The UTM square is 1 ha on the UTM *grid*; the true ground area differs by the UTM scale factor
        # (k0 = 0.9996 at the central meridian -> ground area = 10000 / 0.9996^2).
        assert record.area_m2 == pytest.approx(10_000 / 0.9996**2, rel=1e-5)
        assert record.bbox_wgs84 is not None and 74.9 < record.bbox_wgs84[0] < 75.1

    def test_web_mercator_source_is_not_measured_in_mercator(self) -> None:
        mercator = CRS.from_epsg(3857)
        x0, y0 = 8_593_000.0, 3_325_000.0  # around Delhi
        (record,) = run([box(x0, y0, x0 + 1000, y0 + 1000)], ctx(mercator.to_string()))
        # 1 km x 1 km on the Mercator plane is only ~0.77 km2 on the ground at 28.6N (scale sec^2(lat)).
        assert record.area_m2 == pytest.approx(1_000_000 * 0.7706, rel=0.01)

    def test_distortion_issue_raised_for_huge_feature_with_tight_threshold(self) -> None:
        processor = FeatureProcessor(UtmStrategy(), ProcessingOptions(distortion_threshold=1e-5))
        batch = RawBatch([0], [shapely.to_wkb(box(70, 20, 80, 30))], [{}])
        (record,) = processor.process(ctx(), 0, batch)
        assert FeatureCode.HIGH_PROJECTION_DISTORTION in [i.code for i in record.issues]

    def test_antimeridian_suspect_flagged(self) -> None:
        (record,) = run([box(-179.9, 10, 179.9, 10.1)])
        assert FeatureCode.ANTIMERIDIAN_CROSSING_SUSPECTED in [i.code for i in record.issues]

    def test_z_is_recorded_but_measurement_is_planimetric(self) -> None:
        flat, raised = run([box(77, 28, 77.01, 28.01), Polygon([(x, y, 500.0) for x, y in square(77, 28, 0.01)])])
        assert raised.has_z and not flat.has_z
        assert raised.area_m2 == pytest.approx(flat.area_m2, rel=1e-12)


class TestPipeline:
    def collect(self, path: Path, fmt: SourceFormat, **kwargs: object) -> tuple[object, list[FeatureRecord]]:
        records: list[FeatureRecord] = []
        progress: list[int] = []
        result = process_dataset(
            path,
            fmt,
            crs_override=kwargs.pop("crs_override", None),  # type: ignore[arg-type]
            processor=FeatureProcessor(LocalEqualAreaStrategy()),
            batch_size=int(kwargs.pop("batch_size", 2)),  # type: ignore[call-overload]
            max_features=int(kwargs.pop("max_features", 1000)),  # type: ignore[call-overload]
            sink=records.extend,
            on_progress=progress.append,
        )
        assert progress == sorted(progress) and (not records or progress[-1] == len(records))
        return result, records

    def test_kml_with_folders_reads_every_layer(self, tmp_path: Path) -> None:
        doc = kml_document(
            kml_placemark("root point", kml_point(77.5, 28.5)),
            folders={
                "Parcels": [kml_placemark("P1", kml_polygon(square(77, 28, 0.01)), {"owner": "Asha"})],
                "Roads": [kml_placemark("R1", kml_line([(77, 28), (77.02, 28.02)])), kml_placemark("empty")],
            },
        )
        path = tmp_path / "survey.kml"
        path.write_bytes(doc)
        result, records = self.collect(path, SourceFormat.KML)
        assert [r.layer for r in records] == ["test", "Parcels", "Roads", "Roads"]
        assert [r.feature_index for r in records] == [0, 1, 2, 3]
        assert records[1].properties == {"Name": "P1", "owner": "Asha"}
        assert records[1].name == "P1"
        assert result.crs.identifier == "EPSG:4326"  # type: ignore[attr-defined]
        summary = result.summary  # type: ignore[attr-defined]
        assert summary["total_features"] == 4
        assert summary["status_counts"] == {"MEASURED": 2, "NOT_APPLICABLE": 1, "UNSUPPORTED": 0, "FAILED": 1}
        assert [layer["name"] for layer in summary["layers"]] == ["test", "Parcels", "Roads"]

    def test_shapefile_with_projected_crs(self, tmp_path: Path, workdir: Path) -> None:
        archive = shapefile_zip([box(500000, 3100000, 500100, 3100100)] * 5, crs="EPSG:32643", workdir=workdir)
        shp = self._extract(archive, tmp_path)
        result, records = self.collect(shp, SourceFormat.SHAPEFILE)
        assert result.crs.identifier == "EPSG:32643"  # type: ignore[attr-defined]
        assert len(records) == 5 and all(r.status is MeasurementStatus.MEASURED for r in records)
        assert [r.source_fid for r in records] == [0, 1, 2, 3, 4]

    def test_shapefile_without_prj(self, tmp_path: Path, workdir: Path) -> None:
        archive = shapefile_zip([box(10, 10, 20, 20)], crs="EPSG:4326", drop={".prj"}, workdir=workdir)
        result, records = self.collect(self._extract(archive, tmp_path), SourceFormat.SHAPEFILE)
        assert result.crs is None  # type: ignore[attr-defined]
        assert DatasetWarningCode.CRS_MISSING in [w.code for w in result.warnings]  # type: ignore[attr-defined]
        assert records[0].error_code == FeatureCode.CRS_MISSING

    def test_crs_override_enables_measurement_without_prj(self, tmp_path: Path, workdir: Path) -> None:
        archive = shapefile_zip([box(500000, 3100000, 500100, 3100100)], crs=None, workdir=workdir)
        result, records = self.collect(
            self._extract(archive, tmp_path), SourceFormat.SHAPEFILE, crs_override=CRS.from_epsg(32643)
        )
        assert records[0].status is MeasurementStatus.MEASURED
        assert result.crs.source.value == "USER_OVERRIDE"  # type: ignore[attr-defined]

    def test_feature_limit_is_enforced(self, tmp_path: Path, workdir: Path) -> None:
        archive = shapefile_zip([Point(77, 28)] * 6, workdir=workdir)
        with pytest.raises(DatasetError) as exc:
            self.collect(self._extract(archive, tmp_path), SourceFormat.SHAPEFILE, max_features=5)
        assert exc.value.code == "TOO_MANY_FEATURES"

    def test_empty_kml_completes_with_warning(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.kml"
        path.write_bytes(kml_document())
        result, records = self.collect(path, SourceFormat.KML)
        assert records == []
        assert DatasetWarningCode.NO_FEATURES in [w.code for w in result.warnings]  # type: ignore[attr-defined]

    def test_unreadable_dataset(self, tmp_path: Path) -> None:
        path = tmp_path / "broken.shp"
        path.write_bytes(b"not a shapefile at all")
        with pytest.raises(DatasetError) as exc:
            self.collect(path, SourceFormat.SHAPEFILE)
        assert exc.value.code == "UNREADABLE_DATASET"

    @staticmethod
    def _extract(archive: bytes, tmp_path: Path) -> Path:
        zip_path = tmp_path / "a.zip"
        zip_path.write_bytes(archive)
        out = tmp_path / "x"
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(out)  # trusted test fixture
        return next(out.glob("*.shp"))


def test_summary_totals_are_null_when_nothing_was_measured() -> None:
    """'0 m2' would claim the polygons have no area; null says they were not measured."""
    from app.geoprocessing.summary import SummaryAccumulator

    records = run([box(10, 10, 20, 20), LineString([(1, 1), (2, 2)])], ctx(None))  # no CRS -> not measured
    accumulator = SummaryAccumulator()
    accumulator.add(records)
    snapshot = accumulator.snapshot()
    assert snapshot["total_area_m2"] is None and snapshot["total_length_m"] is None

    measured = SummaryAccumulator()
    measured.add(run([box(77, 28, 77.01, 28.01)]))
    assert measured.snapshot()["total_area_m2"] > 0 and measured.snapshot()["total_length_m"] is None


def test_unknown_geometry_family_is_reported_unsupported(monkeypatch: pytest.MonkeyPatch) -> None:
    """A geometry the classifier does not know must be UNSUPPORTED with a reason, never a silent NOT_APPLICABLE."""
    from app.geoprocessing import processor as processor_module
    from app.geoprocessing.geometry import Normalized

    monkeypatch.setattr(processor_module, "normalize", lambda g: Normalized(g, None, False))
    (record,) = run([box(77, 28, 77.01, 28.01)])
    assert record.status is MeasurementStatus.UNSUPPORTED
    assert record.error_code == FeatureCode.UNSUPPORTED_GEOMETRY_TYPE


def test_pipeline_reports_declared_total_at_start(tmp_path: Path) -> None:
    path = tmp_path / "three.kml"
    path.write_bytes(kml_document(*[kml_placemark(f"p{i}", kml_point(77, 28)) for i in range(3)]))
    started: list[int | None] = []
    process_dataset(
        path,
        SourceFormat.KML,
        crs_override=None,
        processor=FeatureProcessor(LocalEqualAreaStrategy()),
        batch_size=2,
        max_features=10,
        sink=lambda records: None,
        on_start=started.append,
    )
    assert started == [3]
