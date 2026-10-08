"""Worker loop: claim -> execute -> repeat.

One job at a time per worker process: processing is CPU-bound (GEOS/PROJ), and the GIL makes threads a poor
fit, so throughput scales by running more worker *processes/containers* - ``SKIP LOCKED`` lets any number of
them share the queue safely. Idle workers poll every ``WORKER_POLL_INTERVAL_S``; an in-process ``wake()``
(used by the API's embedded worker right after an upload) skips the wait.

Polling instead of ``LISTEN/NOTIFY`` is deliberate: it works through transaction-mode poolers (Supabase
Supavisor, PgBouncer) where LISTEN cannot, and one tiny indexed query per second per worker is negligible.
"""

from __future__ import annotations

import logging
import os
import socket
import threading

from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db.repositories import JobRepository
from app.observability.context import worker_id_var
from app.services.processing import JobExecutor
from app.storage import StorageBackend

logger = logging.getLogger("app.worker")

_MAX_ERROR_BACKOFF_S = 30.0


class Worker:
    def __init__(
        self,
        settings: Settings,
        session_factory: sessionmaker[Session],
        storage: StorageBackend,
        *,
        name: str | None = None,
    ) -> None:
        self.settings = settings
        self.name = name or f"{socket.gethostname()}-{os.getpid()}"
        self._sessions = session_factory
        self._executor = JobExecutor(session_factory, storage, settings)
        self._stop = threading.Event()
        self._wake = threading.Event()

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    def wake(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def run_once(self) -> bool:
        """Claim and execute at most one job. Returns True if a job was processed."""
        with self._sessions.begin() as session:
            job = JobRepository(session).claim_next(self.name, self.settings.worker_lease_s)
        if job is None:
            return False
        self._executor.execute(job, stop=self._stop)
        return True

    def run_until_idle(self, max_jobs: int = 1000) -> int:
        """Process jobs until the queue is empty (used by tests and one-shot batch runs)."""
        processed = 0
        while processed < max_jobs and self.run_once():
            processed += 1
        return processed

    def run_forever(self) -> None:
        worker_id_var.set(self.name)
        logger.info(
            "worker started",
            extra={
                "fields": {
                    "poll_interval_s": self.settings.worker_poll_interval_s,
                    "lease_s": self.settings.worker_lease_s,
                }
            },
        )
        error_backoff = 1.0
        while not self._stop.is_set():
            try:
                worked = self.run_once()
                error_backoff = 1.0
            except (OperationalError, DBAPIError):
                logger.exception("queue unavailable; backing off", extra={"fields": {"backoff_s": error_backoff}})
                self._stop.wait(error_backoff)
                error_backoff = min(error_backoff * 2, _MAX_ERROR_BACKOFF_S)
                continue
            if not worked:
                self._wake.wait(self.settings.worker_poll_interval_s)
                self._wake.clear()
        logger.info("worker stopped")
