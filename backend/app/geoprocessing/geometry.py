"""Geometry classification, normalisation and validation/repair.

Repair policy (documented in docs/geospatial/geometry-handling.md):

* Invalid polygons (self-intersections, bow-ties, ring problems) are repaired with GEOS ``MakeValid`` using
  the ``structure`` method: rings are rebuilt, shells unioned and holes subtracted - the interpretation that
  matches what a surveyor drew. Collapsed lower-dimension parts are discarded (``keep_collapsed=False``)
  because they contribute no area.
* The *original* geometry is always stored and returned; the repaired geometry is stored separately and is
  the one measured. The feature is flagged ``GEOMETRY_REPAIRED`` with the GEOS validity reason, so nothing
  is silently changed.
* If repair yields nothing of the right dimension the feature fails with ``GEOMETRY_INVALID_UNREPAIRABLE``.
"""

from __future__ import annotations

from dataclasses import dataclass

import shapely
from shapely.geometry import MultiLineString, MultiPoint, MultiPolygon
from shapely.geometry.base import BaseGeometry

from app.domain.enums import GeometryFamily

_FAMILY_BY_TYPE: dict[str, GeometryFamily] = {
    "Polygon": GeometryFamily.AREAL,
    "MultiPolygon": GeometryFamily.AREAL,
    "LineString": GeometryFamily.LINEAR,
    "MultiLineString": GeometryFamily.LINEAR,
    "LinearRing": GeometryFamily.LINEAR,
    "Point": GeometryFamily.PUNTAL,
    "MultiPoint": GeometryFamily.PUNTAL,
}


def family_of(geom: BaseGeometry) -> GeometryFamily | None:
    """Family of a non-collection geometry; ``None`` for collections or unknown types."""
    return _FAMILY_BY_TYPE.get(geom.geom_type)


def _flatten(geom: BaseGeometry) -> list[BaseGeometry]:
    """Recursively explode collections and multi-geometries into non-empty single parts."""
    if geom.is_empty:
        return []
    if hasattr(geom, "geoms"):
        parts: list[BaseGeometry] = []
        for part in geom.geoms:
            parts.extend(_flatten(part))
        return parts
    return [geom]


@dataclass(frozen=True, slots=True)
class Normalized:
    geometry: BaseGeometry
    family: GeometryFamily | None  # None -> unsupported type
    collection_normalized: bool


def normalize(geom: BaseGeometry) -> Normalized:
    """Turn homogeneous GeometryCollections into the equivalent Multi* type.

    KML ``<MultiGeometry>`` often arrives as a GeometryCollection even when every member is a polygon.
    Treating it as a MultiPolygon lets us measure it instead of rejecting it. Collections mixing
    dimensions (e.g. a point and a line) are reported as MIXED: there is no single meaningful measure.
    """
    if geom.geom_type != "GeometryCollection":
        return Normalized(geom, family_of(geom), False)
    parts = _flatten(geom)
    families = {family_of(p) for p in parts}
    if len(families) != 1 or None in families:
        return Normalized(geom, GeometryFamily.MIXED, False)
    family = families.pop()
    if family is GeometryFamily.AREAL:
        return Normalized(MultiPolygon(parts), family, True)
    if family is GeometryFamily.LINEAR:
        return Normalized(MultiLineString(parts), family, True)
    return Normalized(MultiPoint(parts), GeometryFamily.PUNTAL, True)


def _extract(geom: BaseGeometry, family: GeometryFamily) -> BaseGeometry | None:
    """Keep only the parts of ``geom`` that belong to ``family`` (after make_valid)."""
    parts = [p for p in _flatten(geom) if family_of(p) is family]
    if not parts:
        return None
    if family is GeometryFamily.AREAL:
        polys = [q for p in parts for q in (p.geoms if p.geom_type == "MultiPolygon" else [p])]
        return polys[0] if len(polys) == 1 else MultiPolygon(polys)
    lines = [q for p in parts for q in (p.geoms if p.geom_type == "MultiLineString" else [p])]
    return lines[0] if len(lines) == 1 else MultiLineString(lines)


@dataclass(frozen=True, slots=True)
class ValidationResult:
    geometry: BaseGeometry | None  # geometry to measure; None when unrepairable
    is_valid: bool
    reason: str | None
    repaired: bool


def validate_and_repair(geom: BaseGeometry, family: GeometryFamily) -> ValidationResult:
    if shapely.is_valid(geom):
        return ValidationResult(geom, True, None, False)
    reason = shapely.is_valid_reason(geom)
    if family is GeometryFamily.AREAL:
        fixed = shapely.make_valid(geom, method="structure", keep_collapsed=False)
    else:
        fixed = shapely.make_valid(geom)
    measurable = _extract(fixed, family) if fixed is not None else None
    if measurable is None or measurable.is_empty:
        return ValidationResult(None, False, reason, False)
    return ValidationResult(measurable, False, reason, True)
