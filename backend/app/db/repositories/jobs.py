"""Processing-job persistence and the PostgreSQL-backed work queue.

Queue semantics (see docs/architecture/processing.md):

* **Claim** - ``UPDATE ... WHERE id = (SELECT ... FOR UPDATE SKIP LOCKED LIMIT 1)``: concurrent workers never
  claim the same job and never block each other. A job is claimable when PENDING and due (``run_after``),
  or PROCESSING with an *expired lease* (its worker crashed or hung).
* **Lease + heartbeat** - a claimed job carries ``lease_expires_at``; the worker extends it after every batch.
* **Fencing** - every write by a worker is conditional on ``locked_by = <worker id for this attempt>``.
  A worker whose lease was taken over gets ``rowcount == 0`` and stops, so a stale worker can never
  overwrite a newer attempt.
* **Retries** - transient failures go back to PENDING with exponential ``run_after`` backoff until
  ``max_attempts``; permanent (dataset) failures go straight to FAILED. A crash-looping job (e.g. one that
  OOM-kills the worker) is failed once its attempts are exhausted instead of looping forever.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, Table, and_, case, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.models import ProcessingJob
from app.domain.enums import JobStatus, SourceFormat

jobs = cast(Table, ProcessingJob.__table__)


def _rowcount(result: Any) -> int:
    """Rows matched by an UPDATE (``Session.execute`` is typed as returning a generic ``Result``)."""
    return int(cast(CursorResult[Any], result).rowcount)


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    id: uuid.UUID
    storage_key: str
    source_format: SourceFormat
    crs_override: str | None
    attempts: int
    max_attempts: int
    request_id: str | None
    worker_id: str  # fencing token: unique per worker *and* attempt

    @property
    def exhausted(self) -> bool:
        return self.attempts > self.max_attempts


@dataclass(frozen=True, slots=True)
class JobOutcome:
    status: JobStatus
    duration_ms: int
    total_features: int
    counts: dict[str, int]
    crs: str | None
    crs_name: str | None
    crs_source: str | None
    crs_wkt: str | None
    summary: dict[str, Any]
    warnings: list[dict[str, str]]


class JobRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    # ------------------------------------------------------------------ API side
    def get(self, job_id: uuid.UUID) -> ProcessingJob | None:
        return self.session.get(ProcessingJob, job_id)

    def get_or_create(
        self,
        *,
        fingerprint: str,
        content_sha256: str,
        source_format: SourceFormat,
        storage_key: str,
        crs_override: str | None,
        processor_version: int,
        measurement_strategy: str,
        max_attempts: int,
        request_id: str | None,
    ) -> tuple[ProcessingJob, bool]:
        """Insert the job unless one with the same fingerprint exists (race-safe via ON CONFLICT)."""
        stmt = (
            pg_insert(jobs)
            .values(
                id=uuid.uuid4(),
                fingerprint=fingerprint,
                content_sha256=content_sha256,
                source_format=source_format.value,
                storage_key=storage_key,
                crs_override=crs_override,
                processor_version=processor_version,
                measurement_strategy=measurement_strategy,
                status=JobStatus.PENDING.value,
                attempts=0,
                max_attempts=max_attempts,
                request_id=request_id,
                warnings=[],
            )
            .on_conflict_do_nothing(index_elements=[jobs.c.fingerprint])
            .returning(jobs.c.id)
        )
        created_id = self.session.execute(stmt).scalar_one_or_none()
        job = self.session.execute(select(ProcessingJob).where(ProcessingJob.fingerprint == fingerprint)).scalar_one()
        return job, created_id is not None

    def requeue(self, job_id: uuid.UUID, request_id: str | None) -> None:
        """Give a job that failed for transient reasons a fresh set of attempts."""
        self.session.execute(
            update(jobs)
            .where(jobs.c.id == job_id, jobs.c.status == JobStatus.FAILED.value)
            .values(
                status=JobStatus.PENDING.value,
                attempts=0,
                run_after=func.now(),
                locked_by=None,
                lease_expires_at=None,
                finished_at=None,
                error_code=None,
                error_message=None,
                error_retryable=False,
                request_id=request_id,
                updated_at=func.now(),
            )
        )

    # ------------------------------------------------------------------ worker side
    def claim_next(self, worker_name: str, lease_s: int) -> ClaimedJob | None:
        now = func.now()
        candidate = (
            select(jobs.c.id)
            .where(
                or_(
                    and_(jobs.c.status == JobStatus.PENDING.value, jobs.c.run_after <= now),
                    and_(jobs.c.status == JobStatus.PROCESSING.value, jobs.c.lease_expires_at < now),
                )
            )
            .order_by(jobs.c.run_after)
            .limit(1)
            .with_for_update(skip_locked=True)
            .scalar_subquery()
        )
        token = f"{worker_name}:{uuid.uuid4().hex[:12]}"
        stmt = (
            update(jobs)
            .where(jobs.c.id == candidate)
            .values(
                status=JobStatus.PROCESSING.value,
                attempts=jobs.c.attempts + 1,
                locked_by=token,
                lease_expires_at=now + timedelta(seconds=lease_s),
                started_at=now,
                finished_at=None,
                processed_features=0,
                updated_at=now,
            )
            .returning(
                jobs.c.id,
                jobs.c.storage_key,
                jobs.c.source_format,
                jobs.c.crs_override,
                jobs.c.attempts,
                jobs.c.max_attempts,
                jobs.c.request_id,
            )
        )
        row = self.session.execute(stmt).one_or_none()
        if row is None:
            return None
        return ClaimedJob(
            id=row.id,
            storage_key=row.storage_key,
            source_format=SourceFormat(row.source_format),
            crs_override=row.crs_override,
            attempts=row.attempts,
            max_attempts=row.max_attempts,
            request_id=row.request_id,
            worker_id=token,
        )

    def _fenced(self, job: ClaimedJob) -> Any:
        return update(jobs).where(
            jobs.c.id == job.id, jobs.c.locked_by == job.worker_id, jobs.c.status == JobStatus.PROCESSING.value
        )

    def heartbeat(self, job: ClaimedJob, lease_s: int, processed: int, total: int | None = None) -> bool:
        values: dict[str, Any] = {
            "lease_expires_at": func.now() + timedelta(seconds=lease_s),
            "processed_features": processed,
            "updated_at": func.now(),
        }
        if total is not None:
            values["total_features"] = total
        return _rowcount(self.session.execute(self._fenced(job).values(**values))) == 1

    def complete(self, job: ClaimedJob, outcome: JobOutcome) -> bool:
        counts = outcome.counts
        stmt = self._fenced(job).values(
            status=outcome.status.value,
            finished_at=func.now(),
            duration_ms=outcome.duration_ms,
            total_features=outcome.total_features,
            processed_features=outcome.total_features,
            measured_features=counts.get("MEASURED", 0),
            not_applicable_features=counts.get("NOT_APPLICABLE", 0),
            unsupported_features=counts.get("UNSUPPORTED", 0),
            failed_features=counts.get("FAILED", 0),
            crs=outcome.crs,
            crs_name=outcome.crs_name,
            crs_source=outcome.crs_source,
            crs_wkt=outcome.crs_wkt,
            summary=outcome.summary,
            warnings=outcome.warnings,
            locked_by=None,
            lease_expires_at=None,
            error_code=None,
            error_message=None,
            error_retryable=False,
            updated_at=func.now(),
        )
        return _rowcount(self.session.execute(stmt)) == 1

    def fail(
        self,
        job: ClaimedJob,
        *,
        code: str,
        message: str,
        retryable: bool,
        retry_delay_s: float,
        duration_ms: int | None = None,
    ) -> JobStatus | None:
        """Record a failure. Retryable failures return to PENDING until attempts are exhausted."""
        if retryable:
            next_status: Any = case(
                (jobs.c.attempts >= jobs.c.max_attempts, JobStatus.FAILED.value), else_=JobStatus.PENDING.value
            )
        else:
            next_status = JobStatus.FAILED.value
        stmt = (
            self._fenced(job)
            .values(
                status=next_status,
                run_after=func.now() + timedelta(seconds=retry_delay_s),
                finished_at=func.now(),
                duration_ms=duration_ms,
                locked_by=None,
                lease_expires_at=None,
                error_code=code,
                error_message=message[:2000],
                error_retryable=retryable,
                updated_at=func.now(),
            )
            .returning(jobs.c.status)
        )
        status = self.session.execute(stmt).scalar_one_or_none()
        return JobStatus(status) if status is not None else None

    def release(self, job: ClaimedJob) -> bool:
        """Graceful shutdown: hand the job back without consuming an attempt."""
        stmt = self._fenced(job).values(
            status=JobStatus.PENDING.value,
            attempts=jobs.c.attempts - 1,
            locked_by=None,
            lease_expires_at=None,
            run_after=func.now(),
            updated_at=func.now(),
        )
        return _rowcount(self.session.execute(stmt)) == 1
