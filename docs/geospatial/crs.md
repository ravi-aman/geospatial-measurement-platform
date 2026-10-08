# CRS handling and projection strategy

> Code: [`app/geoprocessing/crs.py`](../../backend/app/geoprocessing/crs.py),
> [`app/geoprocessing/projection.py`](../../backend/app/geoprocessing/projection.py),
> [`app/geoprocessing/measurement.py`](../../backend/app/geoprocessing/measurement.py) ·
> Decision record: [ADR-005](../decisions/adr-005-crs-strategy.md)

## The problem in one paragraph

Geographic coordinates (EPSG:4326, longitude/latitude in degrees) are angles, not lengths. A degree of longitude
is 111.3 km at the equator, 97.7 km at 28.6°N (Delhi) and 0 km at the poles, so `polygon.area` on lon/lat
coordinates returns "square degrees" — a number with no physical meaning. Every measurement must therefore be made
on a **projected** (planar, metric) coordinate system. But every projection distorts something: shape, area,
distance or direction. Choosing *which* projection decides how wrong the answer is.

## Pipeline

```
source CRS ──resolve──▶ ResolvedCrs (or None)
   │                              │ None ─▶ features stored as raw coordinates, measurements FAILED (CRS_MISSING)
   ▼
transform to WGS 84 (always_xy)  ── used for storage, display, validity checks, geodesic reference
   │
   ▼
choose a local metric CRS per feature (strategy)  ── group features that share a CRS
   │
   ▼
vectorised transform ─▶ planar area / length (m², m)  ─┐
                                                       ├─▶ relative difference ─▶ HIGH_PROJECTION_DISTORTION if > 0.1 %
geodesic reference on the WGS 84 ellipsoid (Karney) ───┘
```

## 1. Resolving the source CRS

Precedence (`resolve_crs`):

1. **Client override** — optional `crs` form field on upload. Only authority codes are accepted
   (`EPSG:32643`, `ESRI:102100`): unambiguous, easy to validate, and — unlike free-form PROJ strings — they cannot
   reference external grid files. Compound CRSs (horizontal + vertical) are reduced to their horizontal part. If the
   file declares a different CRS, the file gets a `CRS_OVERRIDDEN` warning.
2. **Declared by the dataset** — the `.prj` of a Shapefile, parsed by GDAL/PROJ. ESRI-flavoured WKT is matched to its
   EPSG equivalent (`GCS_WGS_1984` → `EPSG:4326`) with `to_authority(min_confidence=70)`; definitions that match no
   authority code are kept as `CUSTOM` and their WKT is stored on the job.
3. **KML** — the OGC KML 2.2 specification fixes the CRS to WGS 84 longitude/latitude; GDAL reports `EPSG:4326` for
   every KML layer, so no special case is needed.
4. **Otherwise: unknown.** The file gets a `CRS_MISSING` (or `CRS_INVALID` / `CRS_UNSUPPORTED`) warning.

### Why a missing CRS is never guessed

A common shortcut is "if every coordinate fits in ±180/±90, assume EPSG:4326". It is unsafe: a local engineering
grid or a small projected extent can produce values like `(45.2, 12.8)` that fit those ranges perfectly and are
metres, not degrees. A silent guess turns an input problem into a wrong answer that nobody notices. Instead:

* the features are still extracted (geometry type, attributes, raw coordinates in `geom_raw` — never mislabelled
  as WGS 84, and returned as `raw_geometry` with `geometry_crs: null`);
* polygons and lines are marked `FAILED` with `CRS_MISSING`; points stay `NOT_APPLICABLE`;
* the file status is `COMPLETED_WITH_ERRORS`, and the warning tells the user to re-upload with `crs=EPSG:xxxx`.

A related safety net: if a file *declares* EPSG:4326 but its coordinates fall outside lon/lat bounds (typically
projected metres with a wrong `.prj`), the feature fails with `COORDINATES_OUT_OF_RANGE` instead of producing nonsense.

### Axis order

EPSG:4326 is officially defined as (latitude, longitude); GIS files store (longitude, latitude). Every pyproj
`Transformer` is created with `always_xy=True`, and a unit test asserts that UTM 43N's origin maps to (75°E, 0°).
Without this flag every coordinate would be silently swapped.

## 2. Choosing the measurement CRS

Two interchangeable strategies (`MEASUREMENT_STRATEGY`):

### `local_equal_area` (default): Lambert Azimuthal Equal-Area near each feature

* **Equal-area** means the projected area of *any* shape equals its area on the ellipsoid — so polygon areas are
  exact regardless of where the projection is centred (PROJ uses the ellipsoidal formulation via authalic latitude).
* Linear scale error grows only with the distance *d* from the centre, approximately `(d/R)²/8`: ~0.0008 % at 50 km,
  ~0.012 % at 200 km — smaller than UTM's in-zone error for any realistic survey feature.
* Defined everywhere, including the poles and across UTM zone boundaries.
* Centres are snapped to the centre of the 1° × 1° cell containing the feature's bounding-box centre, so all features
  in the same area share one CRS and one cached `Transformer` (performance, determinism). Features wider than 2° get
  their own centre so their distortion stays minimal.
* The CRS is reported as a reproducible PROJ string, e.g.
  `+proj=laea +lat_0=28.5 +lon_0=77.5 +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs` — paste it into QGIS to reproduce.

### `utm`: the UTM zone containing each feature

