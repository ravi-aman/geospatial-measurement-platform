"""HTTP response/request schemas (the public API contract, rendered into OpenAPI).

Conventions:
* units are part of field names (``area_m2``, ``length_m``) so they cannot be misread;
* measurement values are rounded for presentation (0.01 m / 0.01 m2); full double precision is kept in the DB;
* GeoJSON geometries are RFC 7946: WGS 84 longitude/latitude. Coordinates whose CRS is unknown are never
  presented as ``geometry`` - they are returned separately as ``raw_geometry`` with ``geometry_crs: null``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import CrsSource, JobStatus, MeasurementStatus, ProjectionMethod, SourceFormat


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True)


# ---------------------------------------------------------------------------- errors
class ErrorBody(_Model):
    code: str = Field(examples=["SHAPEFILE_INCOMPLETE"])
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    request_id: str | None = None


class ErrorResponse(_Model):
    error: ErrorBody


class IssueOut(_Model):
    code: str
    message: str


# ---------------------------------------------------------------------------- files & jobs
class LayerSummaryOut(_Model):
    name: str
    driver: str
    feature_count: int


class FileSummaryOut(_Model):
    total_features: int
    measured: int
    not_applicable: int
    unsupported: int
    failed: int
    geometry_types: dict[str, int]
    total_area_m2: float | None = Field(description="Sum of measured polygon areas, square metres.")
    total_length_m: float | None = Field(description="Sum of measured line lengths, metres.")
    bbox: list[float] | None = Field(description="[min_lon, min_lat, max_lon, max_lat] in WGS 84.")
    features_with_z: int
    repaired_features: int
    error_counts: dict[str, int]
    issue_counts: dict[str, int]
    layers: list[LayerSummaryOut]


class JobErrorOut(_Model):
    code: str
    message: str
    retryable: bool


class JobProgressOut(_Model):
    processed_features: int
    total_features: int | None


class JobOut(_Model):
    id: uuid.UUID
    status: JobStatus
    attempts: int
    max_attempts: int
    progress: JobProgressOut
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    duration_ms: int | None
    error: JobErrorOut | None


class FileLinksOut(_Model):
    self_: str = Field(alias="self", serialization_alias="self")
    measurements: str
    features: str
    tiles: str
    job: str

    model_config = ConfigDict(frozen=True, populate_by_name=True)


class FileOut(_Model):
    id: uuid.UUID
    filename: str
    format: SourceFormat
    size_bytes: int
    sha256: str
    status: JobStatus
    feature_count: int | None = Field(description="Number of features; null until processing finishes.")
    crs: str | None = Field(
        description="Source CRS, e.g. 'EPSG:4326'. 'CUSTOM' if it matches no authority code; "
        "null if the dataset declares none.",
        examples=["EPSG:4326"],
    )
    crs_name: str | None
    crs_source: CrsSource | None
    measurement_strategy: str
    summary: FileSummaryOut | None
    warnings: list[IssueOut]
    job: JobOut
    created_at: datetime
    links: FileLinksOut


class FileListItemOut(_Model):
    id: uuid.UUID
    filename: str
    format: SourceFormat
    size_bytes: int
    status: JobStatus
    feature_count: int | None
    crs: str | None
    created_at: datetime


class FileListOut(_Model):
    items: list[FileListItemOut]
    next_cursor: str | None


# ---------------------------------------------------------------------------- measurements
class MeasurementOut(_Model):
    area_m2: float | None = Field(description="Planimetric area in square metres (polygons).")
    perimeter_m: float | None = Field(description="Total boundary length incl. holes, metres (polygons).")
    length_m: float | None = Field(description="Length in metres (lines).")
    projected_crs: str = Field(description="CRS the geometry was projected to for measurement.")
    method: ProjectionMethod
    geodesic_area_m2: float | None = Field(description="Independent ellipsoidal reference (quality check).")
    geodesic_length_m: float | None
    relative_difference: float | None = Field(
        description="projected / geodesic - 1. A per-feature bound on projection distortion."
    )


class MeasurementItemOut(_Model):
    feature_id: int = Field(description="0-based index of the feature within the file, across all layers.")
    layer: str
    name: str | None
    geometry_type: str | None
    measurement_status: MeasurementStatus
    measurement: MeasurementOut | None
    geometry_repaired: bool
    validity_reason: str | None
    issues: list[IssueOut]
    error: IssueOut | None


class UnitsOut(_Model):
    area: Literal["m2"] = "m2"
    length: Literal["m"] = "m"


class PageOut(_Model):
    limit: int
    next_cursor: str | None
    total: int | None = Field(description="Total matching items; returned on the first page only.")


class MeasurementPageOut(_Model):
    file_id: uuid.UUID
    status: JobStatus
    crs: str | None
    units: UnitsOut = UnitsOut()
    items: list[MeasurementItemOut]
    page: PageOut


# ---------------------------------------------------------------------------- features (GeoJSON)
class FeatureOut(_Model):
    type: Literal["Feature"] = "Feature"
    id: int
    geometry: dict[str, Any] | None = Field(description="RFC 7946 GeoJSON geometry (WGS 84 lon/lat).")
    properties: dict[str, Any] = Field(description="Source attributes, preserved as read.")
    layer: str
    source_fid: int | None
    name: str | None
    geometry_type: str | None
    geometry_crs: Literal["EPSG:4326"] | None
    raw_geometry: dict[str, Any] | None = Field(
        default=None, description="Coordinates as stored in the file when they cannot be placed in WGS 84."
    )
    repaired_geometry: dict[str, Any] | None = Field(
        default=None, description="Geometry actually measured, when the original was invalid and repaired."
    )
    geometry_repaired: bool
    validity_reason: str | None
    measurement_status: MeasurementStatus
    measurement: MeasurementOut | None
    issues: list[IssueOut]
    error: IssueOut | None


class FeatureCollectionOut(_Model):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    file_id: uuid.UUID
    source_crs: str | None
    features: list[FeatureOut]
    page: PageOut


# ---------------------------------------------------------------------------- service
class HealthOut(_Model):
    status: Literal["ok"]


class ReadinessOut(_Model):
    status: Literal["ready", "not_ready"]
    checks: dict[str, str]


class CapabilitiesOut(_Model):
    formats: list[str]
    extensions: list[str]
    max_upload_bytes: int
    max_features_per_file: int
    measurement_strategy: str
    measurements: dict[str, str]
