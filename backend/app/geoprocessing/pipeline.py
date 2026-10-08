"""Dataset-level orchestration: layers -> CRS resolution -> batches -> records -> sink.

``process_dataset`` knows nothing about databases, queues or HTTP. Results are handed to a ``sink``
callback (the job service persists them; tests collect them in a list), and progress is reported through
``on_progress``. This keeps the geospatial core testable without the API (requirement: "processing logic
testable without the API").
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pyproj import CRS

from app.domain.enums import DatasetWarningCode, MeasurementStatus, SourceFormat
from app.domain.errors import DatasetError
from app.geoprocessing.crs import ResolvedCrs, resolve_crs
from app.geoprocessing.models import FeatureRecord, Issue
from app.geoprocessing.processor import FeatureProcessor, LayerContext
from app.geoprocessing.reader import describe_dataset, iter_batches
from app.geoprocessing.summary import SummaryAccumulator

RecordSink = Callable[[list[FeatureRecord]], None]
ProgressCallback = Callable[[int], None]
StartCallback = Callable[[int | None], None]


@dataclass(slots=True)
class DatasetResult:
    crs: ResolvedCrs | None
    summary: dict[str, Any]
    warnings: list[Issue] = field(default_factory=list)
    status_counts: dict[str, int] = field(default_factory=dict)

    @property
    def total_features(self) -> int:
        return int(self.summary["total_features"])


def _dedupe(issues: list[Issue]) -> list[Issue]:
    seen: set[tuple[str, str]] = set()
    unique: list[Issue] = []
    for issue in issues:
        key = (str(issue.code), issue.message)
        if key not in seen:
            seen.add(key)
            unique.append(issue)
    return unique


def process_dataset(
    path: Path,
    fmt: SourceFormat,
    *,
    crs_override: CRS | None,
    processor: FeatureProcessor,
    batch_size: int,
    max_features: int,
    sink: RecordSink,
    on_progress: ProgressCallback | None = None,
    on_start: StartCallback | None = None,
) -> DatasetResult:
    layers = describe_dataset(path)
    declared_total = sum(layer.feature_count for layer in layers if layer.feature_count > 0)
    if declared_total > max_features:
        raise DatasetError(
            "TOO_MANY_FEATURES", f"The dataset has {declared_total} features; the limit is {max_features}."
        )
    if on_start is not None:  # total is unknown (None) if any layer cannot report its count cheaply
        on_start(declared_total if all(layer.feature_count >= 0 for layer in layers) else None)

    accumulator = SummaryAccumulator()
    warnings: list[Issue] = []
    file_crs: ResolvedCrs | None = None
    index = 0
    for layer in layers:
        crs, crs_warnings = resolve_crs(layer.declared_crs, crs_override)
        warnings.extend(crs_warnings)
        file_crs = file_crs or crs
        ctx = LayerContext.create(layer.name, is_kml=fmt is SourceFormat.KML, crs=crs)
        accumulator.start_layer(layer.name, layer.driver)
        for batch in iter_batches(path, layer.name, batch_size):
            if index + len(batch) > max_features:
                raise DatasetError("TOO_MANY_FEATURES", f"The dataset exceeds the limit of {max_features} features.")
            records = processor.process(ctx, index, batch)
            sink(records)
            accumulator.add(records)
            index += len(records)
            if on_progress is not None:
                on_progress(index)

    warnings.extend(_summary_warnings(accumulator))
    return DatasetResult(
        crs=file_crs,
        summary=accumulator.snapshot(),
        warnings=_dedupe(warnings),
        status_counts={str(s): accumulator.count(s) for s in MeasurementStatus},
    )


def _summary_warnings(acc: SummaryAccumulator) -> list[Issue]:
    warnings: list[Issue] = []
    if acc.total_features == 0:
        warnings.append(Issue(DatasetWarningCode.NO_FEATURES, "The dataset contains no features."))
    if acc.features_with_z:
        warnings.append(
            Issue(
                DatasetWarningCode.Z_COORDINATES_IGNORED,
                f"{acc.features_with_z} feature(s) have elevation (Z) values. Measurements are planimetric (2D): "
                "horizontal area and length on the ellipsoid surface, not 3D surface area or slope length.",
            )
        )
    unsupported = acc.count(MeasurementStatus.UNSUPPORTED)
    if unsupported:
        warnings.append(
            Issue(
                DatasetWarningCode.UNSUPPORTED_FEATURES,
                f"{unsupported} feature(s) have geometry types that are not measured.",
            )
        )
    return warnings