* Same rule as GeoPandas' `estimate_utm_crs()` (zone of the bounding-box centre) but **per feature** and
  vectorised. A property-based test compares the zone selection with `estimate_utm_crs()` on 150 random points.
* Familiar EPSG codes (`EPSG:32643`), but UTM is **conformal, not equal-area**: the scale factor is 0.9996 on the
  central meridian and ~1.001 at the zone edge, so areas are off by −0.08 % to +0.2 % inside the zone and more
  outside it.
* Undefined beyond 84°N / 80°S; such features fall back to the local equal-area projection.

### Why not `estimate_utm_crs()` on the whole file?

It picks **one** zone from the centre of the whole dataset's bounds. That is fine for a single site, but a dataset
spanning several zones (a national road network, a state-wide parcel layer) measures its outer features far outside
their zone, where distortion keeps growing. It also raises an error for polar data. Per-feature selection removes
both problems.

### Why never EPSG:3857 (Web Mercator)?

Web Mercator is a display projection. Its scale factor is `sec(latitude)`, so lengths are inflated by ~14 % and areas
by ~29 % at Delhi, and by orders of magnitude near the poles. A test (`test_web_mercator_would_overstate_area`)
pins this behaviour, and another (`test_web_mercator_source_is_not_measured_in_mercator`) proves that a *file
stored in* EPSG:3857 is still measured correctly: the source CRS is only used to place coordinates on the ellipsoid;
it is never used for measurement, even when it is already projected.

## 3. Measured accuracy

Reproduce with `python scripts/compare_projections.py` (from `backend/`). Values are the relative difference from
the ellipsoidal geodesic reference:

| Case | Geodesic reference | UTM | Local LAEA (default) | Web Mercator |
|---|---:|---:|---:|---:|
| 1 km square on UTM 43N central meridian (75E, 28N) | 1,090,040.8 m² | −0.0800 % | −0.0000 % | +28.8 % |
| 1 km square near the UTM zone edge (77.95E, 28N) | 1,090,040.8 m² | +0.1278 % | −0.0000 % | +28.8 % |
| 10 km square, Delhi (77.2E, 28.6N) | 108,403,878.1 m² | +0.0342 % | −0.0000 % | +30.2 % |
| 50 km square straddling UTM zones 43/44 | 2,447,557,911.7 m² | +0.1340 % | −0.0008 % | +29.3 % |
| 200 km square (4 vertices) | 43,600,944,182.8 m² | +0.0133 % | −0.0136 % | +28.8 % |
| 10 km square at 85N (outside UTM) | 87,269,545.6 m² | n/a (polar) | −0.0000 % | +13216.7 % |
| 10 km E-W line on the central meridian | 9,836.2 m | −0.0400 % | −0.0006 % | +13.2 % |
| 10 km E-W line near the zone edge | 9,836.2 m | +0.0639 % | −0.0005 % | +13.2 % |
| 100 km diagonal line | 95,581.7 m | +0.0260 % | −0.0002 % | +13.8 % |

A note on the 200 km square: with only four vertices, "the" area depends on how edges between vertices are
interpreted — straight lines in some projection, or geodesics on the ellipsoid. The 0.0136 % is that edge
interpretation, not projection error. For very large features, densifying vertices removes the ambiguity.

## 4. Per-feature quality check

Every measured feature also gets an independent **geodesic reference** computed directly on the WGS 84 ellipsoid with
Karney's algorithm (GeographicLib, via `pyproj.Geod`) — no projection involved. The API reports
`relative_difference = projected / geodesic − 1`, and a feature whose |difference| exceeds
`DISTORTION_WARNING_THRESHOLD` (0.1 %) gets a `HIGH_PROJECTION_DISTORTION` issue. This turns "I picked a good
projection" into evidence attached to every number. (The projected value remains the reported measurement because
the assignment requires projecting; the geodesic value is quality control.)

Two implementation details that matter: `pyproj.Geod` returns a *signed* area and adds holes unless they wind
opposite to the shell, so polygons are re-oriented (`shapely.orient_polygons`) first; and the reference is skipped
(`relative_difference: null`) for degenerate shapes where a ratio is meaningless.

## 5. Units and precision

* Areas in **square metres** (`area_m2`), lengths and perimeters in **metres** (`length_m`, `perimeter_m`). Units are
  part of the field names. The projected CRS is asserted to use metres before measuring.
* Measurements are **planimetric (2D)**: Z values are recorded (`has_z`) but ignored, and the file gets a
  `Z_COORDINATES_IGNORED` warning. 3D surface area is a different quantity (see future work).
* The database keeps full double precision; the API rounds to 0.01 m / 0.01 m² and reports `relative_difference`
  to three significant figures. Source coordinates rarely justify more (1e-7° ≈ 1 cm).

## Known limitations

* **Antimeridian:** a lon/lat polygon from 179.9° to −179.9° is ambiguous (it could span 0.2° or 359.8°). Such
  features are flagged `ANTIMERIDIAN_CROSSING_SUSPECTED`; the geodesic check exposes the disagreement. Splitting at
  ±180° (RFC 7946 §3.1.9) is future work.
* **Datum transformations** use PROJ's default pipeline with network grids disabled; for most datums the shift is
  metres and does not materially change area or length, but sub-metre datum accuracy is not claimed.
* **Very large features** (continental scale) are measured with small but non-zero LAEA linear distortion; the
  quality check reports it.
