# `app/geoprocessing` — the geospatial core

Framework-free: imports nothing from FastAPI, SQLAlchemy or storage. Everything here runs in unit tests with
in-memory WKB or small files on disk.

| Module | Role |
|---|---|
| `reader.py` | enumerate **all** GDAL layers; stream each as Arrow batches (`pyogrio.open_arrow`) |
| `crs.py` | resolve the source CRS (override > declared > unknown); WGS 84 transformer (`always_xy`) |
| `geometry.py` | classify families, normalise collections, validate + repair |
| `projection.py` | measurement CRS per feature: local equal-area (default) or UTM |
| `measurement.py` | vectorised transform, planar area/length, geodesic reference, relative difference |
| `properties.py` | JSON-safe attributes, LIBKML noise removal, display name |
| `processor.py` | one batch → `FeatureRecord`s with per-feature failure isolation |
| `pipeline.py` | one dataset → layers → batches → sink; dataset-level errors and warnings |
| `summary.py` | totals, counts, bbox accumulated while streaming |
| `models.py` | `FeatureRecord`, `Issue` |

## 1. Why it exists
To turn an untrusted vector file into per-feature records — geometry, CRS, attributes, measurement and an explicit
status — correctly (CRS-aware, metric, validated) and with bounded memory, independent of how the result is stored
or served.

## 2. How it works
`process_dataset(path, format, crs_override, processor, batch_size, max_features, sink, on_progress)`:
for every layer → resolve CRS → stream batches → `FeatureProcessor.process()`:
decode WKB → checks (missing/malformed/empty/too complex) → transform to WGS 84 → lon/lat range check → normalise →
validate/repair → choose projection per feature → group → vectorised projection → area/length → geodesic reference →
records handed to `sink`. Details: [docs/geospatial](../../../docs/geospatial/).

## 3. Inputs
A local dataset path (KML or extracted `.shp`), the source format, an optional override CRS, a projection strategy and
limits. Paths come from the job executor's private temp directory — never from user input.

## 4. Outputs
`FeatureRecord` per feature (WKB in WGS 84 or raw, repaired WKB, attributes, status, area/perimeter/length, projected
CRS, geodesic reference, error/issue codes) and a `DatasetResult` (resolved CRS, summary, warnings, status counts).

## 5. Failure modes
* Per feature → status + code on the record, processing continues (see
  [geometry-handling.md](../../../docs/geospatial/geometry-handling.md)).
* Per dataset → `DatasetError` (`UNREADABLE_DATASET`, `TOO_MANY_FEATURES`) — the job fails permanently.
* Exceptions raised by the `sink` (lease lost, shutdown) propagate unchanged.

## 6. Design decisions
Per-feature local equal-area projection with a geodesic cross-check ([ADR-005](../../../docs/decisions/adr-005-crs-strategy.md));
repair with `MakeValid(structure)` keeping both geometries ([ADR-006](../../../docs/decisions/adr-006-geometry-repair.md));
streaming with pyogrio + Shapely 2 instead of GeoDataFrames ([ADR-009](../../../docs/decisions/adr-009-streaming-instead-of-geodataframes.md));
never guess a missing CRS.

## 7. Scaling considerations
Vectorised per batch; transformers cached per projection key (features in the same 1° cell share one);
~7,400 features/s for 100k polygons in one process. Memory is bounded by `batch_size` for Shapefiles; GDAL parses
KML in memory. Chunking a single huge file across workers is the next step ([scalability](../../../docs/architecture/scalability.md)).

## 8. Security considerations
Receives only files that passed `app/ingestion` checks (re-run in the worker). Attribute values are made JSON- and
PostgreSQL-safe. Vertex and feature limits bound CPU and memory. pyproj network access stays disabled (no remote
grids); CRS overrides are authority codes only.

## 9. Testing
`tests/unit/test_geo_*`: CRS precedence and parsing, axis order, UTM zone selection vs GeoPandas (Hypothesis),
LAEA centring, accuracy vs geodesics on several continents, Web Mercator trap, holes/multi-parts, repair cases,
property sanitisation, failure isolation, real KML (multi-folder) and Shapefile (projected, no `.prj`, override,
limits) through GDAL. `tests/performance/test_throughput.py`: 100k features.

## 10. Future improvements
Antimeridian splitting; 3D surface area/length from Z or a DEM; dissolved footprint (`ST_Union`) besides sums;
chunked processing of huge files; GeoPackage/FlatGeobuf/GeoParquet/KMZ inputs; densify sparse long edges before
measuring.
