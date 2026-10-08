# Test strategy

317 automated tests: 299 backend (pytest) and 18 frontend (Vitest). All pass. `mypy --strict` is clean on the 55
modules of `backend/app`, and `ruff` is clean. How to run them: [../deployment/local.md#running-tests](../deployment/local.md#running-tests).

## Pyramid

| Layer | Tests | Needs PostGIS | Location | Selected by |
|---|---:|---|---|---|
| Unit | 200 | no | `backend/tests/unit/` | always runs |
| Integration | 33 | yes | `backend/tests/integration/` | `TEST_DATABASE_URL` |
| API (HTTP via Starlette `TestClient`) | 64 | yes | `backend/tests/api/` | `TEST_DATABASE_URL` |
| Performance | 2 | no | `backend/tests/performance/` | marker `slow` |
| Frontend | 18 | no | `frontend/src/**/*.test.{ts,tsx}` | `npm test` (jsdom) |

Counts per file (from `pytest --collect-only`; parametrised cases counted individually):

| File | Tests | What it covers |
|---|---:|---|
| `unit/test_api_plumbing.py` | 29 | Cursor encoding (no floats, bound to sort order, malformed input), body-size middleware (declared and chunked), request-id generation/propagation/sanitising, security headers, error envelope incl. unhandled 500, JSON log formatter and redaction, `SecretStr`, schema-name validation. Uses a minimal FastAPI app, no database. |
| `unit/test_geo_crs_and_projection.py` | 37 | CRS override parsing (authority codes only), declared-vs-override resolution and warnings, ESRI WKT recognised as EPSG, axis order, UTM zone selection (table, polar exclusion, Hypothesis oracle), LAEA grid snapping and metric/equal-area checks. |
| `unit/test_geo_measurement_and_geometry.py` | 31 | Area/length accuracy against the geodesic reference at four locations for both strategies, LAEA vs UTM at a central meridian, Web Mercator overstatement, holes and winding, normalisation of collections, `MakeValid` repair and unrepairable cases. |
| `unit/test_geo_processor_and_pipeline.py` | 21 | Per-feature failure isolation, statuses, vertex limit, missing CRS, mislabelled projected coordinates, distortion and antimeridian flags, Z handling, multi-layer KML, Shapefile with/without `.prj`, CRS override, feature limit, empty and unreadable datasets, null totals. |
| `unit/test_geo_properties.py` | 6 | JSON-safe attribute normalisation, NUL stripping, LIBKML presentation defaults, name derivation. |
| `unit/test_ingestion_archive.py` | 25 | ZIP inspection and hostile archives (see below), flat extraction, streaming byte budget, CRC. |
| `unit/test_ingestion_filenames_and_sniffing.py` | 29 | Display-filename sanitising, extension allow-list, magic bytes, KML root detection, spoofed files. |
| `unit/test_ingestion_kml_safety.py` | 6 | Placemark/NetworkLink counting, XXE, billion laughs, wrong root, malformed, empty. |
| `unit/test_storage.py` | 14 | Storage key validation, `LocalStorage` round trip and atomic overwrite, `S3Storage` against moto, error wrapping, factory. |
| `integration/test_job_queue.py` | 11 | Queue semantics (see below). |
| `integration/test_migrations.py` | 1 | Upgrade, model drift, downgrade, re-upgrade. |
| `integration/test_processing_and_uploads.py` | 21 | Upload service (dedup, idempotency, requeue rules, nothing persisted on rejection) and job executor end to end on real PostGIS and local storage. |
| `api/test_upload_api.py` | 29 | `POST /api/files/`: 202/201/200 paths, `Location`/`Retry-After`, `Prefer: wait`, idempotent replay, CRS override, every rejection code, size limits. |
| `api/test_read_api.py` | 24 | File info, listing with cursor, measurements (not ready / failed, units, rounding, keyset paging, sort with NULLs last across pages, filters, caps), GeoJSON, single feature with repaired geometry, unreferenced coordinates, vector tiles, jobs. |
| `api/test_system_api.py` | 7 | `/health`, `/ready` (incl. broken storage), capabilities, OpenAPI paths, CORS allow/deny, request id and security headers. |
| `performance/test_throughput.py` | 2 | 100,000 polygons through the pipeline, once per projection strategy. |
| `frontend: lib/api.test.ts` | 8 | Error-envelope parsing, query building (repeated filters, cursor), `ApiError` mapping, network failure, file-selection validation, CRS pattern parity with the API. |
| `frontend: lib/format.test.ts` | 7 | Unit selection (m², ha, km², m, km), SI formatting, relative difference, sizes, durations, coordinates, labels. |
| `frontend: components/status.test.tsx` | 3 | Status labels always carry text (colour is never the only signal). |

## Database isolation

Database tests run against `TEST_DATABASE_URL` and never touch the application schema (`backend/tests/conftest.py`):

1. **Throwaway schema per session.** `test_<10 hex chars>` is created by running the **real Alembic migrations**
   (`command.upgrade(config, "head")` with `config.attributes["schema"]`), so tests exercise exactly the DDL that
   production gets, including PostGIS enablement and partial indexes. It is dropped with `DROP SCHEMA ... CASCADE`
   at the end of the session.
2. **Empty tables per test.** The `clean_db` fixture runs `TRUNCATE features, files, processing_jobs` before each
   test that uses the `container`.
3. **Schema routing without `search_path`.** The test engine uses the same `schema_translate_map` mechanism as
   production, pointed at the throwaway schema.
4. **Isolated settings.** `make_settings()` builds `Settings(_env_file=None, ...)`, so a developer's `.env` cannot leak
   in. Defaults are tuned for tests: `processing_batch_size=3` (tiny files still exercise multi-batch paths), retry
   base delay 0 s, poll interval 0.05 s, lease 60 s.
5. **Skip vs fail.** Without `TEST_DATABASE_URL` the DB tests skip; with `REQUIRE_DB_TESTS=1` (CI) they fail instead,
   so a misconfigured pipeline cannot pass silently.
6. **No real cloud calls.** An autouse fixture removes `AWS_PROFILE`/`AWS_*` credentials from the environment for
   every test.

## Oracles (independent answers to compare against)

| Oracle | Used for |
|---|---|
| Ellipsoidal geodesic area/length (`pyproj.Geod`, Karney's algorithm; no projection involved) | Projected measurements must match within 1e-6 (LAEA) or 2e-3 (UTM) at Delhi, London, Sydney and Cape Town; a feature on UTM 43N's central meridian must show the expected 0.08 % UTM error and < 1e-8 for LAEA. |
| GeoPandas `estimate_utm_crs()` via Hypothesis (150 random points, zone boundaries filtered out) | `utm_epsg()` must pick the same zone as GeoPandas' PROJ-database query. GeoPandas is a dev dependency only. |
| PostGIS `ST_IsValid` | The stored original bow-tie is invalid and the stored repaired geometry is valid, as judged by a second geometry engine. |
| Analytic values | A 100 m x 100 m square in UTM is 10,000 m² with a 400 m perimeter; 0.1° of longitude at 28°N is about 9,836 m; Web Mercator overstates area by more than 28 % at Delhi. |

## Hostile-input tests

Archives: path traversal (6 name variants: `../`, nested `../`, absolute POSIX, backslash-absolute, drive letter,
backslash `..`), symlink entries, encrypted-flag entries, too many entries, compression-ratio bomb, declared total
size over the limit, a header that under-declares its size (rejected by the CRC check or the byte counter, and never
more than the declared bytes written), the overall extraction budget, and CRC corruption. XML: external entity (XXE),
billion laughs, a DTD with an entity declaration in the prolog, wrong root element, malformed and empty documents. Uploads: spoofed extensions (ZIP named `.kml`, XML named `.zip`, executable named `.kml`), empty
file, empty ZIP, `.kmz`/`.geojson`/`.shp`/`.exe`/no extension, oversized bodies with and without `Content-Length`,
`../../etc/` in the filename, CRS strings such as `file:///etc/passwd` and `+proj=...`, malformed `Idempotency-Key`,
unsafe `X-Request-ID` values, and schema names like `drop table`. Builders for these live in
[`backend/tests/builders.py`](../../backend/tests/builders.py) ([test-data.md](test-data.md)).

## Queue semantics tests

`integration/test_job_queue.py` and the executor tests in `integration/test_processing_and_uploads.py`:

- empty queue; `get_or_create` idempotent on fingerprint;
- claim sets `PROCESSING`, a lease and a fencing token `worker:<random>`;
- **SKIP LOCKED is non-blocking**: two open transactions claim two different jobs, a third returns `None`
  immediately instead of waiting;
- jobs with `run_after` in the future are not claimed; backoff delays the next claim;
- **lease takeover and fencing**: after a lease expires a second worker claims the job (attempt 2) and the stale
  worker's `heartbeat()` and `complete()` both return `False`;
- transient failures retry until `max_attempts`, permanent failures are final, `release()` returns a job without
  consuming an attempt, `requeue()` resets a failed job;
- **crash re-run without duplicates**: a job forced back to `PENDING` with its rows still present ends with exactly
  the original feature count;
- a worker that lost its lease writes no features; a pre-set stop event releases the job (`PENDING`, attempts 0);
  an exhausted job fails with `MAX_ATTEMPTS_EXCEEDED`; a missing blob is retried, then fails as
  `INFRASTRUCTURE_ERROR` with `retryable=true`.

## Migration drift test

`test_upgrade_downgrade_roundtrip_and_no_model_drift` migrates a fresh `mig_<random>` schema, then runs Alembic's
`compare_metadata` between the migrated database and the ORM models (copied into that schema). Any difference fails
the test, so models and migrations cannot diverge. It then downgrades to `base` (only `alembic_version` remains) and
upgrades again. CI additionally runs `alembic upgrade head` against the service database with the default
`DB_SCHEMA` (`geomeasure`), i.e. exactly as a deployment would.

## Performance tests

`tests/performance/test_throughput.py` (marker `slow`) writes 100,000 random square polygons (4° x 4° area near
75-79°E, 20-24°N) to a Shapefile with GeoPandas and runs `process_dataset` with batch size 5,000 for each strategy.
It asserts every feature is measured and that throughput exceeds a deliberately low floor of **1,000 features/s**
(shared CI runners), which catches order-of-magnitude regressions such as a per-feature `Transformer`.

Measured on the author's Windows laptop, single process, pipeline only (no database writes): **about 7,400
features/s** (~13.4 s for 100k) with the default strategy and ~7,300 with UTM. CI prints its own numbers (`-s`) but
they are not recorded in the repository. See [../architecture/scalability.md](../architecture/scalability.md).

## Continuous integration

`.github/workflows/ci.yml`, on pushes to `main` and on pull requests. There is no version matrix: one Python (3.13),
one Node (24), one PostGIS image.

| Job | Steps |
|---|---|
| Backend | `postgis/postgis:17-3.5` service; `uv pip install -c constraints.txt -e ".[dev]"`; `ruff check` + `ruff format --check`; `mypy app`; `pytest -m "not slow"` with coverage (XML uploaded as an artifact); `pytest -m slow -s`; `alembic upgrade head` on a clean database. `REQUIRE_DB_TESTS=1`. |
| Frontend | `npm ci`, `npm run lint` (oxlint, warnings are errors), `npm run typecheck`, `npm test`, `npm run build`. |
| Docker | Builds both images (`push: false`) after backend and frontend pass. |
| Audit | `pip-audit` on `constraints.txt`, `npm audit --omit=dev --audit-level=high`. `continue-on-error: true` (informational). |

## Bugs the tests caught

1. **500 responses lacked `X-Request-ID`.** Starlette's `ServerErrorMiddleware` sits outside user middleware, so
   unhandled exceptions were rendered after the request-id middleware had finished. Fix: `RequestContextMiddleware`
   renders the 500 envelope itself. Guarded by `test_unhandled_errors_do_not_leak_internals`.
2. **Float cursors broke paging on Supabase.** Supabase runs with `extra_float_digits=0`, so `double precision`
   values come back with 15 significant digits; a cursor carrying the last `area_m2` no longer equalled the stored
   value and rows repeated or vanished. Found by an API test failing against Supabase. Fix: the cursor carries only
   `feature_index` and the database resolves the sort value. Guarded by `test_cursor_carries_no_float_values` and
   `test_sort_by_area_descending_with_nulls_last_across_pages`.
3. **Totals 0 vs null.** A file with nothing measured reported `total_area_m2: 0` instead of `null`. Guarded by
   `test_summary_totals_are_null_when_nothing_was_measured`.
4. **Leaked test session holding a lock.** A session left open by a test kept its lock and blocked the tests that
   followed (the per-test `TRUNCATE` needs an exclusive lock). Tests that deliberately hold transactions open, such
   as the SKIP LOCKED test, now commit and close their sessions in `finally`.

## What is not tested

| Gap | Notes |
|---|---|
| Browser end-to-end tests | No Playwright/Cypress in CI. The UI was checked manually in a browser (that is how the MapLibre container-height bug was found). Map, table, upload panel and hooks have no automated tests. |
| Real AWS S3 | `S3Storage` is tested only against moto. |
| Running containers | CI builds the Docker images but never starts them; Compose has not been run locally. |
| Python 3.12 | Allowed by `requires-python`, never tested. |
| Real signal handling | Graceful shutdown is tested by setting the stop event directly, not by sending SIGTERM to `python -m app.worker`. |
| Many worker processes, API load | No multi-process or HTTP load test; the performance tests cover the pipeline without the database. |
| Supabase in CI | CI uses the PostGIS container; Supabase was exercised from the developer machine only. |
| Coverage threshold | Coverage is measured and uploaded as a CI artifact (95 % statements + branches on the last local run); no minimum is enforced. |
| Type checking of tests | CI runs `mypy app`, not `mypy tests`. |
