from __future__ import annotations

import datetime as dt
from decimal import Decimal

from app.geoprocessing.properties import clean_properties, derive_name


def test_json_unsafe_values_are_normalised() -> None:
    raw = {
        "nan": float("nan"),
        "inf": float("inf"),
        "nul": "a\x00b",
        "bytes": b"\x01\x02",
        "when": dt.datetime(2026, 1, 2, 3, 4, 5),
        "day": dt.date(2026, 1, 2),
        "money": Decimal("1.25"),
        "nested": {"k\x00": [1, float("nan")]},
        "other": object,
    }
    cleaned, sanitized = clean_properties(raw, is_kml=False)
    assert sanitized
    assert cleaned["nan"] is None and cleaned["inf"] is None
    assert cleaned["nul"] == "ab"
    assert cleaned["bytes"] == {"$base64": "AQI="}
    assert cleaned["when"] == "2026-01-02T03:04:05"
    assert cleaned["day"] == "2026-01-02"
    assert cleaned["money"] == 1.25
    assert cleaned["nested"] == {"k": [1, None]}
    assert isinstance(cleaned["other"], str)


def test_clean_values_are_not_flagged() -> None:
    raw = {"a": 1, "b": "x", "c": None, "d": True, "e": 2.5}
    cleaned, sanitized = clean_properties(raw, is_kml=False)
    assert cleaned == raw and not sanitized


def test_shapefile_nulls_are_preserved() -> None:
    cleaned, _ = clean_properties({"owner": None, "area": 3}, is_kml=False)
    assert cleaned == {"owner": None, "area": 3}


def test_libkml_presentation_defaults_are_dropped_but_real_data_kept() -> None:
    raw = {
        "Name": "P1",
        "description": None,
        "tessellate": -1,
        "extrude": 0,
        "visibility": -1,
        "drawOrder": None,
        "altitudeMode": None,
        "timestamp": None,
        "owner": "Asha",
    }
    cleaned, sanitized = clean_properties(raw, is_kml=True)
    assert cleaned == {"Name": "P1", "owner": "Asha"}
    assert not sanitized


def test_explicit_non_default_kml_presentation_values_are_kept() -> None:
    cleaned, _ = clean_properties({"visibility": 0, "tessellate": 1, "extrude": 1}, is_kml=True)
    assert cleaned == {"visibility": 0, "tessellate": 1, "extrude": 1}


def test_derive_name_prefers_common_label_fields() -> None:
    assert derive_name({"Name": "  Plot 7 "}) == "Plot 7"
    assert derive_name({"title": "T", "id": 4}) == "T"
    assert derive_name({"ID": 42}) == "42"
    assert derive_name({"Name": "", "flag": True}) is None
    assert derive_name({"name": "x" * 500}) == "x" * 200
