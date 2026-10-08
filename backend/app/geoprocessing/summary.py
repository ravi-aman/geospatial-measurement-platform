"""Incremental dataset summary, accumulated while batches stream through.

Computing totals while streaming (instead of an aggregate query afterwards) keeps the file-info endpoint
O(1): the summary is stored once on the job row and read back as-is.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from app.domain.enums import MeasurementStatus
from app.geoprocessing.models import FeatureRecord

NO_GEOMETRY = "(none)"


@dataclass(slots=True)
class LayerStats:
    name: str
    driver: str
    feature_count: int = 0


@dataclass(slots=True)
class SummaryAccumulator:
    status_counts: Counter[str] = field(default_factory=Counter)
    geometry_types: Counter[str] = field(default_factory=Counter)
    error_counts: Counter[str] = field(default_factory=Counter)
    issue_counts: Counter[str] = field(default_factory=Counter)
    total_area_m2: float = 0.0
    total_length_m: float = 0.0
    measured_areal: int = 0  # totals are null (not 0) when nothing of that kind was measured
    measured_linear: int = 0
    features_with_z: int = 0
    repaired_features: int = 0
    total_features: int = 0
    bbox: list[float] | None = None
    layers: list[LayerStats] = field(default_factory=list)

    def start_layer(self, name: str, driver: str) -> None:
        self.layers.append(LayerStats(name, driver))

    def add(self, records: list[FeatureRecord]) -> None:
        for record in records:
            self.total_features += 1
            if self.layers:
                self.layers[-1].feature_count += 1
            self.status_counts[str(record.status)] += 1
            self.geometry_types[record.geometry_type or NO_GEOMETRY] += 1
            if record.error_code:
                self.error_counts[record.error_code] += 1
            for issue in record.issues:
                self.issue_counts[str(issue.code)] += 1
            if record.has_z:
                self.features_with_z += 1
            if record.repaired:
                self.repaired_features += 1
            if record.status is MeasurementStatus.MEASURED and record.area_m2 is not None:
                self.total_area_m2 += record.area_m2
                self.measured_areal += 1
            if record.status is MeasurementStatus.MEASURED and record.length_m is not None:
                self.total_length_m += record.length_m
                self.measured_linear += 1
            if record.bbox_wgs84 is not None:
                self._extend_bbox(record.bbox_wgs84)

    def _extend_bbox(self, box: tuple[float, float, float, float]) -> None:
        if self.bbox is None:
            self.bbox = list(box)
            return
        self.bbox = [
            min(self.bbox[0], box[0]),
            min(self.bbox[1], box[1]),
            max(self.bbox[2], box[2]),
            max(self.bbox[3], box[3]),
        ]

    def count(self, status: MeasurementStatus) -> int:
        return self.status_counts.get(str(status), 0)

    def snapshot(self) -> dict[str, Any]:
        return {
            "total_features": self.total_features,
            "status_counts": {str(s): self.count(s) for s in MeasurementStatus},
            "geometry_types": dict(self.geometry_types.most_common()),
            "total_area_m2": self.total_area_m2 if self.measured_areal else None,
            "total_length_m": self.total_length_m if self.measured_linear else None,
            "bbox": self.bbox,
            "features_with_z": self.features_with_z,
            "repaired_features": self.repaired_features,
            "error_counts": dict(self.error_counts.most_common()),
            "issue_counts": dict(self.issue_counts.most_common()),
            "layers": [
                {"name": layer.name, "driver": layer.driver, "feature_count": layer.feature_count}
                for layer in self.layers
            ],
        }
