"""Execution of one claimed processing job.

Failure classification decides retry behaviour:

=============================  ==========================  ==================================================
Exception                      Meaning                     Job outcome
=============================  ==========================  ==================================================
``DatasetError``               the input itself is bad     FAILED immediately (retrying cannot help)
``StorageError`` / DB errors   infrastructure hiccup       back to PENDING with exponential backoff
``LeaseLostError``             another worker took over    stop silently; the newer attempt owns the job
``JobInterruptedError``        worker shutting down        released to PENDING without consuming an attempt
anything else                  bug / unexpected            retried (bounded by max_attempts), then FAILED
=============================  ==========================  ==================================================

Idempotency: each attempt first deletes any features left by a previous (crashed) attempt, and every batch
insert happens in the same transaction as a *fenced* heartbeat, so rows from a stale worker can never be
interleaved with rows from the current one.
"""

from __future__ import annotations

import logging
import random
import tempfile
import threading
import time
from pathlib import Path

from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db.repositories import ClaimedJob, FeatureRepository, JobOutcome, JobRepository
from app.domain.enums import DatasetWarningCode, JobStatus, MeasurementStatus, SourceFormat
from app.domain.errors import (
    DatasetError,
    JobInterruptedError,
    LeaseLostError,
    ProcessingError,
    UploadRejectedError,
)
from app.geoprocessing.crs import parse_crs_override
from app.geoprocessing.models import FeatureRecord, Issue
from app.geoprocessing.pipeline import DatasetResult, process_dataset
from app.geoprocessing.processor import FeatureProcessor, ProcessingOptions
from app.geoprocessing.projection import strategy_for
from app.ingestion.archive import ZipLimits, extract_shapefile, inspect_shapefile_zip
from app.ingestion.kml_safety import scan_kml
from app.observability.context import bind_job
from app.storage import StorageBackend, StorageError

logger = logging.getLogger("app.processing")

MAX_LISTED_IGNORED_ENTRIES = 10


