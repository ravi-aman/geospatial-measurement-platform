# Measurements, features and vector tiles

Three read views over the per-feature results of a processed file:

| Endpoint | Format | Use |
|---|---|---|
| `GET /api/files/{file_id}/measurements/` | JSON, paginated | Tables and reports: one row per feature, no geometry |
| `GET /api/files/{file_id}/features/` and `.../features/{feature_id}/` | GeoJSON (RFC 7946), paginated | Geometry + attributes + measurement |
| `GET /api/files/{file_id}/tiles/{z}/{x}/{y}.mvt` | Mapbox Vector Tile | Map rendering (MapLibre) |

The method behind the numbers (projection choice, geodesic check) is described in
[../geospatial/measurements.md](../geospatial/measurements.md) and [../geospatial/crs.md](../geospatial/crs.md).
Validation and repair are in [../geospatial/geometry-handling.md](../geospatial/geometry-handling.md).

## Availability (409)

All of these endpoints first load the file (`404 FILE_NOT_FOUND`). They then require the job to have results:

| Job status | Response |
|---|---|
| `PENDING`, `PROCESSING` | `409 RESULTS_NOT_READY`, header `Retry-After: 2`, `details: {"status": "...", "retry_after_s": 2}` |
| `FAILED` | `409 PROCESSING_FAILED`, `details: {"status": "FAILED", "error_code": "...", "error_message": "..."}` (no `Retry-After`) |
| `COMPLETED`, `COMPLETED_WITH_ERRORS` | `200` (tiles: `200` or `204`) |

