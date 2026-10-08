"""Per-batch feature processing: decode -> place in WGS 84 -> classify/validate -> project -> measure.

Pure computation with no I/O, so it is unit-testable with in-memory WKB. Failure isolation is per feature:
every problem becomes a status/error code on that feature's record and processing continues. Only
problems with the *dataset* (unreadable file, too many features) abort a job - see ``pipeline.py``.

Work is vectorised where it matters (WKB decoding, coordinate transformation, bounds) and grouped by
projection so each pyproj ``Transformer`` is built once and applied to many geometries at a time.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass

import numpy as np
import shapely
from pyproj import Transformer
from shapely.geometry.base import BaseGeometry

from app.domain.enums import FeatureCode, GeometryFamily, MeasurementStatus
from app.geoprocessing.crs import WGS84, ResolvedCrs
from app.geoprocessing.geometry import normalize, validate_and_repair
from app.geoprocessing.measurement import (
    assert_metric,
    geodesic_reference,
    planar_measures,
    relative_difference,
    transform_geometries,
)
from app.geoprocessing.models import FeatureRecord
from app.geoprocessing.projection import ChoiceKey, ProjectionChoice, ProjectionStrategy
from app.geoprocessing.properties import clean_properties, derive_name
from app.geoprocessing.reader import RawBatch

_LON_LAT_EPSILON = 1e-9


@dataclass(frozen=True, slots=True)
class ProcessingOptions:
    max_vertices: int = 1_000_000
    distortion_threshold: float = 0.001


@dataclass(frozen=True, slots=True)
class LayerContext:
    name: str
    is_kml: bool
    crs: ResolvedCrs | None
    to_wgs84: Transformer | None  # None: identity (already WGS 84) or no CRS

    @classmethod
    def create(cls, name: str, *, is_kml: bool, crs: ResolvedCrs | None) -> LayerContext:
        return cls(name, is_kml, crs, crs.transformer_to_wgs84() if crs is not None else None)


@dataclass(slots=True)
class _Measurable:
    record: FeatureRecord
    family: GeometryFamily
    geometry: BaseGeometry  # WGS 84, valid (possibly repaired)


def _wkb(geom: BaseGeometry) -> bytes:
    data: bytes = shapely.to_wkb(geom, output_dimension=2)
    return data


def _in_lon_lat_range(bounds: tuple[float, float, float, float]) -> bool:
    minx, miny, maxx, maxy = bounds
    e = _LON_LAT_EPSILON
    return -180 - e <= minx <= maxx <= 180 + e and -90 - e <= miny <= maxy <= 90 + e


class FeatureProcessor:
    def __init__(self, strategy: ProjectionStrategy, options: ProcessingOptions | None = None) -> None:
        self._strategy = strategy
        self._options = options or ProcessingOptions()
        self._transformers: dict[ChoiceKey, Transformer] = {}

    # ------------------------------------------------------------------ public API
    def process(self, ctx: LayerContext, start_index: int, batch: RawBatch) -> list[FeatureRecord]:
        records = [self._new_record(ctx, start_index + i, batch, i) for i in range(len(batch))]
        decoded = self._decode(records, batch)
        if ctx.crs is None:
            self._handle_unreferenced(decoded)
            return records
        located = self._locate(ctx, decoded)
        measurable = self._prepare(located)
        self._measure(measurable)
        return records

    # ------------------------------------------------------------------ steps
    @staticmethod
    def _new_record(ctx: LayerContext, index: int, batch: RawBatch, row: int) -> FeatureRecord:
        properties, sanitized = clean_properties(batch.properties[row], is_kml=ctx.is_kml)
        fid = batch.fids[row]
        record = FeatureRecord(
            feature_index=index,
            layer=ctx.name,
            source_fid=int(fid) if fid is not None else None,
            properties=properties,
            name=derive_name(properties),
        )
        if sanitized:
            record.add_issue(
                FeatureCode.PROPERTIES_SANITIZED, "Some attribute values were not JSON-safe and were normalised."
            )
        return record

    def _decode(self, records: list[FeatureRecord], batch: RawBatch) -> list[tuple[FeatureRecord, BaseGeometry]]:
        geoms = shapely.from_wkb(np.array(batch.wkb, dtype=object), on_invalid="ignore")
        decoded: list[tuple[FeatureRecord, BaseGeometry]] = []
        for record, raw, geom in zip(records, batch.wkb, geoms, strict=True):
            if raw is None:
                record.fail(FeatureCode.GEOMETRY_MISSING, "The feature has no geometry.")
                continue
            if geom is None:
                record.fail(FeatureCode.GEOMETRY_MALFORMED, "The geometry could not be decoded.")
                continue
            record.geometry_type = geom.geom_type
            record.has_z = bool(shapely.has_z(geom))
            if geom.is_empty:
                record.fail(FeatureCode.GEOMETRY_EMPTY, "The geometry is empty.")
                continue
            record.vertex_count = int(shapely.get_num_coordinates(geom))
            if record.vertex_count > self._options.max_vertices:
                record.fail(
                    FeatureCode.GEOMETRY_TOO_COMPLEX,
                    f"The geometry has {record.vertex_count} vertices (limit {self._options.max_vertices}).",
                )
                continue
            decoded.append((record, geom))
        return decoded

    def _handle_unreferenced(self, decoded: list[tuple[FeatureRecord, BaseGeometry]]) -> None:
        """No CRS: keep the raw coordinates, never measure (we refuse to guess units)."""
        for record, geom in decoded:
            record.geom_raw = _wkb(geom)
            if self._is_measurable_family(record, normalize(geom).family):
                record.fail(
                    FeatureCode.CRS_MISSING, "The dataset has no CRS, so coordinates cannot be converted to metres."
                )

    def _locate(
        self, ctx: LayerContext, decoded: list[tuple[FeatureRecord, BaseGeometry]]
    ) -> list[tuple[FeatureRecord, BaseGeometry]]:
        """Transform originals to WGS 84 (2D) and reject coordinates that do not fit lon/lat."""
        if not decoded:
            return []
        originals = np.array([geom for _, geom in decoded], dtype=object)
        wgs = transform_geometries(originals, ctx.to_wgs84) if ctx.to_wgs84 is not None else shapely.force_2d(originals)
        bounds = shapely.bounds(wgs)
        located: list[tuple[FeatureRecord, BaseGeometry]] = []
        for (record, original), geom, box in zip(decoded, wgs, bounds, strict=True):
            box_tuple = (float(box[0]), float(box[1]), float(box[2]), float(box[3]))
            if not all(math.isfinite(v) for v in box_tuple):
                record.geom_raw = _wkb(original)
                record.fail(
                    FeatureCode.TRANSFORM_FAILED,
                    f"Coordinates could not be transformed from {ctx.crs.identifier if ctx.crs else '?'} to WGS 84.",
                )
                continue
            if not _in_lon_lat_range(box_tuple):
                record.geom_raw = _wkb(original)
                record.fail(
                    FeatureCode.COORDINATES_OUT_OF_RANGE,
                    f"Coordinates fall outside longitude/latitude bounds {box_tuple}; "
                    "the declared CRS is probably wrong.",
                )
                continue
            record.geom_wgs84 = _wkb(geom)
            record.bbox_wgs84 = box_tuple
            located.append((record, geom))
        return located

    def _prepare(self, located: list[tuple[FeatureRecord, BaseGeometry]]) -> list[_Measurable]:
        measurable: list[_Measurable] = []
        for record, geom in located:
            norm = normalize(geom)
            if norm.collection_normalized:
                record.add_issue(
                    FeatureCode.COLLECTION_NORMALIZED,
                    f"Homogeneous GeometryCollection measured as {norm.geometry.geom_type}.",
                )
            if norm.family is None or not self._is_measurable_family(record, norm.family):
                continue
            result = validate_and_repair(norm.geometry, norm.family)
            record.is_valid = result.is_valid
            record.validity_reason = result.reason
            if result.geometry is None:
                record.fail(
                    FeatureCode.GEOMETRY_INVALID_UNREPAIRABLE,
                    f"Invalid geometry could not be repaired ({result.reason}).",
                )
                continue
            if result.repaired:
                record.geom_repaired = _wkb(result.geometry)
                record.add_issue(
                    FeatureCode.GEOMETRY_REPAIRED, f"Invalid geometry ({result.reason}) was repaired before measuring."
                )
            minx, _, maxx, _ = result.geometry.bounds
            if maxx - minx > 180:
                record.add_issue(
                    FeatureCode.ANTIMERIDIAN_CROSSING_SUSPECTED,
                    "The geometry spans more than 180 degrees of longitude; it may cross the "
                    "antimeridian, which lon/lat coordinates cannot represent unambiguously.",
                )
            measurable.append(_Measurable(record, norm.family, result.geometry))
        return measurable

    @staticmethod
    def _is_measurable_family(record: FeatureRecord, family: GeometryFamily | None) -> bool:
        if family in (GeometryFamily.AREAL, GeometryFamily.LINEAR):
            return True
        if family is GeometryFamily.PUNTAL:
            record.status = MeasurementStatus.NOT_APPLICABLE
        elif family is GeometryFamily.MIXED:
            record.unsupported(
                FeatureCode.MIXED_GEOMETRY_COLLECTION,
                "GeometryCollection mixing points, lines and/or polygons has no single measure.",
            )
        else:
            record.unsupported(
                FeatureCode.UNSUPPORTED_GEOMETRY_TYPE,
                f"Geometry type {record.geometry_type} is not supported for measurement.",
            )
        return False

    def _measure(self, items: list[_Measurable]) -> None:
        if not items:
            return
        geoms = np.array([item.geometry for item in items], dtype=object)
        choices = self._strategy.assign(shapely.bounds(geoms))
        groups: dict[ChoiceKey, list[int]] = defaultdict(list)
        for i, choice in enumerate(choices):
            groups[choice.key].append(i)
        for indices in groups.values():
            choice = choices[indices[0]]
            projected = transform_geometries(geoms[indices], self._transformer(choice))
            for i, geom in zip(indices, projected, strict=True):
                self._record_measurement(items[i], geom, choice)

    def _transformer(self, choice: ProjectionChoice) -> Transformer:
        transformer = self._transformers.get(choice.key)
        if transformer is None:
            target = choice.crs()
            assert_metric(target)
            transformer = Transformer.from_crs(WGS84, target, always_xy=True)
            self._transformers[choice.key] = transformer
        return transformer

    def _record_measurement(self, item: _Measurable, projected: BaseGeometry, choice: ProjectionChoice) -> None:
        record = item.record
        measures = planar_measures(projected, item.family)
        value = measures.area_m2 if item.family is GeometryFamily.AREAL else measures.length_m
        if value is None or not math.isfinite(value):
            record.fail(FeatureCode.MEASUREMENT_FAILED, "Projected measurement was not a finite number.")
            return
        record.status = MeasurementStatus.MEASURED
        record.area_m2, record.perimeter_m, record.length_m = (
            measures.area_m2,
            measures.perimeter_m,
            measures.length_m,
        )
        record.projected_crs = choice.label
        record.projection_method = choice.method
        try:
            reference = geodesic_reference(item.geometry, item.family)
        except (ValueError, RuntimeError):
            return  # quality check unavailable; the measurement itself stands
        if item.family is GeometryFamily.AREAL:
            record.geodesic_area_m2 = reference
        else:
            record.geodesic_length_m = reference
        difference = relative_difference(value, reference)
        if difference is not None and abs(difference) > self._options.distortion_threshold:
            record.add_issue(
                FeatureCode.HIGH_PROJECTION_DISTORTION,
                f"Projected value differs from the ellipsoidal geodesic reference by {difference:+.3%}.",
            )
