from __future__ import annotations

import math

import geopandas as gpd
import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pyproj import CRS
from shapely.geometry import Point

from app.domain.enums import CrsSource, DatasetWarningCode, ProjectionMethod
from app.domain.errors import UploadRejectedError
from app.geoprocessing.crs import horizontal_component, parse_crs_override, resolve_crs
from app.geoprocessing.projection import LocalEqualAreaStrategy, UtmStrategy, strategy_for, utm_epsg


class TestCrsOverride:
    def test_accepts_authority_codes(self) -> None:
        crs = parse_crs_override(" epsg:32643 ")
        assert crs is not None and crs.to_epsg() == 32643

    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_empty_means_no_override(self, value: str | None) -> None:
        assert parse_crs_override(value) is None

    @pytest.mark.parametrize("value", ["+proj=longlat", "WGS84", "EPSG:abc", "file:///etc/passwd", "EPSG:1"])
    def test_rejects_non_authority_input(self, value: str) -> None:
        with pytest.raises(UploadRejectedError) as exc:
            parse_crs_override(value)
        assert exc.value.code == "INVALID_CRS"

    def test_rejects_unknown_code(self) -> None:
        with pytest.raises(UploadRejectedError):
            parse_crs_override("EPSG:999999")

    def test_rejects_vertical_only_crs(self) -> None:
        with pytest.raises(UploadRejectedError):
            parse_crs_override("EPSG:5703")  # NAVD88 height

    def test_compound_crs_uses_horizontal_part(self) -> None:
        crs = parse_crs_override("EPSG:7405")  # OSGB36 / British National Grid + ODN height
        assert crs is not None and crs.to_epsg() == 27700


class TestResolveCrs:
    def test_declared_crs_is_used(self) -> None:
        resolved, warnings = resolve_crs("EPSG:32643", None)
        assert resolved is not None
        assert (resolved.identifier, resolved.source) == ("EPSG:32643", CrsSource.FILE)
        assert not resolved.is_geographic
        assert warnings == []

    def test_esri_wkt_is_recognised_as_epsg(self) -> None:
        esri_wkt = (
            'GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",SPHEROID["WGS_1984",6378137.0,298.257223563]],'
            'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]]'
        )
        resolved, _ = resolve_crs(esri_wkt, None)
        assert resolved is not None and resolved.identifier == "EPSG:4326" and resolved.is_wgs84

    def test_override_wins_and_conflict_is_reported(self) -> None:
        resolved, warnings = resolve_crs("EPSG:4326", CRS.from_epsg(32643))
        assert resolved is not None and resolved.source is CrsSource.USER_OVERRIDE
        assert [w.code for w in warnings] == [DatasetWarningCode.CRS_OVERRIDDEN]

    def test_matching_override_is_not_a_conflict(self) -> None:
        _, warnings = resolve_crs("EPSG:4326", CRS.from_epsg(4326))
        assert warnings == []

    def test_missing_crs_is_explicit_never_guessed(self) -> None:
        resolved, warnings = resolve_crs(None, None)
        assert resolved is None
        assert [w.code for w in warnings] == [DatasetWarningCode.CRS_MISSING]

    def test_unparseable_crs(self) -> None:
        resolved, warnings = resolve_crs("this is not a crs", None)
        assert resolved is None
        assert [w.code for w in warnings] == [DatasetWarningCode.CRS_INVALID]

    def test_wgs84_needs_no_transformer(self) -> None:
        resolved, _ = resolve_crs("EPSG:4326", None)
        assert resolved is not None and resolved.transformer_to_wgs84() is None

    def test_transformer_uses_lon_lat_axis_order(self) -> None:
        resolved, _ = resolve_crs("EPSG:32643", None)
        assert resolved is not None
        transformer = resolved.transformer_to_wgs84()
        assert transformer is not None
        lon, lat = transformer.transform(500000, 0)  # UTM 43N central meridian at the equator
        assert lon == pytest.approx(75.0) and lat == pytest.approx(0.0, abs=1e-9)


def test_horizontal_component_of_geocentric_is_none() -> None:
    assert horizontal_component(CRS.from_epsg(4978)) is None


class TestUtmZoneSelection:
    @pytest.mark.parametrize(
        ("lon", "lat", "epsg"),
        [(77.2, 28.6, 32643), (-122.4, 37.8, 32610), (151.2, -33.9, 32756), (179.99, 10, 32660), (-180, 0, 32601)],
    )
    def test_known_zones(self, lon: float, lat: float, epsg: int) -> None:
        assert utm_epsg(lon, lat) == epsg

    @pytest.mark.parametrize("lat", [84.5, -80.5, 89.9, -90])
    def test_undefined_in_polar_regions(self, lat: float) -> None:
        assert utm_epsg(10, lat) is None

    @settings(max_examples=150, deadline=None)
    @given(
        lon=st.floats(min_value=-179.9, max_value=179.9).filter(lambda v: abs((v + 180) % 6) > 1e-6),
        lat=st.floats(min_value=-79.9, max_value=83.9).filter(lambda v: abs(v) > 1e-6),
    )
    def test_matches_geopandas_estimate_utm_crs(self, lon: float, lat: float) -> None:
        """Independent oracle: GeoPandas queries the PROJ database for the zone containing the point."""
        expected = gpd.GeoSeries([Point(lon, lat)], crs=4326).estimate_utm_crs().to_epsg()
        assert utm_epsg(lon, lat) == expected

    def test_polar_features_fall_back_to_equal_area(self) -> None:
        choices = UtmStrategy().assign(np.array([[10.0, 85.0, 10.5, 85.2], [77.0, 28.0, 77.1, 28.1]]))
        assert choices[0].method is ProjectionMethod.LOCAL_EQUAL_AREA
        assert choices[1].label == "EPSG:32643" and choices[1].method is ProjectionMethod.UTM


class TestLocalEqualArea:
    def test_small_features_snap_to_grid_cell_centres(self) -> None:
        strategy = LocalEqualAreaStrategy(cell_size_deg=1.0)
        a, b = strategy.assign(np.array([[77.1, 28.2, 77.2, 28.3], [77.8, 28.9, 77.9, 28.95]]))
        assert a.key == b.key == ("laea", 28.5, 77.5)  # same cell -> one shared CRS/transformer

    def test_large_features_get_their_own_centre(self) -> None:
        (choice,) = LocalEqualAreaStrategy().assign(np.array([[70.0, 20.0, 80.0, 30.0]]))
        assert choice.key == ("laea", 25.0, 75.0)

    def test_crs_is_metric_and_equal_area(self) -> None:
        (choice,) = LocalEqualAreaStrategy().assign(np.array([[77.1, 28.2, 77.2, 28.3]]))
        crs = choice.crs()
        assert {a.unit_name for a in crs.axis_info} == {"metre"}
        assert "Lambert Azimuthal Equal Area" in crs.to_wkt()

    def test_poles_are_supported(self) -> None:
        (choice,) = LocalEqualAreaStrategy().assign(np.array([[-180.0, 89.0, 180.0, 90.0]]))
        assert choice.method is ProjectionMethod.LOCAL_EQUAL_AREA
        assert math.isfinite(choice.key[1])  # type: ignore[arg-type]


def test_strategy_factory() -> None:
    assert strategy_for("utm").name == "utm"
    assert strategy_for("local_equal_area").name == "local_equal_area"
    with pytest.raises(ValueError, match="unknown"):
        strategy_for("web_mercator")
