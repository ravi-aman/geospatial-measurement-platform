"""Relational schema.

``processing_jobs``  one row per *unique* (content hash, options, processor version). It is both the queue
                     entry and the result header (status, CRS, counts, summary). Unique ``fingerprint``
                     makes duplicate uploads reuse a job instead of re-processing.
``files``            one row per upload request (what the client calls a "file"): filename, size, hash,
                     idempotency key. Many files may point at one job.
``features``         one row per feature. Measurement columns live here (1:1 with the feature); a separate
                     table would add a join on every read without any normalisation benefit.

Geometry is stored in PostGIS as WGS 84 (``geom``) for spatial indexing and vector tiles. Coordinates that
cannot be placed in WGS 84 (unknown CRS) go to ``geom_raw`` instead - never mislabelled as 4326.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from geoalchemy2 import Geometry
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Double,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.domain.enums import JobStatus, MeasurementStatus, SourceFormat


def _in(column: str, values: list[str]) -> str:
    quoted = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({quoted})"


JOB_STATUSES = [s.value for s in JobStatus]
MEASUREMENT_STATUSES = [s.value for s in MeasurementStatus]
SOURCE_FORMATS = [s.value for s in SourceFormat]


class ProcessingJob(Base):
    __tablename__ = "processing_jobs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True)
    content_sha256: Mapped[str] = mapped_column(String(64), index=True)
    source_format: Mapped[str] = mapped_column(String(16))
    storage_key: Mapped[str] = mapped_column(Text)
    crs_override: Mapped[str | None] = mapped_column(String(32))
    processor_version: Mapped[int] = mapped_column(Integer)
    measurement_strategy: Mapped[str] = mapped_column(String(32))

    # --- queue state
    status: Mapped[str] = mapped_column(String(32), default=JobStatus.PENDING.value)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    locked_by: Mapped[str | None] = mapped_column(String(128))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    request_id: Mapped[str | None] = mapped_column(String(64))

    # --- outcome
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    total_features: Mapped[int | None] = mapped_column(Integer)
    processed_features: Mapped[int] = mapped_column(Integer, default=0)
    measured_features: Mapped[int] = mapped_column(Integer, default=0)
    not_applicable_features: Mapped[int] = mapped_column(Integer, default=0)
    unsupported_features: Mapped[int] = mapped_column(Integer, default=0)
    failed_features: Mapped[int] = mapped_column(Integer, default=0)
    crs: Mapped[str | None] = mapped_column(String(64))
    crs_name: Mapped[str | None] = mapped_column(Text)
    crs_source: Mapped[str | None] = mapped_column(String(32))
    crs_wkt: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    warnings: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    error_retryable: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    files: Mapped[list[FileUpload]] = relationship(back_populates="job")

    __table_args__ = (
        CheckConstraint(_in("status", JOB_STATUSES), name="status_valid"),
        CheckConstraint(_in("source_format", SOURCE_FORMATS), name="source_format_valid"),
        CheckConstraint("attempts >= 0 AND max_attempts >= 1", name="attempts_valid"),
        # Partial indexes keep the queue claim query fast no matter how many finished jobs accumulate.
        Index("ix_processing_jobs_claimable", "run_after", postgresql_where=text("status = 'PENDING'")),
        Index("ix_processing_jobs_leases", "lease_expires_at", postgresql_where=text("status = 'PROCESSING'")),
    )


class FileUpload(Base):
    __tablename__ = "files"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("processing_jobs.id", ondelete="RESTRICT"), index=True)
    original_filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str | None] = mapped_column(String(255))  # as declared by the client (untrusted)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    content_sha256: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str | None] = mapped_column(String(255), unique=True)
    request_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    job: Mapped[ProcessingJob] = relationship(back_populates="files", lazy="joined")

    __table_args__ = (
        CheckConstraint("size_bytes > 0", name="size_positive"),
        Index("ix_files_created_at_id", "created_at", "id"),
    )


class Feature(Base):
    __tablename__ = "features"

    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("processing_jobs.id", ondelete="CASCADE"), primary_key=True)
    feature_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    layer: Mapped[str] = mapped_column(Text)
    source_fid: Mapped[int | None] = mapped_column(BigInteger)
    name: Mapped[str | None] = mapped_column(Text)
    geometry_type: Mapped[str | None] = mapped_column(String(32))
    has_z: Mapped[bool] = mapped_column(Boolean, default=False)
    vertex_count: Mapped[int] = mapped_column(Integer, default=0)
    geom: Mapped[Any] = mapped_column(Geometry("GEOMETRY", srid=4326, spatial_index=False), nullable=True)
    geom_raw: Mapped[Any] = mapped_column(Geometry("GEOMETRY", srid=-1, spatial_index=False), nullable=True)
    geom_repaired: Mapped[Any] = mapped_column(Geometry("GEOMETRY", srid=4326, spatial_index=False), nullable=True)
    properties: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    is_valid: Mapped[bool | None] = mapped_column(Boolean)
    validity_reason: Mapped[str | None] = mapped_column(Text)
    measurement_status: Mapped[str] = mapped_column(String(32))
    area_m2: Mapped[float | None] = mapped_column(Double)
    perimeter_m: Mapped[float | None] = mapped_column(Double)
    length_m: Mapped[float | None] = mapped_column(Double)
    projected_crs: Mapped[str | None] = mapped_column(Text)
    projection_method: Mapped[str | None] = mapped_column(String(32))
    geodesic_area_m2: Mapped[float | None] = mapped_column(Double)
    geodesic_length_m: Mapped[float | None] = mapped_column(Double)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    issues: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)

    __table_args__ = (
        CheckConstraint(_in("measurement_status", MEASUREMENT_STATUSES), name="measurement_status_valid"),
        CheckConstraint("geom IS NULL OR geom_raw IS NULL", name="one_geometry_representation"),
        CheckConstraint("area_m2 IS NULL OR area_m2 >= 0", name="area_non_negative"),
        CheckConstraint("length_m IS NULL OR length_m >= 0", name="length_non_negative"),
        Index("ix_features_geom", "geom", postgresql_using="gist"),
        Index("ix_features_job_id_area_m2", "job_id", "area_m2"),
        Index("ix_features_job_id_length_m", "job_id", "length_m"),
    )