class JobExecutor:
    def __init__(self, session_factory: sessionmaker[Session], storage: StorageBackend, settings: Settings) -> None:
        self._sessions = session_factory
        self._storage = storage
        self._settings = settings
        self._zip_limits = ZipLimits(
            settings.max_zip_entries, settings.max_zip_uncompressed_bytes, settings.max_zip_compression_ratio
        )

    def execute(self, job: ClaimedJob, stop: threading.Event | None = None) -> JobStatus | None:
        started = time.perf_counter()
        with bind_job(str(job.id), job.request_id):
            logger.info(
                "job started",
                extra={
                    "fields": {
                        "attempt": job.attempts,
                        "max_attempts": job.max_attempts,
                        "format": job.source_format.value,
                    }
                },
            )
            if job.exhausted:
                return self._fail(
                    job,
                    ProcessingError(
                        "MAX_ATTEMPTS_EXCEEDED", "The job repeatedly failed to finish (worker crash or timeout)."
                    ),
                    retryable=False,
                    started=started,
                )
            try:
                status = self._run(job, started, stop)
            except LeaseLostError:
                logger.warning("lease lost; another worker owns the job now")
                return None
            except JobInterruptedError:
                with self._sessions.begin() as session:
                    JobRepository(session).release(job)
                logger.info("job released for shutdown")
                return JobStatus.PENDING
            except DatasetError as exc:
                return self._fail(job, exc, retryable=False, started=started)
            except (StorageError, OperationalError, DBAPIError) as exc:
                return self._fail(
                    job, ProcessingError("INFRASTRUCTURE_ERROR", str(exc)), retryable=True, started=started
                )
            except Exception as exc:
                logger.exception("unexpected processing error")
                return self._fail(
                    job,
                    ProcessingError("INTERNAL_ERROR", f"{type(exc).__name__}: {exc}"),
                    retryable=True,
                    started=started,
                )
            return status

    # ------------------------------------------------------------------ main path
    def _run(self, job: ClaimedJob, started: float, stop: threading.Event | None) -> JobStatus:
        settings = self._settings
        with tempfile.TemporaryDirectory(prefix="geomeasure-job-", dir=settings.temp_dir) as tmp:
            dataset_path, input_warnings = self._prepare_dataset(job, Path(tmp))
            with self._sessions.begin() as session:
                if not JobRepository(session).heartbeat(job, settings.worker_lease_s, 0):
                    raise LeaseLostError
                FeatureRepository(session).delete_for_job(job.id)  # idempotent re-run

            processed = 0

            def sink(records: list[FeatureRecord]) -> None:
                nonlocal processed
                if stop is not None and stop.is_set():
                    raise JobInterruptedError
                processed += len(records)
                with self._sessions.begin() as session:
                    if not JobRepository(session).heartbeat(job, settings.worker_lease_s, processed):
                        raise LeaseLostError
                    FeatureRepository(session).insert_records(job.id, records)

            processor = FeatureProcessor(
                strategy_for(settings.measurement_strategy),
                ProcessingOptions(settings.max_vertices_per_feature, settings.distortion_warning_threshold),
            )
            try:
                override = parse_crs_override(job.crs_override)
            except UploadRejectedError as exc:
                raise DatasetError(exc.code, exc.message) from exc

            def on_start(total: int | None) -> None:
                with self._sessions.begin() as session:
                    if not JobRepository(session).heartbeat(job, settings.worker_lease_s, 0, total=total):
                        raise LeaseLostError

            result = process_dataset(
                dataset_path,
                job.source_format,
                crs_override=override,
                processor=processor,
                batch_size=settings.processing_batch_size,
                max_features=settings.max_features_per_file,
                sink=sink,
                on_start=on_start,
            )

        outcome = self._outcome(result, input_warnings, started)
        with self._sessions.begin() as session:
            if not JobRepository(session).complete(job, outcome):
                raise LeaseLostError
        logger.info(
            "job finished",
            extra={
                "fields": {
                    "status": outcome.status.value,
                    "duration_ms": outcome.duration_ms,
                    "features": outcome.total_features,
                    **{k.lower(): v for k, v in outcome.counts.items()},
                }
            },
        )
        return outcome.status

    def _prepare_dataset(self, job: ClaimedJob, workdir: Path) -> tuple[Path, list[Issue]]:
        """Fetch the upload and turn it into a path GDAL can open, re-validating untrusted content."""
        raw = self._storage.download_to(job.storage_key, workdir / "upload.bin")
        warnings: list[Issue] = []
        if job.source_format is SourceFormat.KML:
            dataset = raw.rename(workdir / "dataset.kml")
            scan = scan_kml(dataset)
            if scan.network_links:
                warnings.append(
                    Issue(
                        DatasetWarningCode.KML_NETWORK_LINKS_IGNORED,
                        f"{scan.network_links} <NetworkLink> element(s) reference remote content "
                        "that is not fetched; only features inside this file were processed.",
                    )
                )
            return dataset, warnings
        try:
            archive = inspect_shapefile_zip(raw, self._zip_limits)
        except UploadRejectedError as exc:
            raise DatasetError(exc.code, exc.message) from exc
        extract_dir = workdir / "extracted"
        extract_dir.mkdir()
        shp = extract_shapefile(raw, archive, extract_dir, self._zip_limits)
        if archive.ignored_entries:
            listed = ", ".join(archive.ignored_entries[:MAX_LISTED_IGNORED_ENTRIES])
            more = len(archive.ignored_entries) - MAX_LISTED_IGNORED_ENTRIES
            warnings.append(
                Issue(
                    DatasetWarningCode.ZIP_ENTRIES_IGNORED,
                    f"Archive entries not used: {listed}" + (f" (+{more} more)" if more > 0 else ""),
                )
            )
        return shp, warnings

    def _outcome(self, result: DatasetResult, input_warnings: list[Issue], started: float) -> JobOutcome:
        failed = result.status_counts.get(MeasurementStatus.FAILED.value, 0)
        crs = result.crs
        return JobOutcome(
            status=JobStatus.COMPLETED_WITH_ERRORS if failed else JobStatus.COMPLETED,
            duration_ms=int((time.perf_counter() - started) * 1000),
            total_features=result.total_features,
            counts=result.status_counts,
            crs=crs.identifier if crs else None,
            crs_name=crs.name if crs else None,
            crs_source=crs.source.value if crs else None,
            crs_wkt=crs.wkt() if crs and crs.identifier == "CUSTOM" else None,
            summary=result.summary,
            warnings=[w.as_dict() for w in input_warnings + result.warnings],
        )

    # ------------------------------------------------------------------ failure path
    def _fail(self, job: ClaimedJob, error: ProcessingError, *, retryable: bool, started: float) -> JobStatus | None:
        delay = self._settings.job_retry_base_delay_s * (2 ** max(job.attempts - 1, 0)) * random.uniform(0.8, 1.2)  # noqa: S311 - jitter, not crypto
        duration_ms = int((time.perf_counter() - started) * 1000)
        try:
            with self._sessions.begin() as session:
                status = JobRepository(session).fail(
                    job,
                    code=error.code,
                    message=error.message,
                    retryable=retryable,
                    retry_delay_s=delay,
                    duration_ms=duration_ms,
                )
        except (OperationalError, DBAPIError):
            # The DB is down: the lease will expire and another attempt will pick the job up.
            logger.exception("could not record job failure")
            return None
        level = logging.WARNING if status is JobStatus.PENDING else logging.ERROR
        logger.log(
            level,
            "job failed",
            extra={
                "fields": {
                    "error_code": error.code,
                    "error": error.message[:500],
                    "retryable": retryable,
                    "next_status": status.value if status else None,
                    "retry_in_s": round(delay, 1) if status is JobStatus.PENDING else None,
                }
            },
        )
        return status
