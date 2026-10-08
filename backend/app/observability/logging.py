"""Structured logging on top of the standard library.

One JSON object per line on stdout - the format log shippers (CloudWatch, Loki, Datadog) ingest without
parsing rules. Correlation ids come from :mod:`app.observability.context`; structured fields are passed
with ``logger.info("msg", extra={"fields": {...}})``.

The stdlib is used deliberately instead of a logging framework: the requirements (JSON, context
propagation, exception capture) fit in ~80 lines, and uvicorn / SQLAlchemy / botocore already log through
the stdlib, so their records get the same format and correlation ids for free.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from app.observability.context import job_id_var, request_id_var, worker_id_var

# Keys that must never appear in log output even if a caller passes them by mistake.
_REDACTED_KEYS = frozenset({"password", "secret", "token", "authorization", "database_url", "api_key"})


def _redact(fields: dict[str, Any]) -> dict[str, Any]:
    return {k: ("***" if k.lower() in _REDACTED_KEYS else v) for k, v in fields.items()}


class ContextFilter(logging.Filter):
    """Copy correlation ids from contextvars onto every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        record.job_id = job_id_var.get()
        record.worker_id = worker_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key in ("request_id", "job_id", "worker_id"):
            value = getattr(record, key, None)
            if value:
                payload[key] = value
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            payload.update(_redact(fields))
        if record.exc_info:
            payload["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


class ConsoleFormatter(logging.Formatter):
    """Human-friendly single-line format for local development."""

    def format(self, record: logging.LogRecord) -> str:
        base = f"{self.formatTime(record, '%H:%M:%S')} {record.levelname:<7} {record.name}: {record.getMessage()}"
        ids = " ".join(
            f"{key}={getattr(record, key)}" for key in ("request_id", "job_id") if getattr(record, key, None)
        )
        fields = getattr(record, "fields", None)
        extra = " ".join(f"{k}={v}" for k, v in _redact(fields).items()) if isinstance(fields, dict) else ""
        line = " | ".join(part for part in (base, ids, extra) if part)
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if fmt == "json" else ConsoleFormatter())
    handler.addFilter(ContextFilter())

    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)

    # Our access-log middleware replaces uvicorn's access log (it adds request ids and durations).
    logging.getLogger("uvicorn.access").disabled = True
    for noisy in ("botocore", "boto3", "urllib3", "s3transfer"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    logging.getLogger("uvicorn.error").handlers.clear()
    logging.getLogger("uvicorn").handlers.clear()
