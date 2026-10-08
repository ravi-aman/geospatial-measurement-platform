"""Streaming access to vector datasets through GDAL (pyogrio).

Why not ``geopandas.read_file``? It materialises the whole dataset as one DataFrame, and by default reads
only the *first layer* - a KML with several ``<Folder>`` elements is several GDAL layers, so the rest would
be silently dropped. Instead we:

* enumerate **every** layer (``pyogrio.list_layers``), and
* stream each layer as Arrow record batches (``pyogrio.open_arrow``), so memory is bounded by
  ``batch_size`` rather than by the file - true for Shapefiles. (GDAL's KML drivers parse the whole
  document in memory, so KML size is bounded by the upload limit instead.)
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyogrio
from pyogrio.errors import DataLayerError, DataSourceError

from app.domain.errors import DatasetError

_DEFAULT_GEOMETRY_COLUMN = "wkb_geometry"


@dataclass(frozen=True, slots=True)
class LayerDescriptor:
    name: str
    driver: str
    declared_crs: str | None  # "EPSG:xxxx" or WKT as reported by GDAL; None when undeclared
    feature_count: int  # -1 when unknown
    geometry_type: str | None
    fields: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RawBatch:
    fids: list[int | None]
    wkb: list[bytes | None]
    properties: list[dict[str, Any]]

    def __len__(self) -> int:
        return len(self.wkb)


def describe_dataset(path: Path) -> list[LayerDescriptor]:
    try:
        layers = pyogrio.list_layers(path)
        descriptors = []
        for name, geometry_type in layers.tolist():
            info = pyogrio.read_info(path, layer=name, force_feature_count=True)
            descriptors.append(
                LayerDescriptor(
                    name=str(name),
                    driver=str(info.get("driver") or "unknown"),
                    declared_crs=info.get("crs") or None,
                    feature_count=int(info.get("features", -1)),
                    geometry_type=str(geometry_type) if geometry_type else None,
                    fields=tuple(str(f) for f in info.get("fields", ())),
                )
            )
        return descriptors
    except (DataSourceError, DataLayerError) as exc:
        raise DatasetError("UNREADABLE_DATASET", f"GDAL could not read the dataset: {exc}") from exc


def iter_batches(path: Path, layer: str, batch_size: int) -> Iterator[RawBatch]:
    """Yield the layer's features in batches of at most ``batch_size``."""
    try:
        with pyogrio.open_arrow(path, layer=layer, batch_size=batch_size, return_fids=True, use_pyarrow=True) as (
            meta,
            reader,
        ):
            geometry_column = meta.get("geometry_name") or _DEFAULT_GEOMETRY_COLUMN
            fid_column = meta.get("fid_column") or "OGC_FID"
            for record_batch in reader:
                columns = record_batch.to_pydict()
                wkb = columns.pop(geometry_column, [None] * record_batch.num_rows)
                fids = columns.pop(fid_column, [None] * record_batch.num_rows)
                names = list(columns)
                properties = [{name: columns[name][row] for name in names} for row in range(record_batch.num_rows)]
                yield RawBatch(fids=list(fids), wkb=list(wkb), properties=properties)
    except (DataSourceError, DataLayerError) as exc:
        raise DatasetError("UNREADABLE_DATASET", f"GDAL failed while reading layer '{layer}': {exc}") from exc
