"""Exception hierarchy.

Two families with different consumers:

* :class:`AppError` - request-level errors raised by services and rendered by the API as the structured
  error envelope (``{"error": {"code", "message", "details", "request_id"}}``).
* :class:`ProcessingError` - raised while a background job runs. ``DatasetError`` is *permanent* (the input
  itself is bad, retrying cannot help); ``TransientProcessingError`` is *retryable* (infrastructure hiccup).
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    status_code: int = 400
    code: str = "BAD_REQUEST"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        details: dict[str, Any] | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        self.details = details or {}


class UploadRejectedError(AppError):
    """The uploaded file failed validation at the API boundary (fail fast, nothing is persisted)."""

    status_code = 422
    code = "INVALID_UPLOAD"


class UnsupportedMediaTypeError(UploadRejectedError):
    status_code = 415
    code = "UNSUPPORTED_FILE_TYPE"


class PayloadTooLargeError(UploadRejectedError):
    status_code = 413
    code = "FILE_TOO_LARGE"


class NotFoundError(AppError):
    status_code = 404
    code = "NOT_FOUND"


class ConflictError(AppError):
    status_code = 409
    code = "CONFLICT"


class InvalidRequestError(AppError):
    status_code = 400
    code = "INVALID_REQUEST"


# ---------------------------------------------------------------------------- background processing


class ProcessingError(Exception):
    retryable: bool = False

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class DatasetError(ProcessingError):
    """The dataset cannot be processed as submitted. Permanent: the job fails without retries."""

    retryable = False


class TransientProcessingError(ProcessingError):
    """Infrastructure failure (storage, database). The job is retried with backoff."""

    retryable = True


class LeaseLostError(Exception):
    """Another worker took over the job (our lease expired). Abort without writing anything further."""


class JobInterruptedError(Exception):
    """The worker is shutting down; the job is released back to the queue."""
