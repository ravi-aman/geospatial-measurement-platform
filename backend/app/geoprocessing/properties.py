"""Attribute (properties) normalisation for JSONB storage.

Attributes are preserved, not curated. The only transformations are the ones required for correctness:

* values must be JSON-serialisable (dates -> ISO 8601, bytes -> base64, Decimal -> float);
* NaN / Infinity are not valid JSON -> ``null``;
* PostgreSQL text/JSONB cannot contain NUL (``\\u0000``) -> stripped;
* KML via GDAL's LIBKML driver: the driver adds ~12 presentation fields (``tessellate``, ``visibility``,
  ``drawOrder`` ...) to every feature. When they hold the driver's "unset" sentinel they carry no user data
  and are dropped. KML has no fixed schema, so ``null`` values (field absent on that placemark) are dropped
  too. Shapefile attributes keep their ``null`` values because the DBF schema is fixed.

Whenever a value had to be altered the feature gets a ``PROPERTIES_SANITIZED`` issue.
"""

from __future__ import annotations

import base64
import datetime as dt
import math
from decimal import Decimal
from typing import Any

_LIBKML_UNSET: dict[str, object] = {"tessellate": -1, "extrude": 0, "visibility": -1}
_LIBKML_OPTIONAL = frozenset({"id", "timestamp", "begin", "end", "altitudeMode", "drawOrder", "icon"})
_NAME_KEYS = ("Name", "name", "NAME", "title", "Title", "label", "Label", "id", "ID", "Id")
MAX_NAME_LENGTH = 200


class _Sanitizer:
    def __init__(self) -> None:
        self.changed = False

    def value(self, value: Any) -> Any:
        if value is None or isinstance(value, bool | int):
            return value
        if isinstance(value, float):
            if math.isfinite(value):
                return value
            self.changed = True
            return None
        if isinstance(value, str):
            if "\x00" in value:
                self.changed = True
                return value.replace("\x00", "")
            return value
        if isinstance(value, dt.datetime | dt.date | dt.time):
            return value.isoformat()
        if isinstance(value, Decimal):
            return self.value(float(value))
        if isinstance(value, bytes | bytearray | memoryview):
            self.changed = True
            return {"$base64": base64.b64encode(bytes(value)).decode("ascii")}
        if isinstance(value, dict):
            return {self.key(k): self.value(v) for k, v in value.items()}
        if isinstance(value, list | tuple):
            return [self.value(v) for v in value]
        self.changed = True
        return str(value)

    def key(self, key: Any) -> str:
        text = str(key)
        if "\x00" in text:
            self.changed = True
            text = text.replace("\x00", "")
        return text


def clean_properties(raw: dict[str, Any], *, is_kml: bool) -> tuple[dict[str, Any], bool]:
    """Return ``(properties, sanitized)``; ``sanitized`` is True if any value had to be altered."""
    sanitizer = _Sanitizer()
    cleaned: dict[str, Any] = {}
    for key, value in raw.items():
        if is_kml:
            if value is None or value == "":
                continue
            if key in _LIBKML_UNSET and value == _LIBKML_UNSET[key]:
                continue
            if key in _LIBKML_OPTIONAL and value in (None, ""):
                continue
        cleaned[sanitizer.key(key)] = sanitizer.value(value)
    return cleaned, sanitizer.changed


def derive_name(properties: dict[str, Any]) -> str | None:
    """Best-effort human label for a feature (KML ``<name>``, common attribute names)."""
    for key in _NAME_KEYS:
        value = properties.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:MAX_NAME_LENGTH]
        if isinstance(value, int | float) and not isinstance(value, bool):
            return str(value)[:MAX_NAME_LENGTH]
    return None
