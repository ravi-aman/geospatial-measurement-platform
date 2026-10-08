"""Mapping from persistence rows to API schemas, including presentation rounding.

Rounding policy: metric values are stored with full double precision and presented to 0.01 m / 0.01 m2.
Source coordinates rarely support more (a 1e-7 degree KML coordinate is ~1 cm), and the projected-vs-
geodesic difference is reported alongside so clients can judge the real precision.
"""

from __future__ import annotations

from typing import Any

from app.api.schemas import (
    FeatureOut,
    FileLinksOut,
    FileListItemOut,
    FileOut,
    FileSummaryOut,
    IssueOut,
    JobErrorOut,
    JobOut,
    JobProgressOut,
    LayerSummaryOut,
    MeasurementItemOut,
    MeasurementOut,
)
from app.db.models import FileUpload, ProcessingJob
from app.db.repositories.features import parse_geojson
from app.domain.enums import CrsSource, JobStatus, MeasurementStatus, ProjectionMethod, SourceFormat

METRIC_DECIMALS = 2
RATIO_SIGNIFICANT_DIGITS = 3


def _round(value: float | None, digits: int = METRIC_DECIMALS) -> float | None:
    return None if value is None else round(float(value), digits)


def job_out(job: ProcessingJob) -> JobOut:
    error = None
    if job.error_code:
        error = JobErrorOut(code=job.error_code, message=job.error_message or "", retryable=job.error_retryable)
    return JobOut(
        id=job.id,
        status=JobStatus(job.status),
        attempts=job.attempts,
        max_attempts=job.max_attempts,
        progress=JobProgressOut(processed_features=job.processed_features, total_features=job.total_features),
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        duration_ms=job.duration_ms,
        error=error,
    )


def summary_out(job: ProcessingJob) -> FileSummaryOut | None:
    summary = job.summary
    if not summary:
        return None
    counts = summary.get("status_counts", {})
    return FileSummaryOut(
        total_features=summary.get("total_features", 0),
        measured=counts.get(MeasurementStatus.MEASURED.value, 0),
        not_applicable=counts.get(MeasurementStatus.NOT_APPLICABLE.value, 0),
        unsupported=counts.get(MeasurementStatus.UNSUPPORTED.value, 0),
        failed=counts.get(MeasurementStatus.FAILED.value, 0),
        geometry_types=summary.get("geometry_types", {}),
        total_area_m2=_round(summary.get("total_area_m2")),
        total_length_m=_round(summary.get("total_length_m")),
        bbox=summary.get("bbox"),
        features_with_z=summary.get("features_with_z", 0),
        repaired_features=summary.get("repaired_features", 0),
        error_counts=summary.get("error_counts", {}),
        issue_counts=summary.get("issue_counts", {}),
        layers=[LayerSummaryOut(**layer) for layer in summary.get("layers", [])],
    )


def file_links(file: FileUpload) -> FileLinksOut:
    base = f"/api/files/{file.id}"
    return FileLinksOut(
        self_=f"{base}/",
        measurements=f"{base}/measurements/",
        features=f"{base}/features/",
        tiles=f"{base}/tiles/{{z}}/{{x}}/{{y}}.mvt",
        job=f"/api/jobs/{file.job_id}/",
    )


def file_out(file: FileUpload) -> FileOut:
    job = file.job
    return FileOut(
        id=file.id,
        filename=file.original_filename,
        format=SourceFormat(job.source_format),
        size_bytes=file.size_bytes,
        sha256=file.content_sha256,
        status=JobStatus(job.status),
        feature_count=job.total_features if JobStatus(job.status).has_results else None,
        crs=job.crs,
        crs_name=job.crs_name,
        crs_source=CrsSource(job.crs_source) if job.crs_source else None,
        measurement_strategy=job.measurement_strategy,
        summary=summary_out(job),
        warnings=[IssueOut(**w) for w in job.warnings or []],
        job=job_out(job),
        created_at=file.created_at,
        links=file_links(file),
    )


def file_list_item(file: FileUpload) -> FileListItemOut:
    job = file.job
    return FileListItemOut(
        id=file.id,
        filename=file.original_filename,
        format=SourceFormat(job.source_format),
        size_bytes=file.size_bytes,
        status=JobStatus(job.status),
        feature_count=job.total_features if JobStatus(job.status).has_results else None,
        crs=job.crs,
        created_at=file.created_at,
    )


def measurement_out(row: Any) -> MeasurementOut | None:
    if row.measurement_status != MeasurementStatus.MEASURED.value or row.projected_crs is None:
        return None
    value = row.area_m2 if row.area_m2 is not None else row.length_m
    reference = row.geodesic_area_m2 if row.area_m2 is not None else row.geodesic_length_m
    difference = None
    if value is not None and reference:
        difference = float(f"{value / reference - 1.0:.{RATIO_SIGNIFICANT_DIGITS}g}") + 0.0  # +0.0 drops "-0.0"
    return MeasurementOut(
        area_m2=_round(row.area_m2),
        perimeter_m=_round(row.perimeter_m),
        length_m=_round(row.length_m),
        projected_crs=row.projected_crs,
        method=ProjectionMethod(row.projection_method),
        geodesic_area_m2=_round(row.geodesic_area_m2),
        geodesic_length_m=_round(row.geodesic_length_m),
        relative_difference=difference,
    )


def _issues(row: Any) -> list[IssueOut]:
    return [IssueOut(**issue) for issue in row.issues or []]


def _error(row: Any) -> IssueOut | None:
    if not row.error_code:
        return None
    return IssueOut(code=row.error_code, message=row.error_message or "")


def measurement_item(row: Any) -> MeasurementItemOut:
    return MeasurementItemOut(
        feature_id=row.feature_index,
        layer=row.layer,
        name=row.name,
        geometry_type=row.geometry_type,
        measurement_status=MeasurementStatus(row.measurement_status),
        measurement=measurement_out(row),
        geometry_repaired=bool(row.repaired),
        validity_reason=row.validity_reason,
        issues=_issues(row),
        error=_error(row),
    )


def feature_out(row: Any, *, include_repaired: bool = False) -> FeatureOut:
    geometry = parse_geojson(row.geometry)
    return FeatureOut(
        id=row.feature_index,
        geometry=geometry,
        properties=row.properties or {},
        layer=row.layer,
        source_fid=row.source_fid,
        name=row.name,
        geometry_type=row.geometry_type,
        geometry_crs="EPSG:4326" if geometry is not None else None,
        raw_geometry=parse_geojson(row.geometry_raw),
        repaired_geometry=parse_geojson(row.geometry_repaired) if include_repaired else None,
        geometry_repaired=bool(row.repaired),
        validity_reason=row.validity_reason,
        measurement_status=MeasurementStatus(row.measurement_status),
        measurement=measurement_out(row),
        issues=_issues(row),
        error=_error(row),
    )
