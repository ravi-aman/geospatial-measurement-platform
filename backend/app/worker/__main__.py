"""Standalone worker entrypoint: ``python -m app.worker``.

SIGTERM (sent by Docker/ECS/Kubernetes on scale-in or deploy) and Ctrl+C trigger a graceful stop: the
current batch finishes, the job is released back to the queue, and the process exits.
"""

from __future__ import annotations

import signal
from types import FrameType

from app.config import get_settings
from app.db.session import create_db_engine, create_session_factory
from app.observability.logging import configure_logging
from app.storage import build_storage
from app.worker.runner import Worker


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    engine = create_db_engine(settings)
    worker = Worker(settings, create_session_factory(engine), build_storage(settings))

    def _shutdown(signum: int, _frame: FrameType | None) -> None:
        worker.stop()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)
    try:
        worker.run_forever()
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
