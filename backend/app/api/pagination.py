"""Opaque cursors for keyset pagination.

A cursor is base64url(JSON) of the last row's sort key. It is opaque to clients (they pass it back as-is)
but not secret: tampering with it only changes which page the caller sees, never what they may access.
"""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from datetime import datetime
from typing import Any

from app.db.repositories import SortSpec
from app.domain.errors import InvalidRequestError


def _encode(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _decode(cursor: str) -> dict[str, Any]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode()))
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise InvalidRequestError("Malformed pagination cursor.", code="INVALID_CURSOR") from exc
    if not isinstance(payload, dict):
        raise InvalidRequestError("Malformed pagination cursor.", code="INVALID_CURSOR")
    return payload


def encode_feature_cursor(sort: SortSpec, feature_index: int) -> str:
    return _encode({"s": sort.token, "i": feature_index})


def decode_feature_cursor(cursor: str, sort: SortSpec) -> int:
    """Return the ``feature_index`` of the last row of the previous page."""
    payload = _decode(cursor)
    if payload.get("s") != sort.token:
        raise InvalidRequestError("The cursor was issued for a different sort order.", code="INVALID_CURSOR")
    index = payload.get("i")
    if not isinstance(index, int) or isinstance(index, bool) or index < 0:
        raise InvalidRequestError("Malformed pagination cursor.", code="INVALID_CURSOR")
    return index


def encode_file_cursor(created_at: datetime, file_id: uuid.UUID) -> str:
    return _encode({"t": created_at.isoformat(), "id": str(file_id)})


def decode_file_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    payload = _decode(cursor)
    try:
        return datetime.fromisoformat(payload["t"]), uuid.UUID(payload["id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidRequestError("Malformed pagination cursor.", code="INVALID_CURSOR") from exc
