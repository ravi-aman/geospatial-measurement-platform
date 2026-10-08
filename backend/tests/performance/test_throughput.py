"""Throughput checks on synthetic datasets (``pytest -m slow``).

The floors are deliberately conservative (CI machines are slow and shared); the printed numbers are what
docs/architecture/scalability.md reports. They guard against accidental de-vectorisation (e.g. a per-feature
Transformer or a Python loop over coordinates), which would show up as an order-of-magnitude drop.
"""

from __future__ import annotations

import time
from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
import shapely

from app.domain.enums import MeasurementStatus, SourceFormat
from app.geoprocessing.models import FeatureRecord
from app.geoprocessing.pipeline import process_dataset
from app.geoprocessing.processor import FeatureProcessor
from app.geoprocessing.projection import LocalEqualAreaStrategy, UtmStrategy

pytestmark = pytest.mark.slow


def synthetic_parcels(path: Path, count: int, *, spread_deg: float = 4.0) -> Path:
    rng = np.random.default_rng(42)
    lon = 75.0 + rng.random(count) * spread_deg
    lat = 20.0 + rng.random(count) * spread_deg
    size = 0.0005 + rng.random(count) * 0.002
    geoms = shapely.box(lon, lat, lon + size, lat + size)
    frame = gpd.GeoDataFrame({"parcel_id": np.arange(count), "owner": ["synthetic"] * count}, geometry=geoms, crs=4326)
    shp = path / "parcels.shp"
    frame.to_file(shp, engine="pyogrio")
    return shp


@pytest.mark.parametrize("strategy", [LocalEqualAreaStrategy(), UtmStrategy()], ids=lambda s: s.name)
def test_pipeline_throughput_100k_features(tmp_path: Path, strategy: object) -> None:
    count = 100_000
    shp = synthetic_parcels(tmp_path, count)
    measured = 0

    def sink(records: list[FeatureRecord]) -> None:
        nonlocal measured
        measured += sum(1 for r in records if r.status is MeasurementStatus.MEASURED)

    started = time.perf_counter()
    result = process_dataset(
        shp,
        SourceFormat.SHAPEFILE,
        crs_override=None,
        processor=FeatureProcessor(strategy),  # type: ignore[arg-type]
        batch_size=5_000,
        max_features=count,
        sink=sink,
    )
    elapsed = time.perf_counter() - started
    rate = count / elapsed
    print(f"\n[{strategy.name}] {count} features in {elapsed:.1f}s -> {rate:,.0f} features/s")  # type: ignore[attr-defined]
    assert result.total_features == count and measured == count
    assert rate > 1_000, f"throughput regression: {rate:.0f} features/s"
