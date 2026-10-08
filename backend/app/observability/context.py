"""Correlation identifiers carried through the call stack with ``contextvars``.

``contextvars`` (unlike thread-locals) follow asyncio tasks and are copied into the threadpool that runs
FastAPI's sync endpoints, so every log line emitted while handling a request automatically carries its
``request_id`` without passing loggers around. The worker binds ``job_id`` / ``worker_id`` the same way.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from contextvars import ContextVar

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
job_id_var: ContextVar[str | None] = ContextVar("job_id", default=None)
worker_id_var: ContextVar[str | None] = ContextVar("worker_id", default=None)


def current_request_id() -> str | None:
    return request_id_var.get()


@contextlib.contextmanager
def bind_job(job_id: str, request_id: str | None = None) -> Iterator[None]:
    """Bind the job (and the request that created it) to every log line emitted inside the block."""
    job_token = job_id_var.set(job_id)
    request_token = request_id_var.set(request_id)
    try:
        yield
    finally:
        job_id_var.reset(job_token)
        request_id_var.reset(request_token)
