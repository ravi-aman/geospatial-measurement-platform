"""Reproduce the projection-accuracy table in docs/geospatial/crs.md and ADR-005.

    python scripts/compare_projections.py

For a set of test shapes it measures area/length with (a) the UTM zone of the shape, (b) the project's local
Lambert Azimuthal Equal-Area strategy and (c) Web Mercator, and reports the relative difference from the
ellipsoidal geodesic reference (Karney, pyproj.Geod). No database or network access is needed.
"""

from __future__ import annotations

import numpy as np
from pyproj import CRS, Transformer
from shapely.geometry import LineString, box
from shapely.geometry.base import BaseGeometry

from app.domain.enums import GeometryFamily
from app.geoprocessing.crs import WGS84
from app.geoprocessing.measurement import geodesic_reference, planar_measures, transform_geometries
from app.geoprocessing.projection import LocalEqualAreaStrategy, UtmStrategy

CASES: list[tuple[str, BaseGeometry]] = [
    ("1 km square on UTM 43N central meridian (75E, 28N)", box(74.995, 27.995, 75.005, 28.005)),
    ("1 km square near the UTM zone edge (77.95E, 28N)", box(77.945, 27.995, 77.955, 28.005)),
    ("10 km square, Delhi (77.2E, 28.6N)", box(77.15, 28.55, 77.25, 28.65)),
    ("50 km square straddling UTM zones 43/44", box(77.75, 28.0, 78.25, 28.45)),
    ("200 km square (4 vertices)", box(76.0, 27.0, 78.0, 29.0)),
    ("10 km square at 85N (outside UTM)", box(10, 85, 10.9, 85.09)),
    ("10 km E-W line on the central meridian", LineString([(74.95, 28), (75.05, 28)])),
    ("10 km E-W line near the zone edge", LineString([(77.9, 28), (78.0, 28)])),
    ("100 km diagonal line", LineString([(77.0, 28.0), (77.7, 28.6)])),
]


def measure(geom: BaseGeometry, crs: CRS, family: GeometryFamily) -> float:
    transformer = Transformer.from_crs(WGS84, crs, always_xy=True)
    projected = transform_geometries(np.array([geom], dtype=object), transformer)[0]
    measures = planar_measures(projected, family)
    value = measures.area_m2 if family is GeometryFamily.AREAL else measures.length_m
    assert value is not None
    return value


def main() -> None:
    utm, laea = UtmStrategy(), LocalEqualAreaStrategy()
    print("| Case | Geodesic reference | UTM | Local LAEA (default) | Web Mercator |")
    print("|---|---:|---:|---:|---:|")
    for label, geom in CASES:
        family = GeometryFamily.AREAL if geom.geom_type == "Polygon" else GeometryFamily.LINEAR
        reference = geodesic_reference(geom, family)
        unit = "m²" if family is GeometryFamily.AREAL else "m"
        cells = []
        for strategy in (utm, laea):
            (choice,) = strategy.assign(np.array([geom.bounds]))
            if strategy is utm and choice.method.value != "UTM":
                cells.append("n/a (polar)")
                continue
            cells.append(f"{(measure(geom, choice.crs(), family) / reference - 1) * 100:+.4f} %")
        mercator = measure(geom, CRS.from_epsg(3857), family)
        cells.append(f"{(mercator / reference - 1) * 100:+.1f} %")
        print(f"| {label} | {reference:,.1f} {unit} | " + " | ".join(cells) + " |")


if __name__ == "__main__":
    main()
