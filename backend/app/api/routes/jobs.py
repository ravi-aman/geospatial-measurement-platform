"""``/api/jobs`` - processing job status (progress, attempts, errors)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from app.api import presenters
from app.api.deps import SessionDep
from app.api.schemas import ErrorResponse, JobOut
from app.db.repositories import JobRepository
from app.domain.errors import NotFoundError

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get(
    "/{job_id}/",
    response_model=JobOut,
    responses={404: {"model": ErrorResponse}},
    summary="Processing job status and progress",
)
def get_job(job_id: uuid.UUID, session: SessionDep) -> JobOut:
    job = JobRepository(session).get(job_id)
    if job is None:
        raise NotFoundError("No job with this id.", code="JOB_NOT_FOUND")
    return presenters.job_out(job)
