"""HTTP plumbing that needs no database: cursors, middleware, error envelope, logging, config."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.errors import install_error_handlers
from app.api.middleware import BodySizeLimitMiddleware, RequestContextMiddleware, SecurityHeadersMiddleware
from app.api.pagination import (
    decode_feature_cursor,
    decode_file_cursor,
    encode_feature_cursor,
    encode_file_cursor,
)
from app.config import Settings
from app.db.repositories import SortSpec
from app.domain.errors import InvalidRequestError, NotFoundError
from app.observability.context import bind_job, request_id_var
from app.observability.logging import ConsoleFormatter, ContextFilter, JsonFormatter


class TestCursors:
    def test_feature_cursor_roundtrip(self) -> None:
        spec = SortSpec.parse("-area_m2")
        assert decode_feature_cursor(encode_feature_cursor(spec, 42), spec) == 42

    def test_cursor_carries_no_float_values(self) -> None:
        """Sort values are resolved server-side, so float text-rendering precision can never break paging."""
        import base64

        cursor = encode_feature_cursor(SortSpec.parse("-area_m2"), 7)
        payload = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        assert payload == {"s": "-area_m2", "i": 7}

    def test_cursor_bound_to_sort_order(self) -> None:
        cursor = encode_feature_cursor(SortSpec.parse("feature_id"), 3)
        with pytest.raises(InvalidRequestError, match="different sort"):
            decode_feature_cursor(cursor, SortSpec.parse("-area_m2"))

    @pytest.mark.parametrize(
        "cursor",
        [
            "!!!",
            "bm90LWpzb24",  # not-json
            "WzEsMl0",  # [1,2]
            "eyJzIjoiZmVhdHVyZV9pZCIsImkiOiJ4In0",  # {"s":"feature_id","i":"x"}
            "eyJzIjoiZmVhdHVyZV9pZCIsImkiOi0xfQ",  # {"s":"feature_id","i":-1}
            "eyJzIjoiZmVhdHVyZV9pZCIsImkiOnRydWV9",  # {"s":"feature_id","i":true}
        ],
    )
    def test_malformed_cursors(self, cursor: str) -> None:
        with pytest.raises(InvalidRequestError):
            decode_feature_cursor(cursor, SortSpec.parse("feature_id"))

    def test_file_cursor_roundtrip(self) -> None:
        now, file_id = datetime.now(UTC), uuid.uuid4()
        assert decode_file_cursor(encode_file_cursor(now, file_id)) == (now, file_id)
        with pytest.raises(InvalidRequestError):
            decode_file_cursor(encode_feature_cursor(SortSpec(), 1))

    def test_sort_spec(self) -> None:
        assert SortSpec.parse("feature_id") == SortSpec("feature_index", False)
        assert SortSpec.parse("-length_m").token == "-length_m"
        with pytest.raises(ValueError, match="unsupported"):
            SortSpec.parse("name")


def build_app(max_bytes: int = 100) -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)

    @app.post("/api/files/")
    async def echo(request: Request) -> dict[str, int]:
        return {"received": len(await request.body())}

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("secret internal detail")

    @app.get("/missing")
    def missing() -> None:
        raise NotFoundError("nope", code="FILE_NOT_FOUND")

    @app.get("/rid")
    def rid() -> dict[str, str | None]:
        return {"rid": request_id_var.get()}

    app.add_middleware(BodySizeLimitMiddleware, max_bytes=max_bytes)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestContextMiddleware)
    return app


class TestMiddleware:
    @pytest.fixture
    def client(self) -> TestClient:
        return TestClient(build_app(), raise_server_exceptions=False)

    def test_body_within_limit(self, client: TestClient) -> None:
        assert client.post("/api/files/", content=b"x" * 100).json() == {"received": 100}

    def test_declared_length_over_limit(self, client: TestClient) -> None:
        response = client.post("/api/files/", content=b"x" * 101)
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "FILE_TOO_LARGE"

    def test_chunked_body_without_length_is_counted(self, client: TestClient) -> None:
        def chunks():  # type: ignore[no-untyped-def]
            for _ in range(5):
                yield b"x" * 40

        response = client.post("/api/files/", content=chunks())
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "FILE_TOO_LARGE"

    def test_request_id_generated_and_propagated(self, client: TestClient) -> None:
        response = client.get("/rid")
        assert response.headers["x-request-id"] == response.json()["rid"]
        assert len(response.headers["x-request-id"]) == 32

    def test_valid_incoming_request_id_is_kept(self, client: TestClient) -> None:
        assert client.get("/rid", headers={"X-Request-ID": "abc-123"}).json()["rid"] == "abc-123"

    def test_unsafe_incoming_request_id_is_replaced(self, client: TestClient) -> None:
        rid = client.get("/rid", headers={"X-Request-ID": "bad id\nwith newline"}).json()["rid"]
        assert rid != "bad id\nwith newline" and len(rid) == 32

    def test_security_headers(self, client: TestClient) -> None:
        headers = client.get("/rid").headers
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["x-frame-options"] == "DENY"

    def test_unhandled_errors_do_not_leak_internals(self, client: TestClient) -> None:
        response = client.get("/boom")
        body = response.json()
        assert response.status_code == 500
        assert body["error"]["code"] == "INTERNAL_ERROR"
        assert "secret" not in response.text
        assert body["error"]["request_id"] == response.headers["x-request-id"]

    def test_app_errors_use_envelope(self, client: TestClient) -> None:
        response = client.get("/missing")
        assert response.status_code == 404
        assert response.json()["error"] == {
            "code": "FILE_NOT_FOUND",
            "message": "nope",
            "details": {},
            "request_id": response.headers["x-request-id"],
        }

    def test_unknown_route_uses_envelope(self, client: TestClient) -> None:
        assert client.get("/nowhere").json()["error"]["code"] == "NOT_FOUND"


class TestLogging:
    def _record(self, **extra: object) -> logging.LogRecord:
        record = logging.LogRecord("app.test", logging.INFO, __file__, 1, "hello %s", ("world",), None)
        for key, value in extra.items():
            setattr(record, key, value)
        ContextFilter().filter(record)
        return record

    def test_json_formatter_includes_context_and_redacts_secrets(self) -> None:
        with bind_job("job-1", "req-9"):
            record = self._record(fields={"features": 3, "password": "hunter2", "database_url": "postgres://x"})
        payload = json.loads(JsonFormatter().format(record))
        assert payload["msg"] == "hello world"
        assert payload["job_id"] == "job-1" and payload["request_id"] == "req-9"
        assert payload["features"] == 3
        assert payload["password"] == "***" and payload["database_url"] == "***"

    def test_context_is_reset_after_block(self) -> None:
        with bind_job("job-1", "req-9"):
            pass
        record = self._record()
        assert record.job_id is None  # type: ignore[attr-defined]

    def test_exceptions_are_captured(self) -> None:
        try:
            raise ValueError("bad")
        except ValueError:
            import sys

            record = logging.LogRecord("x", logging.ERROR, __file__, 1, "failed", (), sys.exc_info())
        ContextFilter().filter(record)
        payload = json.loads(JsonFormatter().format(record))
        assert payload["exc_type"] == "ValueError" and "bad" in payload["exc"]
        assert "failed" in ConsoleFormatter().format(record)


class TestSettings:
    def test_secret_url_is_not_rendered(self) -> None:
        settings = Settings(_env_file=None, database_url="postgresql+psycopg://u:topsecret@h/db")  # type: ignore[call-arg]
        assert "topsecret" not in repr(settings)
        assert "topsecret" in settings.sqlalchemy_url

    @pytest.mark.parametrize("schema", ["Public", "drop table", "1abc", "a-b"])
    def test_schema_must_be_identifier(self, schema: str) -> None:
        with pytest.raises(ValidationError):
            Settings(_env_file=None, db_schema=schema)  # type: ignore[call-arg]
