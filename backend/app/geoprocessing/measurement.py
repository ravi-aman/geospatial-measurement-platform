"""Metric measurement of geometries.

Units are fixed and explicit: areas in square metres (m2), lengths in metres (m). Projected CRSs created by
:mod:`app.geoprocessing.projection` always use metres, which :func:`assert_metric` enforces.

Every measured feature also gets an independent *geodesic reference* computed on the WGS 84 ellipsoid with
Karney's algorithm (GeographicLib, via ``pyproj.Geod``). It involves no projection at all, so the relative
difference between the two is an empirical, per-feature bound on projection distortion. The projected value
remains the reported measurement (the assignment requires projecting); the geodesic value is quality control.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import shapely
from numpy.typing import NDArray
from pyproj import CRS, Geod, Transformer
from shapely.geometry.base import BaseGeometry

from app.domain.enums import GeometryFamily

_GEOD = Geod(ellps="WGS84")
_MIN_REFERENCE = 1e-6  # below this the relative difference is numerically meaningless


def assert_metric(crs: CRS) -> None:
    units = {axis.unit_name for axis in crs.axis_info}
    if units != {"metre"}:
        raise ValueError(f"measurement CRS must use metres, got {units}")


def transform_geometries(geoms: NDArray[np.object_], transformer: Transformer) -> NDArray[np.object_]:
    """Vectorised coordinate transformation of many geometries (drops Z; output is 2D)."""

    def _apply(coords: NDArray[np.float64]) -> NDArray[np.float64]:
        x, y = transformer.transform(coords[:, 0], coords[:, 1])
        return np.column_stack((x, y))

    result: NDArray[np.object_] = shapely.transform(geoms, _apply)
    return result


@dataclass(frozen=True, slots=True)
class PlanarMeasures:
    area_m2: float | None
    perimeter_m: float | None
    length_m: float | None


def planar_measures(projected: BaseGeometry, family: GeometryFamily) -> PlanarMeasures:
    if family is GeometryFamily.AREAL:
        # perimeter = total boundary length, including interior rings (holes)
        return PlanarMeasures(float(shapely.area(projected)), float(shapely.length(projected)), None)
    return PlanarMeasures(None, None, float(shapely.length(projected)))


def geodesic_reference(geom_wgs84: BaseGeometry, family: GeometryFamily) -> float:
    """Ellipsoidal area (m2) for polygons or length (m) for lines, from lon/lat coordinates."""
    if family is GeometryFamily.AREAL:
        # pyproj's polygon area is signed by ring orientation and *adds* hole areas unless holes wind
        # opposite to the shell, so orient explicitly: CCW shells, CW holes.
        oriented = shapely.orient_polygons(geom_wgs84, exterior_cw=False)
        area, _perimeter = _GEOD.geometry_area_perimeter(oriented)
        return abs(float(area))
    return float(_GEOD.geometry_length(geom_wgs84))


def relative_difference(projected_value: float, geodesic_value: float) -> float | None:
    if geodesic_value < _MIN_REFERENCE:
        return None
    return projected_value / geodesic_value - 1.0
