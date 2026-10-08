"""Measurement correctness, the degree/Web-Mercator traps, and geometry validation/repair."""

from __future__ import annotations

import numpy as np
import pytest
import shapely
from pyproj import CRS, Geod, Transformer
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiLineString,
    MultiPoint,
    MultiPolygon,
    Point,
    Polygon,
    box,
)

from app.domain.enums import GeometryFamily
from app.geoprocessing.crs import WGS84
from app.geoprocessing.geometry import normalize, validate_and_repair
from app.geoprocessing.measurement import (
    assert_metric,
    geodesic_reference,
    planar_measures,
    relative_difference,
    transform_geometries,
)
from app.geoprocessing.projection import LocalEqualAreaStrategy, UtmStrategy

GEOD = Geod(ellps="WGS84")


def project(geom: shapely.Geometry, crs: CRS) -> shapely.Geometry:
    transformer = Transformer.from_crs(WGS84, crs, always_xy=True)
    return transform_geometries(np.array([geom], dtype=object), transformer)[0]


class TestMeasurementAccuracy:
    def test_one_hectare_square_in_utm(self) -> None:
        """100 m x 100 m drawn directly in UTM coordinates is exactly 1 ha."""
        square = box(500000, 3100000, 500100, 3100100)
        measures = planar_measures(square, GeometryFamily.AREAL)
        assert measures.area_m2 == pytest.approx(10_000)
        assert measures.perimeter_m == pytest.approx(400)

    @pytest.mark.parametrize("strategy", [LocalEqualAreaStrategy(), UtmStrategy()])
    @pytest.mark.parametrize(("lon", "lat"), [(77.2, 28.6), (-0.1, 51.5), (151.2, -33.9), (18.0, -33.9)])
    def test_projected_area_matches_geodesic_reference(self, strategy: object, lon: float, lat: float) -> None:
        polygon = box(lon, lat, lon + 0.01, lat + 0.01)
        (choice,) = strategy.assign(np.array([polygon.bounds]))  # type: ignore[attr-defined]
        projected = planar_measures(project(polygon, choice.crs()), GeometryFamily.AREAL).area_m2
        reference = geodesic_reference(polygon, GeometryFamily.AREAL)
        # UTM is conformal (<= ~0.2 % in zone); LAEA is equal-area (~exact).
        tolerance = 2e-3 if choice.method.value == "UTM" else 1e-6
        assert relative_difference(projected, reference) == pytest.approx(0, abs=tolerance)  # type: ignore[arg-type]

    def test_local_equal_area_beats_utm_at_zone_central_meridian(self) -> None:
        polygon = box(74.995, 27.995, 75.005, 28.005)  # centred on UTM 43N's central meridian (75E)
        reference = geodesic_reference(polygon, GeometryFamily.AREAL)
        errors = {}
        for strategy in (LocalEqualAreaStrategy(), UtmStrategy()):
            (choice,) = strategy.assign(np.array([polygon.bounds]))
            area = planar_measures(project(polygon, choice.crs()), GeometryFamily.AREAL).area_m2
            errors[strategy.name] = abs(relative_difference(area, reference))  # type: ignore[arg-type]
        assert errors["utm"] == pytest.approx(0.0008, rel=0.01)  # k0 = 0.9996 -> area scale 0.9992
        assert errors["local_equal_area"] < 1e-8

    def test_web_mercator_would_overstate_area(self) -> None:
        """Why EPSG:3857 is never used for measurement: scale ~ sec^2(lat) inflates area ~30 % in Delhi."""
        polygon = box(77.2, 28.6, 77.21, 28.61)
        mercator = planar_measures(project(polygon, CRS.from_epsg(3857)), GeometryFamily.AREAL).area_m2
        reference = geodesic_reference(polygon, GeometryFamily.AREAL)
        assert mercator / reference > 1.28  # type: ignore[operator]

    def test_degrees_are_meaningless_as_area(self) -> None:
        polygon = box(77.2, 28.6, 77.21, 28.61)
        assert polygon.area == pytest.approx(1e-4)  # "0.0001 square degrees" - not a unit of area
        assert geodesic_reference(polygon, GeometryFamily.AREAL) == pytest.approx(1.08e6, rel=0.01)

    def test_line_length(self) -> None:
        line = LineString([(77.0, 28.0), (77.1, 28.0)])
        (choice,) = LocalEqualAreaStrategy().assign(np.array([line.bounds]))
        length = planar_measures(project(line, choice.crs()), GeometryFamily.LINEAR).length_m
        assert length == pytest.approx(GEOD.geometry_length(line), rel=1e-5)
        assert length == pytest.approx(9_836, rel=1e-3)  # 0.1 deg of longitude at 28N

    def test_polygon_with_hole(self) -> None:
        shell = [(0, 0), (100, 0), (100, 100), (0, 100), (0, 0)]
        hole = [(25, 25), (75, 25), (75, 75), (25, 75), (25, 25)]
        measures = planar_measures(Polygon(shell, [hole]), GeometryFamily.AREAL)
        assert measures.area_m2 == pytest.approx(10_000 - 2_500)
        assert measures.perimeter_m == pytest.approx(400 + 200)  # perimeter includes the hole's boundary

    def test_geodesic_reference_subtracts_holes_regardless_of_winding(self) -> None:
        shell = list(box(77, 28, 77.02, 28.02).exterior.coords)
        hole = list(box(77.005, 28.005, 77.015, 28.015).exterior.coords)
        same_winding = Polygon(shell, [hole])  # both rings CCW: pyproj alone would ADD the hole
        expected = geodesic_reference(Polygon(shell), GeometryFamily.AREAL) - geodesic_reference(
            Polygon(hole), GeometryFamily.AREAL
        )
        assert geodesic_reference(same_winding, GeometryFamily.AREAL) == pytest.approx(expected, rel=1e-9)

    def test_relative_difference_ignores_degenerate_reference(self) -> None:
        assert relative_difference(1.0, 0.0) is None
        assert relative_difference(101.0, 100.0) == pytest.approx(0.01)

    def test_assert_metric_rejects_degree_crs(self) -> None:
        with pytest.raises(ValueError, match="metres"):
            assert_metric(CRS.from_epsg(4326))


