"""Plain data objects produced by the processing pipeline (no ORM, no HTTP)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.domain.enums import FeatureCode, MeasurementStatus, ProjectionMethod


@dataclass(frozen=True, slots=True)
class Issue:
    code: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"code": str(self.code), "message": self.message}


@dataclass(slots=True)
class FeatureRecord:
    """Everything persisted for one feature. Mirrors the ``features`` table."""

    feature_index: int
    layer: str
    source_fid: int | None
    properties: dict[str, Any]
    name: str | None = None
    geometry_type: str | None = None
    has_z: bool = False
    vertex_count: int = 0
    # WKB (2D). Exactly one of these is set when the feature has usable coordinates:
    geom_wgs84: bytes | None = None  # original geometry in WGS 84 lon/lat
    geom_raw: bytes | None = None  # original coordinates when they cannot be placed in WGS 84
    geom_repaired: bytes | None = None  # repaired geometry actually measured (only when repaired)
    is_valid: bool | None = None
    validity_reason: str | None = None
    status: MeasurementStatus = MeasurementStatus.NOT_APPLICABLE
    area_m2: float | None = None
    perimeter_m: float | None = None
    length_m: float | None = None
    projected_crs: str | None = None
    projection_method: ProjectionMethod | None = None
    geodesic_area_m2: float | None = None
    geodesic_length_m: float | None = None
    error_code: str | None = None
    error_message: str | None = None
    issues: list[Issue] = field(default_factory=list)
    bbox_wgs84: tuple[float, float, float, float] | None = None  # transient: dataset extent summary

    @property
    def repaired(self) -> bool:
        return self.geom_repaired is not None

    def fail(self, code: FeatureCode, message: str) -> None:
        self.status = MeasurementStatus.FAILED
        self.error_code = str(code)
        self.error_message = message

    def unsupported(self, code: FeatureCode, message: str) -> None:
        self.status = MeasurementStatus.UNSUPPORTED
        self.error_code = str(code)
        self.error_message = message

    def add_issue(self, code: FeatureCode, message: str) -> None:
        self.issues.append(Issue(code, message))
