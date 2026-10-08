"""Composition root: builds long-lived infrastructure objects once per process.

Routes receive what they need through FastAPI dependencies that read from this container, so tests can
build a container with a test schema / temp storage and every layer below stays framework-agnostic.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db.session import create_db_engine, create_session_factory
from app.services.uploads import UploadService
from app.storage import StorageBackend, build_storage
from app.worker.runner import Worker

logger = logging.getLogger("app")


@dataclass
class Container:
    settings: Settings
    engine: Engine
    session_factory: sessionmaker[Session]
    storage: StorageBackend
    worker: Worker | None = None
    _worker_thread: threading.Thread | None = field(default=None, repr=False)

    @classmethod
    def build(cls, settings: Settings) -> Container:
        engine = create_db_engine(settings)
        return cls(
            settings=settings,
            engine=engine,
            session_factory=create_session_factory(engine),
            storage=build_storage(settings),
        )

    def upload_service(self) -> UploadService:
        wake = self.worker.wake if self.worker is not None else None
        return UploadService(self.session_factory, self.storage, self.settings, on_enqueued=wake)

    def start_embedded_worker(self) -> None:
        """Run a worker thread inside the API process (single-process deployments / local development).

        Production runs workers as separate processes (``python -m app.worker``) so CPU-heavy processing
        never competes with request handling; the code path is identical either way.
        """
        self.worker = Worker(self.settings, self.session_factory, self.storage, name="embedded")
        self._worker_thread = threading.Thread(target=self.worker.run_forever, name="embedded-worker", daemon=True)
        self._worker_thread.start()
        logger.info("embedded worker started")

    def shutdown(self) -> None:
        if self.worker is not None:
            self.worker.stop()
        if self._worker_thread is not None:
            self._worker_thread.join(timeout=30)
        self.engine.dispose()
