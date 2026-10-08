"""Pure-ASGI middleware.

Written against the raw ASGI interface rather than ``BaseHTTPMiddleware`` (which wraps the app in an extra
task and has known issues with contextvars and streaming bodies).
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from typing import Any

from fastapi import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.observability.context import request_id_var

access_logger = logging.getLogger("app.access")
error_logger = logging.getLogger("app.api")
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers", ()):
        if key.lower() == name:
            return str(value.decode("latin-1"))
    return None


def _json_error(code: str, message: str, details: dict[str, Any] | None = None) -> bytes:
    return json.dumps(
        {
            "error": {
                "code": code,
                "message": message,
                "details": details or {},
                "request_id": request_id_var.get(),
            }
        }
    ).encode()


class RequestContextMiddleware:
    """Assign/propagate ``X-Request-ID``, log one access line per request, and render unhandled errors.

    Unhandled exceptions are converted to the 500 envelope *here* (not by Starlette's outermost
    ServerErrorMiddleware) so that the response still carries the request id - the one response where
    correlating with server logs matters most.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = _header(scope, b"x-request-id")
        request_id = incoming if incoming and _REQUEST_ID.fullmatch(incoming) else uuid.uuid4().hex
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        state: dict[str, int] = {"status": 500, "started": 0}

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                state["status"], state["started"] = message["status"], 1
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode()))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        except Exception:
            error_logger.exception("unhandled error")
            if state["started"]:
                raise
            body = _json_error(
                "INTERNAL_ERROR", "An unexpected error occurred. Quote the request id when reporting this problem."
            )
            await send_with_id(
                {"type": "http.response.start", "status": 500, "headers": [(b"content-type", b"application/json")]}
            )
            await send_with_id({"type": "http.response.body", "body": body})
        finally:
            access_logger.info(
                "request",
                extra={
                    "fields": {
                        "method": scope.get("method"),
                        "path": scope.get("path"),
                        "status": state["status"],
                        "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                    }
                },
            )
            request_id_var.reset(token)


class BodyTooLargeError(HTTPException):
    """Raised from ``receive`` while the body is being read.

    It subclasses ``HTTPException`` because FastAPI converts any *other* exception raised during form parsing
    into a generic 400; HTTPExceptions are re-raised and rendered by our handler as a 413.
    """

    code = "FILE_TOO_LARGE"

    def __init__(self, max_bytes: int) -> None:
        super().__init__(status_code=413, detail=f"Request body exceeds {max_bytes} bytes.")


class BodySizeLimitMiddleware:
    """Reject request bodies above ``max_bytes`` - by declared Content-Length *and* by bytes actually read.

    Starlette's multipart parser has no per-file size limit and spools uploads to disk; without this, a client
    could fill the disk with one chunked request that never declares its length.
    """

    def __init__(self, app: ASGIApp, max_bytes: int, path_prefix: str = "/api/files") -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.path_prefix = path_prefix

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope.get("method") not in ("POST", "PUT", "PATCH")
            or not str(scope.get("path", "")).startswith(self.path_prefix)
        ):
            await self.app(scope, receive, send)
            return

        declared = _header(scope, b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > self.max_bytes:
            await self._reject(send)
            return

        received = 0
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise BodyTooLargeError(self.max_bytes)
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except BodyTooLargeError:
            if not response_started:
                await self._reject(send)

    async def _reject(self, send: Send) -> None:
        body = _json_error(
            "FILE_TOO_LARGE",
            f"Request body exceeds {self.max_bytes} bytes.",
            {"max_request_bytes": self.max_bytes},
        )
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [(b"content-type", b"application/json"), (b"connection", b"close")],
            }
        )
        await send({"type": "http.response.body", "body": body})


class SecurityHeadersMiddleware:
    """Conservative defaults for an API that serves JSON and binary tiles (no HTML except the docs)."""

    HEADERS: tuple[tuple[bytes, bytes], ...] = (
        (b"x-content-type-options", b"nosniff"),
        (b"x-frame-options", b"DENY"),
        (b"referrer-policy", b"no-referrer"),
        (b"cross-origin-opener-policy", b"same-origin"),
    )

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers: list[Any] = list(message.get("headers", []))
                existing = {k.lower() for k, _ in headers}
                headers.extend(h for h in self.HEADERS if h[0] not in existing)
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)
