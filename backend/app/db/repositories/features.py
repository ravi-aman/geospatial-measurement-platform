"""Feature persistence and read queries (pagination, GeoJSON, vector tiles).

Reads use **keyset pagination** (``WHERE (sort_col, feature_index) > (last values)``) instead of OFFSET:
page N costs the same as page 1, which matters at 10^5-10^6 features per file.

The cursor carries only the last row's ``feature_index``; the database looks up that row's sort value itself
(a primary-key lookup). Sending the float sort value through the client would be fragile: PostgreSQL renders
``double precision`` as text with ``extra_float_digits`` (0 on Supabase -> 15 significant digits), so a
round-tripped value may no longer *equal* the stored one and rows would repeat or vanish between pages.

The default order rides the ``(job_id, feature_index)`` primary key; area/length orders ride
``(job_id, area_m2)`` / ``(job_id, length_m)``.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal, cast

from sqlalchemy import (
    ColumnElement,
    LargeBinary,
    Table,
    and_,
    bindparam,
    delete,
    func,
    insert,
    literal_column,
    or_,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from app.db.models import Feature
from app.geoprocessing.models import FeatureRecord

features = cast(Table, Feature.__table__)

# A SQLAlchemy Row carrying the columns selected below (attribute access by column label).
FeatureRow = Any

SortColumn = Literal["feature_index", "area_m2", "length_m"]
GEOJSON_DECIMALS = 9  # ~0.1 mm in degrees; lossless for survey-grade inputs
_WEB_MERCATOR_LAT_LIMIT = 85.05112878


@dataclass(frozen=True, slots=True)
class SortSpec:
    column: SortColumn = "feature_index"
    descending: bool = False

    @classmethod
    def parse(cls, value: str) -> SortSpec:
        descending = value.startswith("-")
        name = value.lstrip("-+")
        if name == "feature_id":
            name = "feature_index"
        if name not in ("feature_index", "area_m2", "length_m"):
            raise ValueError(f"unsupported sort: {value}")
        return cls(name, descending)  # type: ignore[arg-type]

    @property
    def token(self) -> str:
        name = "feature_id" if self.column == "feature_index" else self.column
        return f"-{name}" if self.descending else name


@dataclass(frozen=True, slots=True)
class FeatureFilters:
    statuses: Sequence[str] = ()
    geometry_types: Sequence[str] = ()
    layer: str | None = None


_MEASUREMENT_COLUMNS = (
    features.c.feature_index,
    features.c.layer,
    features.c.source_fid,
    features.c.name,
    features.c.geometry_type,
    features.c.has_z,
    features.c.vertex_count,
    features.c.is_valid,
    features.c.validity_reason,
    features.c.measurement_status,
    features.c.area_m2,
    features.c.perimeter_m,
    features.c.length_m,
    features.c.projected_crs,
    features.c.projection_method,
    features.c.geodesic_area_m2,
    features.c.geodesic_length_m,
    features.c.error_code,
    features.c.error_message,
    features.c.issues,
    (features.c.geom_repaired.is_not(None)).label("repaired"),
)


def _geojson(column: Any, label: str) -> Any:
    return func.ST_AsGeoJSON(column, GEOJSON_DECIMALS).label(label)


class FeatureRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    # ------------------------------------------------------------------ writes (worker)
    def delete_for_job(self, job_id: uuid.UUID) -> None:
        self.session.execute(delete(features).where(features.c.job_id == job_id))

    def insert_records(self, job_id: uuid.UUID, records: Sequence[FeatureRecord]) -> None:
        if not records:
            return
        stmt = insert(features).values(
            job_id=bindparam("job_id"),
            feature_index=bindparam("feature_index"),
            layer=bindparam("layer"),
            source_fid=bindparam("source_fid"),
            name=bindparam("name"),
            geometry_type=bindparam("geometry_type"),
            has_z=bindparam("has_z"),
            vertex_count=bindparam("vertex_count"),
            geom=func.ST_GeomFromWKB(bindparam("geom_wkb", type_=LargeBinary), 4326),
            geom_raw=func.ST_GeomFromWKB(bindparam("geom_raw_wkb", type_=LargeBinary), 0),
            geom_repaired=func.ST_GeomFromWKB(bindparam("geom_repaired_wkb", type_=LargeBinary), 4326),
            properties=bindparam("properties", type_=JSONB),
            is_valid=bindparam("is_valid"),
            validity_reason=bindparam("validity_reason"),
            measurement_status=bindparam("measurement_status"),
            area_m2=bindparam("area_m2"),
            perimeter_m=bindparam("perimeter_m"),
            length_m=bindparam("length_m"),
            projected_crs=bindparam("projected_crs"),
            projection_method=bindparam("projection_method"),
            geodesic_area_m2=bindparam("geodesic_area_m2"),
            geodesic_length_m=bindparam("geodesic_length_m"),
            error_code=bindparam("error_code"),
            error_message=bindparam("error_message"),
            issues=bindparam("issues", type_=JSONB),
        )
        self.session.execute(stmt, [_row(job_id, r) for r in records])

    # ------------------------------------------------------------------ reads (API)
    def _filtered(self, job_id: uuid.UUID, filters: FeatureFilters) -> list[ColumnElement[bool]]:
        clauses: list[ColumnElement[bool]] = [features.c.job_id == job_id]
        if filters.statuses:
            clauses.append(features.c.measurement_status.in_(list(filters.statuses)))
        if filters.geometry_types:
            clauses.append(features.c.geometry_type.in_(list(filters.geometry_types)))
        if filters.layer is not None:
            clauses.append(features.c.layer == filters.layer)
        return clauses

    def count(self, job_id: uuid.UUID, filters: FeatureFilters) -> int:
        stmt = select(func.count()).select_from(features).where(*self._filtered(job_id, filters))
        return int(self.session.execute(stmt).scalar_one())

    def page(
        self,
        job_id: uuid.UUID,
        *,
        filters: FeatureFilters,
        sort: SortSpec,
        after: int | None,
        limit: int,
        with_geometry: bool = False,
    ) -> list[FeatureRow]:
        sort_col = features.c[sort.column]
        columns: list[Any] = list(_MEASUREMENT_COLUMNS)
        if with_geometry:
            columns += [
                features.c.properties,
                _geojson(features.c.geom, "geometry"),
                _geojson(features.c.geom_raw, "geometry_raw"),
            ]
        order = [
            sort_col.desc().nulls_last() if sort.descending else sort_col.asc().nulls_last(),
            features.c.feature_index.asc(),
        ]
        stmt = select(*columns).where(*self._filtered(job_id, filters)).order_by(*order).limit(limit)
        if after is not None:
            stmt = stmt.where(_keyset_predicate(job_id, sort, after))
        return list(self.session.execute(stmt).all())

    def get(self, job_id: uuid.UUID, feature_index: int) -> FeatureRow | None:
        stmt = select(
            *_MEASUREMENT_COLUMNS,
            features.c.properties,
            _geojson(features.c.geom, "geometry"),
            _geojson(features.c.geom_raw, "geometry_raw"),
            _geojson(features.c.geom_repaired, "geometry_repaired"),
        ).where(features.c.job_id == job_id, features.c.feature_index == feature_index)
        return self.session.execute(stmt).one_or_none()

    def tile(self, job_id: uuid.UUID, z: int, x: int, y: int, *, max_features: int) -> bytes:
        """Mapbox Vector Tile for one web-mercator tile, built entirely in PostGIS.

        The GiST index on ``geom`` finds candidates; ST_AsMVTGeom clips/quantises to tile pixels so tiny
        features collapse away at low zoom. Larger features are emitted first so a capped tile still shows
        the most significant shapes.
        """
        envelope = func.ST_TileEnvelope(z, x, y)
        lat = _WEB_MERCATOR_LAT_LIMIT
        clipped = func.ST_ClipByBox2D(features.c.geom, func.ST_MakeEnvelope(-180, -lat, 180, lat, 4326))
        mvt_geom = func.ST_AsMVTGeom(func.ST_Transform(clipped, 3857), envelope, 4096, 64, True)
        inner = (
            select(
                mvt_geom.label("geom"),
                features.c.feature_index.label("feature_id"),
                features.c.name,
                features.c.geometry_type,
                features.c.measurement_status.label("status"),
                features.c.area_m2,
                features.c.length_m,
            )
            .where(
                features.c.job_id == job_id,
                features.c.geom.is_not(None),
                features.c.geom.op("&&")(func.ST_Transform(envelope, 4326)),
            )
            .order_by(
                features.c.area_m2.desc().nulls_last(),
                features.c.length_m.desc().nulls_last(),
                features.c.feature_index,
            )
            .limit(max_features)
            .subquery("tile_rows")
        )
        stmt = select(func.ST_AsMVT(literal_column("tile_rows"), "features", 4096, "geom", "feature_id"))
        stmt = stmt.select_from(inner).where(inner.c.geom.is_not(None))
        data = self.session.execute(stmt).scalar_one_or_none()
        return bytes(data) if data else b""


def _keyset_predicate(job_id: uuid.UUID, sort: SortSpec, after_index: int) -> ColumnElement[bool]:
    """Rows strictly after the row ``after_index`` in ORDER BY (sort_col [DESC] NULLS LAST, feature_index ASC)."""
    idx = features.c.feature_index
    if sort.column == "feature_index":
        return idx < after_index if sort.descending else idx > after_index
    col = features.c[sort.column]
    anchor = features.alias("anchor")
    last = (
        select(anchor.c[sort.column])
        .where(anchor.c.job_id == job_id, anchor.c.feature_index == after_index)
        .scalar_subquery()
    )
    beyond = col < last if sort.descending else col > last
    return or_(
        and_(last.is_not(None), or_(beyond, and_(col == last, idx > after_index), col.is_(None))),
        and_(last.is_(None), col.is_(None), idx > after_index),  # already inside the NULLs tail
    )


def _row(job_id: uuid.UUID, r: FeatureRecord) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "feature_index": r.feature_index,
        "layer": r.layer,
        "source_fid": r.source_fid,
        "name": r.name,
        "geometry_type": r.geometry_type,
        "has_z": r.has_z,
        "vertex_count": r.vertex_count,
        "geom_wkb": r.geom_wgs84,
        "geom_raw_wkb": r.geom_raw,
        "geom_repaired_wkb": r.geom_repaired,
        "properties": r.properties,
        "is_valid": r.is_valid,
        "validity_reason": r.validity_reason,
        "measurement_status": r.status.value,
        "area_m2": r.area_m2,
        "perimeter_m": r.perimeter_m,
        "length_m": r.length_m,
        "projected_crs": r.projected_crs,
        "projection_method": r.projection_method.value if r.projection_method else None,
        "geodesic_area_m2": r.geodesic_area_m2,
        "geodesic_length_m": r.geodesic_length_m,
        "error_code": r.error_code,
        "error_message": r.error_message,
        "issues": [i.as_dict() for i in r.issues],
    }


def parse_geojson(value: str | None) -> dict[str, Any] | None:
    return json.loads(value) if value else None
