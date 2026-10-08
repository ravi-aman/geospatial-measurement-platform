# ADR-009: Stream with pyogrio + Shapely 2 + pyproj; GeoPandas only in tests

**Status:** Accepted

## Context
GeoPandas is the default tool for "read a Shapefile and compute areas" and the natural first choice. Two of its
defaults are wrong for an ingestion service:
* `geopandas.read_file()` reads **only the first layer**. A KML with folders is several GDAL layers; the sample mine
  site KML has four (one per folder) — reading it with defaults would return 4 of its 13 features and silently
  drop the other 9.
* A `GeoDataFrame` materialises the whole dataset in memory, so memory grows with file size.

## Decision
* Enumerate all layers with `pyogrio.list_layers` and stream each with `pyogrio.open_arrow` in batches of
  `PROCESSING_BATCH_SIZE` features (Arrow record batches; memory bounded by the batch for Shapefiles).
* Do the per-batch work with the libraries GeoPandas itself is built on: Shapely 2 vectorised functions
  (`from_wkb`, `transform`, `area`, `length`, `bounds`, `is_valid`, `make_valid`) and pyproj `Transformer`s.
* Keep GeoPandas as a **dev dependency**: it is an independent oracle in tests (`estimate_utm_crs`) and writes the
  synthetic sample Shapefiles.

## Trade-offs
* Slightly more code than a GeoDataFrame one-liner; in exchange every step is explicit, testable and streaming.
* GDAL's KML drivers still parse a whole KML document in memory — KML size is bounded by the upload limit.

## Consequences
* 100,000 polygons are processed at ~7,400 features/s in one process (performance test).
* The runtime image does not need pandas.
