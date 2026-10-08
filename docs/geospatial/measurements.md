# Measurements

> Code: [`app/geoprocessing/processor.py`](../../backend/app/geoprocessing/processor.py),
> [`app/geoprocessing/measurement.py`](../../backend/app/geoprocessing/measurement.py) ·
> CRS strategy: [crs.md](crs.md)

## What is measured

| Geometry type (after normalisation) | Measurement | Fields | `measurement_status` |
|---|---|---|---|
| Polygon, MultiPolygon | area, perimeter | `area_m2`, `perimeter_m` | `MEASURED` |
| LineString, MultiLineString, LinearRing | length | `length_m` | `MEASURED` |
| Point, MultiPoint | none (the assignment requires none) | — | `NOT_APPLICABLE` |
| GeometryCollection of one kind (e.g. only polygons) | as the equivalent Multi* type | as above | `MEASURED` + `COLLECTION_NORMALIZED` issue |
| GeometryCollection mixing kinds (point + line …) | none | — | `UNSUPPORTED` (`MIXED_GEOMETRY_COLLECTION`) |
| any other type | none | — | `UNSUPPORTED` (`UNSUPPORTED_GEOMETRY_TYPE`) |

Definitions:

* **Area** — planimetric area of the polygon, holes subtracted. For a MultiPolygon, the sum of its parts (a valid
  MultiPolygon cannot overlap itself; invalid overlapping parts are dissolved by repair, so nothing is counted twice).
* **Perimeter** — total boundary length, *including* the boundaries of holes (e.g. a pit with a water sump has the
  sump's edge in its perimeter).
* **Length** — total length of all parts of a (Multi)LineString.

Why MultiPolygon / MultiLineString are supported rather than rejected: KML `<MultiGeometry>` and multipart Shapefile
records are ordinary in real survey data (a plot split by a road, a conveyor with two segments). Their measure is
well defined, so rejecting them would only push work onto users.

Why mixed GeometryCollections are not measured: "the area of a point and a line" has no meaning, and summing only the
polygonal part would silently drop the other members. The feature is stored and displayed; it is reported as
`UNSUPPORTED` with an explicit reason.

## Calculation flow (per batch)

1. Decode WKB for the whole batch (vectorised); missing, malformed and empty geometries fail with explicit codes.
2. Transform the original geometry to WGS 84 (2D) and check it lies within lon/lat bounds.
3. Normalise collections, validate, repair if needed ([geometry-handling.md](geometry-handling.md)).
4. Choose the local projected CRS for every measurable feature; group features that share it.
5. One vectorised `shapely.transform` per group with a cached pyproj `Transformer`; then `shapely.area` /
   `shapely.length` on the projected geometries.
6. Compute the geodesic reference and the relative difference; flag `HIGH_PROJECTION_DISTORTION` above 0.1 %.

Steps 1, 2, 5 work on whole arrays of geometries — no Python loop over coordinates — which is what keeps the pipeline
at ~7,400 features/s ([scalability](../architecture/scalability.md)).

## Totals

The file summary contains `total_area_m2` and `total_length_m`: the **sum** of the measured features of that kind.
They are `null` (not `0`) when nothing of that kind was measured — `0 m²` would claim the polygons have no area.

They are sums, not a dissolved footprint: in the sample mine site, the lease boundary *contains* the pits, so the
sum counts the pit areas twice. The UI therefore labels them "Sum of polygon areas" / "Sum of line lengths".
A dissolved "covered area" is a different question (future work: `ST_Union`-based footprint).

## Presentation

* API values: metres / square metres rounded to 0.01; `relative_difference` to 3 significant figures; the database
  stores full doubles. Rounding happens only at the API boundary (`app/api/presenters.py`).
* The UI chooses a readable unit (m² → ha → km², m → km) but always shows the exact SI value underneath.

## Verification

| What | How |
|---|---|
| Correct magnitudes | 100 m × 100 m in UTM = 10,000 m² exactly; 0.1° of longitude at 28°N ≈ 9,836 m |
| Projected ≈ ellipsoidal truth | parametrised tests over 4 continents, both strategies, tolerance 1e-6 (LAEA) / 2e-3 (UTM) |
| Holes, multi-parts | perimeter includes holes; MultiPolygon = sum of parts |
| Traps | Web Mercator overstates area ≥ 28 % at Delhi; "square degrees" are not area |
| End to end | API tests assert `area_m2` of known squares through upload → worker → PostGIS → JSON |
