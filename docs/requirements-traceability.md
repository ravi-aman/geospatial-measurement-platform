# Requirements traceability

Every requirement of the assignment, where it is implemented, and the tests that prove it.

| # | Requirement | Implementation | Evidence (tests) |
|---|---|---|---|
| 1 | Backend in FastAPI or Django/DRF | FastAPI — `backend/app/main.py` | all API tests |
| 2 | `POST /api/files/` accepts `.zip` Shapefile and `.kml` | `app/api/routes/files.py`, `app/services/uploads.py`, `app/ingestion/` | `tests/api/test_upload_api.py::TestAccepted` |
| 3a | Read the file and extract features | `app/geoprocessing/reader.py` (all layers, streamed), `pipeline.py` | `test_kml_with_folders_reads_every_layer`, shapefile pipeline tests |
| 3b | Per feature: ID/index | `feature_id` (0-based across layers) + `source_fid` + `layer` | `test_keyset_pagination_visits_every_item_once` |
| 3c | Geometry type | `geometry_type` (original type) | `test_items_have_explicit_status_and_units` |
| 3d | Geometry | `GET /api/files/{id}/features/` (RFC 7946 GeoJSON), single feature incl. repaired geometry, vector tiles | `TestFeatures` |
| 3e | CRS | file-level `crs` / `crs_name` / `crs_source`; per-measurement `projected_crs` | `test_assignment_contract_fields`, `test_crs_override_is_recorded` |
| 3f | Properties/attributes | `properties` (JSONB, preserved; normalised only when unsafe) | `test_geojson_feature_collection`, `test_geo_properties.py` |
| 3g | Unsupported geometry handled gracefully | `UNSUPPORTED` / `FAILED` statuses with codes; never crashes the job | `test_one_bad_feature_does_not_affect_the_others`, `test_unsupported_and_normalized_collections` |
| 4a | Polygon → area | `area_m2` (+ `perimeter_m`); MultiPolygon supported | `TestMeasurementAccuracy`, API measurement tests |
| 4b | LineString → length | `length_m`; MultiLineString supported | `test_line_length`, `test_multi_geometries_are_measured_as_sums` |
| 4c | Point → no measurement | `NOT_APPLICABLE`, `measurement: null` | `test_polygon_line_point` |
| 5 | Correct CRS handling; no measuring in degrees; project first | per-feature local equal-area (or UTM) projection + geodesic cross-check; missing CRS never guessed | `test_geo_crs_and_projection.py`, `test_web_mercator_*`, `test_degrees_are_meaningless_as_area`, `test_missing_crs_*` |
| 6a | `POST /api/files/` uploads and processes | 202 + queue, or `Prefer: wait=N` → 201 with final results | `test_kml_upload_is_accepted_for_processing`, `test_prefer_wait_returns_final_result` |
| 6b | `GET /api/files/{id}/` → id, filename, feature_count, crs, status | `FileOut` (superset of the example) | `test_assignment_contract_fields` |
| 6c | `GET /api/files/{id}/measurements/` | keyset-paginated, filterable, sortable | `TestMeasurements` |
| 7 | README: setup, API with examples, architecture, file-processing flow, measurement flow, CRS handling, design decisions | [README.md](../README.md) + this `docs/` tree | — |
| 8 | Public GitHub repo; README mentions learning and future scope | README §"What I learned", §"Future scope" | — |

## Beyond the minimum (selected)

| Addition | Where |
|---|---|
| Job queue with leases, fencing, retries, crash recovery | [processing.md](architecture/processing.md) |
| Idempotency-Key and content dedup | [ADR-008](decisions/adr-008-idempotency-and-caching.md) |
| Geometry validation/repair with original + repaired stored | [geometry-handling.md](geospatial/geometry-handling.md) |
| Upload security (zip-slip, zip bombs, XXE, size limits) | [security.md](architecture/security.md) |
| Vector tiles + MapLibre UI, virtualised table | [frontend.md](architecture/frontend.md) |
| Structured logging with correlation ids, health/readiness | [observability.md](architecture/observability.md) |
| 299 backend + 18 frontend tests, 95 % coverage, mypy --strict, CI | [testing/strategy.md](testing/strategy.md) |
| Docker images, compose stack, AWS design | [deployment/](deployment/docker.md) |