class TestNormalize:
    def test_homogeneous_polygon_collection_becomes_multipolygon(self) -> None:
        gc = GeometryCollection([box(0, 0, 1, 1), MultiPolygon([box(2, 2, 3, 3), box(4, 4, 5, 5)])])
        result = normalize(gc)
        assert result.family is GeometryFamily.AREAL and result.collection_normalized
        assert result.geometry.geom_type == "MultiPolygon" and len(result.geometry.geoms) == 3

    def test_homogeneous_line_collection(self) -> None:
        result = normalize(GeometryCollection([LineString([(0, 0), (1, 1)]), LineString([(2, 2), (3, 3)])]))
        assert result.family is GeometryFamily.LINEAR and result.geometry.geom_type == "MultiLineString"

    def test_point_collection(self) -> None:
        result = normalize(GeometryCollection([Point(0, 0), MultiPoint([(1, 1), (2, 2)])]))
        assert result.family is GeometryFamily.PUNTAL and result.geometry.geom_type == "MultiPoint"

    def test_mixed_collection_is_reported(self) -> None:
        result = normalize(GeometryCollection([Point(0, 0), LineString([(0, 0), (1, 1)])]))
        assert result.family is GeometryFamily.MIXED and not result.collection_normalized

    @pytest.mark.parametrize(
        ("geom", "family"),
        [
            (box(0, 0, 1, 1), GeometryFamily.AREAL),
            (MultiPolygon([box(0, 0, 1, 1)]), GeometryFamily.AREAL),
            (LineString([(0, 0), (1, 1)]), GeometryFamily.LINEAR),
            (MultiLineString([[(0, 0), (1, 1)]]), GeometryFamily.LINEAR),
            (Point(0, 0), GeometryFamily.PUNTAL),
        ],
    )
    def test_simple_types(self, geom: shapely.Geometry, family: GeometryFamily) -> None:
        assert normalize(geom).family is family


class TestValidateAndRepair:
    def test_valid_geometry_untouched(self) -> None:
        polygon = box(0, 0, 1, 1)
        result = validate_and_repair(polygon, GeometryFamily.AREAL)
        assert result.is_valid and not result.repaired and result.geometry is polygon

    def test_bowtie_is_repaired_into_two_triangles(self) -> None:
        bowtie = Polygon([(0, 0), (1, 1), (1, 0), (0, 1), (0, 0)])
        result = validate_and_repair(bowtie, GeometryFamily.AREAL)
        assert not result.is_valid and result.repaired
        assert result.reason is not None and result.reason.startswith("Self-intersection")
        assert result.geometry is not None and result.geometry.geom_type == "MultiPolygon"
        assert result.geometry.area == pytest.approx(0.5)

    def test_overlapping_multipolygon_parts_are_dissolved(self) -> None:
        overlapping = MultiPolygon([box(0, 0, 2, 2), box(1, 1, 3, 3)])
        result = validate_and_repair(overlapping, GeometryFamily.AREAL)
        assert result.repaired and result.geometry is not None
        assert result.geometry.area == pytest.approx(7.0)  # union, not 8 (no double counting)

    def test_collapsed_polygon_is_unrepairable(self) -> None:
        flat = Polygon([(0, 0), (1, 0), (2, 0), (0, 0)])
        result = validate_and_repair(flat, GeometryFamily.AREAL)
        assert result.geometry is None and not result.is_valid

    def test_degenerate_line_is_unrepairable(self) -> None:
        line = LineString([(1, 1), (1, 1)])
        result = validate_and_repair(line, GeometryFamily.LINEAR)
        assert result.geometry is None