`GET /api/files/{file_id}/` never returns 409. Poll it for status, as described in [overview.md](overview.md#asynchronous-processing-model).

## `GET /api/files/{file_id}/measurements/`

| Query | Type | Default | Notes |
|---|---|---|---|
| `limit` | int >= 1 | 100 | Values above 500 are clamped to 500. `page.limit` reports the effective value. `0` gives `422`. |
| `cursor` | string | | `page.next_cursor` from the previous page. |
| `sort` | `feature_id` \| `area_m2` \| `length_m`, optional `-` prefix | `feature_id` | Anything else: `422 VALIDATION_ERROR`. See [Sorting](#sorting). |
| `status` | measurement status, repeatable | | `?status=FAILED&status=UNSUPPORTED`. Unknown values: `422`. |
| `geometry_type` | string, repeatable | | Exact, case-sensitive match on `geometry_type`, e.g. `Polygon`. |
| `layer` | string | | Exact match on `layer`. |

Repeated values of one filter are OR-ed; different filters are AND-ed. The same parameters apply to
`/features/`.

Response members: `file_id`, `status` (job status), `crs` (source CRS, as on the file), `units`
(always `{"area": "m2", "length": "m"}`), `items`, `page` (`limit`, `next_cursor`, `total`).

Captured `GET /api/files/474b0681-3c4f-4055-9de7-c7e77c5c5b2e/measurements/?limit=2`:

```jsonc
{
  "file_id": "474b0681-3c4f-4055-9de7-c7e77c5c5b2e",
  "status": "COMPLETED",
  "crs": "EPSG:32643",
  "units": {"area": "m2", "length": "m"},
  "items": [
    {
      "feature_id": 0,
      "layer": "plots_utm43n",
      "name": null,
      "geometry_type": "Polygon",
      "measurement_status": "MEASURED",
      "measurement": {
        "area_m2": 13006.72,
        "perimeter_m": 456.37,
        "length_m": null,
        "projected_crs": "+proj=laea +lat_0=28.5 +lon_0=77.5 +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs",
        "method": "LOCAL_EQUAL_AREA",
        "geodesic_area_m2": 13006.72,
        "geodesic_length_m": null,
        "relative_difference": -4.7e-11
      },
      "geometry_repaired": false,
      "validity_reason": null,
      "issues": [],
      "error": null
    }
    // … item for feature_id 1 trimmed (same values except "relative_difference": -4.38e-11)
  ],
  "page": {"limit": 2, "next_cursor": "eyJzIjoiZmVhdHVyZV9pZCIsImkiOjF9", "total": 6}
}
```

### Item fields

| Field | Type | Meaning |
|---|---|---|
| `feature_id` | int | 0-based index of the feature in the file, counted across all layers in read order. Same value as the GeoJSON `id` and the vector-tile feature id. |
| `layer` | string | GDAL layer name. Shapefile: the sanitised `.shp` stem. KML: one layer per folder/document as reported by GDAL. |
| `name` | string \| null | Label taken from the first non-empty attribute among `Name`, `name`, `NAME`, `title`, `Title`, `label`, `Label`, `id`, `ID`, `Id` (max. 200 characters). |
| `geometry_type` | string \| null | Geometry type as read from the source (`Polygon`, `MultiLineString`, `GeometryCollection`, ...), before any normalisation or repair. `null` if the feature has no geometry or it could not be decoded. |
| `measurement_status` | enum | `MEASURED`, `NOT_APPLICABLE`, `UNSUPPORTED`, `FAILED` ([codes](#measurement-status-and-codes)). |
| `measurement` | object \| null | Present only when `measurement_status` is `MEASURED`. |
| `geometry_repaired` | bool | `true` if the geometry was invalid and the repaired version was measured. |
| `validity_reason` | string \| null | GEOS reason when the geometry was invalid (e.g. `Self-intersection[...]`); `null` when valid or not validated. |
| `issues` | `[{code, message}]` | Informational issues. The feature may still be `MEASURED`. |
| `error` | `{code, message}` \| null | Reason for `FAILED` or `UNSUPPORTED`; `null` otherwise. |

### `measurement` object

| Field | Polygons | Lines | Meaning |
|---|---|---|---|
| `area_m2` | value | `null` | Planar area in `projected_crs`, square metres. |
| `perimeter_m` | value | `null` | Total boundary length including interior rings (holes), metres. |
| `length_m` | `null` | value | Length (sum of parts), metres. |
| `projected_crs` | string | string | CRS the feature was projected to: a PROJ string for Lambert Azimuthal Equal-Area (`+proj=laea +lat_0=28.5 +lon_0=77.5 ...`), or `EPSG:326xx` / `EPSG:327xx` for UTM. |
| `method` | enum | enum | `LOCAL_EQUAL_AREA` or `UTM`. With the `utm` strategy, features beyond 84N / 80S fall back to `LOCAL_EQUAL_AREA`. |
| `geodesic_area_m2` | value \| null | `null` | Area on the WGS 84 ellipsoid (Karney, `pyproj.Geod`), with no projection. Independent reference. |
| `geodesic_length_m` | `null` | value \| null | Same for length. |
| `relative_difference` | number \| null | number \| null | `projected / geodesic - 1`, see below. |

Polygons and multi-polygons get area and perimeter. Lines, multi-lines and rings get length. A repaired feature
is measured on its repaired geometry. A homogeneous `GeometryCollection` is measured as the equivalent
`Multi*`.

### Units and rounding

- Units are fixed and in the field names: square metres (`_m2`) and metres (`_m`). `units` repeats them.
- The database keeps full double precision. The API rounds `area_m2`, `perimeter_m`, `length_m`,
  `geodesic_area_m2`, `geodesic_length_m` and the summary totals to **2 decimal places** (0.01 m / 0.01 m²).
- `relative_difference` is computed from the **unrounded** values and rounded to **3 significant digits**
  (`-4.7e-11`, `0.00128`). JSON exponent notation is used for small values.
- Sorting uses the unrounded values. Vector tiles carry unrounded values.

### `relative_difference`

`projected_value / geodesic_value - 1`, for the area (polygons) or the length (lines). Positive means the
projected measurement is larger than the ellipsoidal reference. It is a per-feature, empirical bound on
projection distortion. The reported measurement remains the projected value; the geodesic value is quality
control. With the default equal-area projection, area differences are numerical noise (about `1e-11` in the
example above). Measured magnitudes for both strategies are in
[../geospatial/crs.md](../geospatial/crs.md#3-measured-accuracy). When `|relative_difference|` exceeds
`DISTORTION_WARNING_THRESHOLD` (0.001 = 0.1 %), the feature gets a `HIGH_PROJECTION_DISTORTION` issue. The
value is `null` when the geodesic reference could not be computed or is zero.

### Measurement status and codes

| `measurement_status` | Meaning | `measurement` | `error` |
|---|---|---|---|
| `MEASURED` | Area (polygonal) or length (linear) computed | object | `null` |
| `NOT_APPLICABLE` | Points and multi-points (also homogeneous point collections): nothing to measure | `null` | `null` |
| `UNSUPPORTED` | Geometry deliberately not measured | `null` | reason code |
| `FAILED` | A measurement should exist but could not be computed. Makes the job `COMPLETED_WITH_ERRORS`. | `null` | error code |

`error.code` for `FAILED`:

| Code | Meaning | Geometry returned |
|---|---|---|
| `GEOMETRY_MISSING` | The feature has no geometry (e.g. a KML Placemark without one). | none |
| `GEOMETRY_MALFORMED` | The geometry could not be decoded. | none |
| `GEOMETRY_EMPTY` | The geometry is empty. | none |
| `GEOMETRY_TOO_COMPLEX` | More vertices than `MAX_VERTICES_PER_FEATURE` (1,000,000). | none |
| `CRS_MISSING` | The dataset has no usable CRS, so a polygon/line cannot be converted to metres. Re-upload with `crs`. | `raw_geometry` |
| `TRANSFORM_FAILED` | Transforming to WGS 84 produced non-finite coordinates. | `raw_geometry` |
| `COORDINATES_OUT_OF_RANGE` | After transformation the coordinates fall outside lon ±180 / lat ±90; the declared CRS is probably wrong. Applies to points too. | `raw_geometry` |
| `GEOMETRY_INVALID_UNREPAIRABLE` | Invalid, and repair produced nothing of the right dimension. | `geometry` |
| `MEASUREMENT_FAILED` | The projected measurement was not a finite number. | `geometry` |

`error.code` for `UNSUPPORTED`: `MIXED_GEOMETRY_COLLECTION` (a `GeometryCollection` mixing points, lines
and/or polygons: no single measure) and `UNSUPPORTED_GEOMETRY_TYPE` (a type with no measurement rule).

`issues[].code` (informational):

| Code | Meaning |
|---|---|
| `GEOMETRY_REPAIRED` | Invalid geometry repaired before measuring (GEOS `make_valid`; polygons with the `structure` method). The message includes the GEOS reason. The original stays in `geometry`; the measured one is `repaired_geometry`. |
| `COLLECTION_NORMALIZED` | Homogeneous `GeometryCollection` measured as `MultiPolygon` / `MultiLineString` / `MultiPoint`. |
| `HIGH_PROJECTION_DISTORTION` | Absolute `relative_difference` above the threshold (0.1 % by default). |
| `ANTIMERIDIAN_CROSSING_SUSPECTED` | The geometry spans more than 180° of longitude; it may cross the antimeridian. |
| `PROPERTIES_SANITIZED` | Some attribute values were altered to be storable as JSON: NaN/Infinity become `null`, NUL characters are stripped, bytes become `{"$base64": ...}`, other types become strings. |

### Sorting

| `sort` | `ORDER BY` |
|---|---|
| `feature_id` / `-feature_id` | `feature_id` ascending / descending |
| `area_m2` / `-area_m2` | `area_m2 ASC` (or `DESC`) `NULLS LAST, feature_id ASC` |
| `length_m` / `-length_m` | `length_m ASC` (or `DESC`) `NULLS LAST, feature_id ASC` |

**Nulls last in both directions.** Features without that measure (another geometry family, `FAILED`,
`NOT_APPLICABLE`, `UNSUPPORTED`) follow all measured features, in `feature_id` order. Example: with
`sort=-area_m2`, the largest polygon comes first and lines, points and failed features come last. Ties are
broken by `feature_id` ascending, also for descending sorts. Area and length sorts use the
`(job_id, area_m2)` and `(job_id, length_m)` indexes. The default order uses the primary key.

### Pagination walk-through

The cursor is base64url (URL-safe, no padding) and is bound to the `sort` it was issued with. Pass the same
filters on every page. Background: [overview.md](overview.md#pagination).

```bash
FILE=474b0681-3c4f-4055-9de7-c7e77c5c5b2e
curl -sS "$BASE/api/files/$FILE/measurements/?limit=2"
#   items: feature_id 0, 1    page: {"limit": 2, "next_cursor": "eyJzIjoiZmVhdHVyZV9pZCIsImkiOjF9", "total": 6}
curl -sS "$BASE/api/files/$FILE/measurements/?limit=2&cursor=eyJzIjoiZmVhdHVyZV9pZCIsImkiOjF9"
#   items: feature_id 2, 3    page.total: null (only the first page counts), next_cursor: non-null
#   third page: items 4, 5 and next_cursor: null -> done
```

Fetching everything (requires `jq`), e.g. measured features by descending area:

```bash
url="$BASE/api/files/$FILE/measurements/?limit=500&sort=-area_m2&status=MEASURED"
cursor=""
while :; do
  page=$(curl -sS "$url${cursor:+&cursor=$cursor}")
  echo "$page" | jq -c '.items[] | {feature_id, area_m2: .measurement.area_m2}'
  cursor=$(echo "$page" | jq -r '.page.next_cursor // empty')
  [ -z "$cursor" ] && break
done
```

Changing `sort` while reusing a cursor gives `400 INVALID_CURSOR` ("The cursor was issued for a different
sort order.").

## `GET /api/files/{file_id}/features/`

Features as an RFC 7946 `FeatureCollection`, with the same query parameters as `/measurements/`. The default
`limit` is 100, and values above 200 are clamped to 200. Served as `application/json`. Captured `?limit=1`:

```json
{
  "type": "FeatureCollection",
  "file_id": "474b0681-3c4f-4055-9de7-c7e77c5c5b2e",
  "source_crs": "EPSG:32643",
  "features": [
    {
      "type": "Feature",
      "id": 0,
      "geometry": {
        "type": "Polygon",
        "coordinates": [[[77.2102, 28.6139], [77.209, 28.6139], [77.209, 28.6149], [77.2102, 28.6149], [77.2102, 28.6139]]]
      },
      "properties": {"owner": "Owner A", "landuse": "agri", "plot_no": 1},
      "layer": "plots_utm43n",
      "source_fid": 0,
      "name": null,
      "geometry_type": "Polygon",
      "geometry_crs": "EPSG:4326",
      "raw_geometry": null,
      "repaired_geometry": null,
      "geometry_repaired": false,
      "validity_reason": null,
      "measurement_status": "MEASURED",
      "measurement": {
        "area_m2": 13006.72,
        "perimeter_m": 456.37,
        "length_m": null,
        "projected_crs": "+proj=laea +lat_0=28.5 +lon_0=77.5 +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs",
        "method": "LOCAL_EQUAL_AREA",
        "geodesic_area_m2": 13006.72,
        "geodesic_length_m": null,
        "relative_difference": -4.7e-11
      },
      "issues": [],
      "error": null
    }
  ],
  "page": {"limit": 1, "next_cursor": "eyJzIjoiZmVhdHVyZV9pZCIsImkiOjB9", "total": 6}
}
```

The source file is in UTM 43N (`source_crs`). `geometry` is still in WGS 84 longitude/latitude, as RFC 7946
requires.

| Member | Meaning |
|---|---|
| `id` | `feature_id` (integer). |
| `geometry` | The feature's original geometry, transformed to WGS 84 lon/lat. It is 2D (Z dropped) with at most 9 decimal places (`ST_AsGeoJSON(geom, 9)`). Not the repaired geometry. `null` when the coordinates cannot be placed in WGS 84 (RFC 7946 allows unlocated features). |
| `properties` | Source attributes after normalisation ([rules](../geospatial/supported-formats.md#attribute-normalisation-both-formats)). Stored as JSONB, so key order is not preserved. |
| `geometry_crs` | `"EPSG:4326"` when `geometry` is non-null, else `null`. |
| `raw_geometry` | GeoJSON-shaped coordinates **in the source file's units, with no CRS**. Only set when the coordinates could not be placed in WGS 84. Never use it as WGS 84. |
| `repaired_geometry` | Always `null` in this list. Populated by the single-feature endpoint. |
| `source_fid` | Feature id in the source layer as reported by GDAL. |
| `layer`, `name`, `geometry_type`, `geometry_repaired`, `validity_reason`, `measurement_status`, `measurement`, `issues`, `error` | As in [measurements](#item-fields). |

All members other than `type`, `id`, `geometry` and `properties` are foreign members (RFC 7946 §6.1), and
generic GeoJSON readers ignore them. The same applies to `file_id`, `source_crs` and `page` on the collection.

| Situation | `geometry` | `geometry_crs` | `raw_geometry` | `repaired_geometry` (single feature) |
|---|---|---|---|---|
| CRS known, coordinates valid | original, in WGS 84 | `EPSG:4326` | `null` | `null` |
| Invalid polygon/line, repaired (`GEOMETRY_REPAIRED`) | original (invalid), in WGS 84 | `EPSG:4326` | `null` | geometry that was measured |
| No usable CRS (`CRS_MISSING`, also points and mixed collections of such a file) | `null` | `null` | original coordinates | `null` |
| `TRANSFORM_FAILED`, `COORDINATES_OUT_OF_RANGE` | `null` | `null` | original coordinates | `null` |
| `GEOMETRY_MISSING`, `_MALFORMED`, `_EMPTY`, `_TOO_COMPLEX` | `null` | `null` | `null` | `null` |

## `GET /api/files/{file_id}/features/{feature_id}/`

Returns one `Feature` with the same members, plus `repaired_geometry` when the measured geometry differs from
the original. `feature_id` must be an integer >= 0 (`422` otherwise). Errors: `404 FILE_NOT_FOUND`, `409` as
above, then `404 FEATURE_NOT_FOUND` if the index does not exist in the file. Example from the API tests: a
self-intersecting "bow-tie" polygon returns `geometry.type: "Polygon"` (as submitted) and
`repaired_geometry.type: "MultiPolygon"` (what was measured).

## `GET /api/files/{file_id}/tiles/{z}/{x}/{y}.mvt`

A Mapbox Vector Tile (XYZ scheme, Web Mercator) of the file's features, built in PostGIS with
`ST_AsMVTGeom` and `ST_AsMVT`.

```bash
curl -sS -o tile.mvt -w '%{http_code}\n' "$BASE/api/files/$FILE/tiles/12/2926/1707.mvt"
```

| Aspect | Behaviour |
|---|---|
| Coordinates | `0 <= z <= 22`, `0 <= x, y < 2^z`. Otherwise `400 INVALID_TILE`, checked before the file lookup. Non-integers: `422`. |
| Availability | `404 FILE_NOT_FOUND`, `409` as [above](#availability-409). |
| `200` | `Content-Type: application/vnd.mapbox-vector-tile`. One layer named `features`, extent 4096, buffer 64, geometries clipped to the tile. |
| `204` | No feature in the tile; empty body. |
| Caching (`200` and `204`) | `Cache-Control: public, max-age=86400` and `ETag: "<job id hex>-<z>-<x>-<y>"`. A finished job's results never change. A request with a matching `If-None-Match` gets `304 Not Modified` without re-rendering the tile. |
| Feature id | MVT feature id = `feature_id` (same as `id` / `feature_id` elsewhere). |
| Properties | `name`, `geometry_type`, `status` (the `measurement_status`), `area_m2`, `length_m`. SQL `NULL` values are not encoded, so e.g. a line has no `area_m2` property. `area_m2` / `length_m` are unrounded. |
| Which features | Features with a WGS 84 `geometry` whose bounding box intersects the tile. The original geometry is drawn, not the repaired one. Features with only `raw_geometry` never appear. Latitudes are clipped to ±85.05112878° before projecting to EPSG:3857. |
| Cap | At most `TILE_MAX_FEATURES` (20,000) per tile, largest first: `area_m2 DESC NULLS LAST, length_m DESC NULLS LAST, feature_id`. Geometries that collapse to nothing at the tile's resolution are dropped after the cap, so a capped tile can hold fewer features. |
| Filters | None. The bundled frontend filters with MapLibre layer filters on the `status` and `geometry_type` properties. |

The tile URL template is also given as `links.tiles` in the file resource. Use `features` as the
`source-layer` in MapLibre.
