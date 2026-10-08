"""PostgreSQL-backed queue semantics (claim, SKIP LOCKED, leases, fencing, retries)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

from app.container import Container
from app.db.models import ProcessingJob
from app.db.repositories import JobOutcome, JobRepository
from app.domain.enums import JobStatus, SourceFormat


def new_job(sessions: sessionmaker[Session], *, max_attempts: int = 3) -> uuid.UUID:
    with sessions.begin() as session:
        job, created = JobRepository(session).get_or_create(
            fingerprint=uuid.uuid4().hex,
            content_sha256="0" * 64,
            source_format=SourceFormat.KML,
            storage_key="uploads/00/x.kml",
            crs_override=None,
            processor_version=1,
            measurement_strategy="local_equal_area",
            max_attempts=max_attempts,
            request_id="req-1",
        )
        assert created
        return job.id


def load(sessions: sessionmaker[Session], job_id: uuid.UUID) -> ProcessingJob:
    with sessions() as session:
        job = session.get(ProcessingJob, job_id)
        assert job is not None
        return job


def set_columns(sessions: sessionmaker[Session], job_id: uuid.UUID, **values: object) -> None:
    with sessions.begin() as session:
        session.execute(update(ProcessingJob).where(ProcessingJob.id == job_id).values(**values))


def outcome() -> JobOutcome:
    return JobOutcome(JobStatus.COMPLETED, 5, 0, {}, "EPSG:4326", "WGS 84", "FILE", None, {"total_features": 0}, [])


def test_empty_queue(container: Container) -> None:
    with container.session_factory.begin() as session:
        assert JobRepository(session).claim_next("w", 60) is None


def test_get_or_create_is_idempotent_on_fingerprint(container: Container) -> None:
    kwargs = {
        "fingerprint": "f" * 64,
        "content_sha256": "0" * 64,
        "source_format": SourceFormat.SHAPEFILE,
        "storage_key": "k",
        "crs_override": None,
        "processor_version": 1,
        "measurement_strategy": "utm",
        "max_attempts": 3,
        "request_id": None,
    }
    with container.session_factory.begin() as session:
        first, created_first = JobRepository(session).get_or_create(**kwargs)  # type: ignore[arg-type]
    with container.session_factory.begin() as session:
        second, created_second = JobRepository(session).get_or_create(**kwargs)  # type: ignore[arg-type]
    assert created_first and not created_second and first.id == second.id


def test_claim_marks_processing_and_starts_lease(container: Container) -> None:
    job_id = new_job(container.session_factory)
    with container.session_factory.begin() as session:
        claimed = JobRepository(session).claim_next("worker-a", 60)
    assert claimed is not None and claimed.id == job_id and claimed.attempts == 1
    assert claimed.worker_id.startswith("worker-a:") and claimed.request_id == "req-1"
    job = load(container.session_factory, job_id)
    assert job.status == JobStatus.PROCESSING and job.locked_by == claimed.worker_id
    assert job.lease_expires_at is not None and job.lease_expires_at > datetime.now(UTC)


def test_skip_locked_never_blocks_or_double_claims(container: Container) -> None:
    first_id, second_id = new_job(container.session_factory), new_job(container.session_factory)
    session_a = container.session_factory()
    session_b = container.session_factory()
    try:
        a = JobRepository(session_a).claim_next("a", 60)  # transaction A stays open, holding the row lock
        b = JobRepository(session_b).claim_next("b", 60)  # must skip A's row, not wait for it
        assert a is not None and b is not None
        assert {a.id, b.id} == {first_id, second_id}
        with container.session_factory() as session_c, session_c.begin():
            c = JobRepository(session_c).claim_next("c", 60)
        assert c is None  # both rows locked -> nothing to claim, returns immediately
    finally:
        session_a.commit()
        session_b.commit()
        session_a.close()
        session_b.close()


def test_jobs_not_yet_due_are_not_claimed(container: Container) -> None:
    job_id = new_job(container.session_factory)
    set_columns(container.session_factory, job_id, run_after=datetime.now(UTC) + timedelta(hours=1))
    with container.session_factory.begin() as session:
        assert JobRepository(session).claim_next("w", 60) is None


def test_expired_lease_is_reclaimed_and_stale_worker_is_fenced(container: Container) -> None:
    job_id = new_job(container.session_factory)
    with container.session_factory.begin() as session:
        stale = JobRepository(session).claim_next("crashed", 60)
    assert stale is not None
    set_columns(container.session_factory, job_id, lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))

    with container.session_factory.begin() as session:
        fresh = JobRepository(session).claim_next("healthy", 60)
    assert fresh is not None and fresh.id == job_id and fresh.attempts == 2

    with container.session_factory.begin() as session:
        repo = JobRepository(session)
        assert repo.heartbeat(stale, 60, 10) is False  # stale worker learns it lost the job
        assert repo.complete(stale, outcome()) is False  # ...and cannot overwrite the new attempt
        assert repo.heartbeat(fresh, 60, 10) is True
        assert repo.complete(fresh, outcome()) is True
    assert load(container.session_factory, job_id).status == JobStatus.COMPLETED


def test_transient_failure_retries_with_backoff_until_exhausted(container: Container) -> None:
    job_id = new_job(container.session_factory, max_attempts=2)
    for attempt, expected in ((1, JobStatus.PENDING), (2, JobStatus.FAILED)):
        set_columns(container.session_factory, job_id, run_after=datetime.now(UTC) - timedelta(seconds=1))
        with container.session_factory.begin() as session:
            claimed = JobRepository(session).claim_next("w", 60)
        assert claimed is not None and claimed.attempts == attempt
        with container.session_factory.begin() as session:
            status = JobRepository(session).fail(claimed, code="X", message="boom", retryable=True, retry_delay_s=30)
        assert status is expected
    job = load(container.session_factory, job_id)
    assert job.status == JobStatus.FAILED and job.error_retryable and job.locked_by is None


def test_backoff_delays_the_next_claim(container: Container) -> None:
    new_job(container.session_factory)
    with container.session_factory.begin() as session:
        claimed = JobRepository(session).claim_next("w", 60)
    assert claimed is not None
    with container.session_factory.begin() as session:
        JobRepository(session).fail(claimed, code="X", message="m", retryable=True, retry_delay_s=3600)
    with container.session_factory.begin() as session:
        assert JobRepository(session).claim_next("w", 60) is None


def test_permanent_failure_is_final(container: Container) -> None:
    job_id = new_job(container.session_factory)
    with container.session_factory.begin() as session:
        claimed = JobRepository(session).claim_next("w", 60)
    assert claimed is not None
    with container.session_factory.begin() as session:
        status = JobRepository(session).fail(
            claimed, code="INVALID_KML", message="bad", retryable=False, retry_delay_s=0
        )
    assert status is JobStatus.FAILED
    job = load(container.session_factory, job_id)
    assert (job.error_code, job.error_retryable, job.attempts) == ("INVALID_KML", False, 1)


def test_release_returns_job_without_consuming_an_attempt(container: Container) -> None:
    job_id = new_job(container.session_factory)
    with container.session_factory.begin() as session:
        claimed = JobRepository(session).claim_next("w", 60)
    assert claimed is not None
    with container.session_factory.begin() as session:
        assert JobRepository(session).release(claimed)
    job = load(container.session_factory, job_id)
    assert (job.status, job.attempts, job.locked_by) == (JobStatus.PENDING, 0, None)


def test_requeue_resets_a_failed_job(container: Container) -> None:
    job_id = new_job(container.session_factory)
    set_columns(container.session_factory, job_id, status="FAILED", attempts=3, error_code="X", error_retryable=True)
    with container.session_factory.begin() as session:
        JobRepository(session).requeue(job_id, "req-2")
    job = load(container.session_factory, job_id)
    assert (job.status, job.attempts, job.error_code, job.request_id) == (JobStatus.PENDING, 0, None, "req-2")
