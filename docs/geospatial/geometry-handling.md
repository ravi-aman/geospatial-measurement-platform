# Geometry handling: validation, repair, failure isolation

> Code: [`app/geoprocessing/geometry.py`](../../backend/app/geoprocessing/geometry.py),
> [`app/geoprocessing/processor.py`](../../backend/app/geoprocessing/processor.py)

## Principle: one bad feature never sinks the file

Every problem with an individual feature becomes a status and a machine-readable code **on that feature**, and
processing continues with the next one. Only problems with the *dataset itself* (unreadable file, unsafe archive,
too many features) fail the job. A file with at least one failed feature finishes as `COMPLETED_WITH_ERRORS`, and its
attributes are kept even for failed features.

## Decision table

| Situation | Status | Code | Stored geometry |
|---|---|---|---|
| Record has no geometry (Shapefile null shape, KML placemark without geometry) | FAILED | `GEOMETRY_MISSING` | none |
| WKB cannot be decoded | FAILED | `GEOMETRY_MALFORMED` | none |
| Geometry is empty | FAILED | `GEOMETRY_EMPTY` | none |
| More than `MAX_VERTICES_PER_FEATURE` (1,000,000) vertices | FAILED | `GEOMETRY_TOO_COMPLEX` | none |
| CRS unknown | FAILED (points: NOT_APPLICABLE) | `CRS_MISSING` | raw coordinates (`geom_raw`) |
| Transform to WGS 84 produced non-finite coordinates | FAILED | `TRANSFORM_FAILED` | raw coordinates |
| Coordinates outside lon/lat bounds after transform | FAILED | `COORDINATES_OUT_OF_RANGE` | raw coordinates |
| Mixed GeometryCollection | UNSUPPORTED | `MIXED_GEOMETRY_COLLECTION` | WGS 84 |
| Invalid polygon, repairable | MEASURED | issue `GEOMETRY_REPAIRED` | original + repaired |
| Invalid polygon, nothing areal left after repair | FAILED | `GEOMETRY_INVALID_UNREPAIRABLE` | original |
| Point / MultiPoint | NOT_APPLICABLE | — | WGS 84 |

## Validation and repair

Validity follows the OGC Simple Features rules as implemented by GEOS (`shapely.is_valid`). For invalid geometries
the GEOS reason is recorded verbatim (`shapely.is_valid_reason`, e.g. `Self-intersection[77.005 28.005]`).

Repair uses GEOS `MakeValid`:

* **Polygons: `method="structure"`, `keep_collapsed=False`.** The structure method rebuilds rings, unions shells and
  subtracts holes — the interpretation that matches what a surveyor intended. Parts that collapse to a lower
  dimension (a sliver that becomes a line) are dropped because they have no area. Overlapping MultiPolygon parts are
  dissolved, so area is never double counted.
* **Lines: the default `linework` method**, then only the linear parts are kept. In practice the only invalid lines
  are degenerate ones (all vertices identical), which collapse to a point and are reported as unrepairable.

Example — a "bow-tie" (a quadrilateral digitised with crossing edges) becomes a MultiPolygon of two triangles:

```
input  POLYGON((0 0, 1 1, 1 0, 0 1, 0 0))         is_valid = false, reason = "Self-intersection[0.5 0.5]"
output MULTIPOLYGON(((0 0, 0.5 0.5, 0 1, 0 0)), ((1 0, 0.5 0.5, 1 1, 1 0)))   area = 0.5
```

### Original vs. repaired: nothing is silently changed

* `features.geom` stores the geometry **as submitted** (in WGS 84), invalid or not; it is what the map and the
  GeoJSON `geometry` show.
* `features.geom_repaired` stores the geometry **actually measured**, only when a repair happened (rare, so the extra
  column costs almost nothing). `GET /api/files/{id}/features/{n}/` returns it as `repaired_geometry`.
* The feature carries `geometry_repaired: true`, `validity_reason`, and a `GEOMETRY_REPAIRED` issue; the file summary
  counts `repaired_features`.
* An integration test asks PostGIS for an independent opinion: `ST_IsValid(geom) = false`,
  `ST_IsValid(geom_repaired) = true`.

### Consequences of repairing (documented trade-off)

Repair changes the measured shape. For a bow-tie, "the area of the two triangles" is one plausible reading of an
ambiguous drawing — another reading is "the user meant a rectangle and clicked vertices out of order", which no
algorithm can recover. Repairing (rather than rejecting) is chosen because most real-world invalidity is minor
(tiny self-touching rings, duplicate vertices) and rejection would discard otherwise valid data; flagging every
repair keeps the decision visible to the user. A strict mode ("reject invalid geometries") would be a small setting
if a customer needs it.

## Normalisation of collections

`GeometryCollection`s whose members are all of one kind are converted to the equivalent Multi* type
(`COLLECTION_NORMALIZED` issue) so they can be measured. GDAL's LIBKML driver already turns homogeneous
`<MultiGeometry>` into Multi* types; the normalisation also covers nested and Shapefile-derived collections.

## Other per-feature checks

* **Z values** are kept as a flag (`has_z`) and ignored for measurement (file warning `Z_COORDINATES_IGNORED`).
* **Antimeridian:** a geometry spanning > 180° of longitude is flagged `ANTIMERIDIAN_CROSSING_SUSPECTED`.
* **Attributes** are preserved; values that are not JSON/PostgreSQL-safe (NaN, NUL characters, bytes, dates) are
  normalised and the feature gets `PROPERTIES_SANITIZED`. See [supported-formats.md](supported-formats.md).
