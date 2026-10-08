# Geomeasure — CRS-aware geospatial file measurement

Upload a **KML** file or a **zipped Shapefile**; every feature is extracted with its geometry type, geometry, CRS and
attributes, and measured — **area (m²)** for polygons, **length (m)** for lines — after projecting to a local metric
coordinate system and cross-checking against an ellipsoidal geodesic reference. Points are listed without a
measurement. A FastAPI + PostGIS backend with a PostgreSQL job queue, and a React + MapLibre frontend.

[![CI](https://github.com/ravi-aman/geospatial-measurement-platform/actions/workflows/ci.yml/badge.svg)](https://github.com/ravi-aman/geospatial-measurement-platform/actions/workflows/ci.yml)

| | |
|---|---|
| **Assignment** | AEREO — Software Development Engineer Intern: *Geospatial File Measurement API* |
| **Backend** | Python 3.13 · FastAPI · SQLAlchemy 2 · PostgreSQL/PostGIS (Supabase) · GDAL (pyogrio) · Shapely 2 · PyProj |
| **Frontend** | React 19 · TypeScript · Vite · Tailwind + shadcn/ui · TanStack Query/Virtual · MapLibre GL JS |
| **Tests** | 293 backend (unit, integration and API against real PostGIS, performance) · 18 frontend · `mypy --strict` |
| **Docs** | [`docs/`](docs/) — architecture, geospatial, API, deployment, 10 ADRs, [interview guide](docs/interview.md) |

| Results map (PostGIS vector tiles) | Title block (dark theme) | Feature table |
|---|---|---|
| ![Map of a synthetic mine-site survey: lease, pits, a repaired self-intersecting dump, stockpiles, haul roads, control points](docs/assets/map-light.jpg) | ![Survey title block with CRS, feature counts and summed area and length](docs/assets/title-block-dark.jpg) | ![Virtualised feature table with status, geometry type and area](docs/assets/feature-table.jpg) |

---

## Contents
[Problem](#problem-statement) · [Features](#features) · [Architecture](#architecture) · [Tech stack](#tech-stack-and-why) ·
[Getting started](#getting-started) · [API](#api) · [Geospatial processing](#geospatial-processing) ·
[Error handling](#error-handling) · [Security](#security) · [Scalability](#scalability-and-performance) ·
[Observability](#observability) · [Testing](#testing) · [Project structure](#project-structure) ·
[Design decisions](#design-decisions) · [Limitations](#limitations) · [Future scope](#future-scope) ·
[What I learned](#what-i-learned) · [Deployment](#production-deployment) · [Interview notes](#interview-talking-points)

---

## Problem statement

Build a service that accepts a geospatial file (KML or zipped Shapefile), processes its features and returns
measurements — without ever computing area or distance in latitude/longitude degrees. Minimum API:
`POST /api/files/`, `GET /api/files/{id}/`, `GET /api/files/{id}/measurements/`.

The hard parts are not the endpoints: they are **CRS correctness** (which projection, what if the CRS is missing or
wrong), **messy real-world geometry** (self-intersections, mixed collections, null shapes) and **untrusted files**
(zip bombs, path traversal, XXE) — while one bad feature must never sink the whole file.

## Features

* **Formats:** KML (all folders/layers, ExtendedData, MultiGeometry) and zipped Shapefiles (nested folders, any case,
  `.prj`/`.cpg`, macOS archive noise ignored).
* **Per feature:** id, layer, geometry type, geometry (RFC 7946 GeoJSON), CRS, attributes, measurement status.
* **Measurements:** area + perimeter for (Multi)Polygons, length for (Multi)LineStrings, nothing for points; projected
  per feature to a local **equal-area** CRS (or UTM), with a **geodesic cross-check** reported per feature.
* **CRS handling:** declared CRS, ESRI WKT recognition, optional override on upload, never guesses a missing CRS,
  catches lon/lat-labelled metre coordinates.
* **Geometry handling:** validation with GEOS reasons, documented repair (`MakeValid`), original and repaired both kept.
* **Robustness:** per-feature failure isolation (`COMPLETED_WITH_ERRORS`), asynchronous jobs with leases, fencing,
  retries and crash recovery; idempotent uploads; content deduplication.
* **Security:** byte-counted size limits, magic-byte sniffing, zip-slip/zip-bomb/symlink/encryption defences,
  XXE-safe KML scanning, structured errors without internals.
* **Read API:** keyset pagination, filters, sorting, GeoJSON features, **PostGIS vector tiles**.
* **UI:** drag-and-drop upload with progress, live processing status, survey-style title block, MapLibre map,
  feature details with measurement provenance, virtualised feature table, light/dark theme.

## Architecture

A **modular monolith**: one backend image running as API or worker, PostgreSQL/PostGIS as database *and* queue,
object storage for uploads, and a static SPA.

```mermaid
flowchart LR
    SPA["React SPA"] -- "upload / poll / read / tiles" --> API["FastAPI API"]
    API -- "blob (sha256 key)" --> OBJ[("Object storage<br/>local / S3")]
    API -- "file + job rows, one transaction" --> DB[("PostgreSQL + PostGIS")]
    W["Workers (N)"] -- "claim: FOR UPDATE SKIP LOCKED<br/>lease + fenced heartbeat" --> DB
    W -- "read upload" --> OBJ
    W -- "features + measurements" --> DB
    API -- "keyset queries, ST_AsMVT" --> DB
```

**File-processing flow.** `POST /api/files/` streams the body to disk (SHA-256 + size limit), validates extension, magic
bytes, ZIP structure and CRS syntax, stores the bytes under their hash, and in one transaction inserts the file row and
gets-or-creates the job (deduplicated by fingerprint) → `202 Accepted` + `Location`. A worker claims the job, re-validates
the file, reads **every layer** in Arrow batches, processes each batch, and writes the features in the same transaction
as a fenced heartbeat; finally it stores the summary and status. Details: [data flow](docs/architecture/data-flow.md),
[processing](docs/architecture/processing.md).

**Measurement-calculation flow (per batch).** decode WKB → checks (missing / malformed / empty / too complex) →
transform to WGS 84 (`always_xy`) → lon/lat range check → normalise collections → validate / repair → choose a local
projected CRS per feature → group by CRS → vectorised projection → `area` / `length` → geodesic reference (Karney) and
relative difference. Details: [measurements](docs/geospatial/measurements.md).

**CRS handling.** Override > declared > unknown (never guessed). Measurement CRS: Lambert Azimuthal Equal-Area centred
near each feature (areas exact anywhere, tiny linear error, works at the poles) or per-feature UTM; never the source
CRS, never Web Mercator. Details: [CRS strategy](docs/geospatial/crs.md).

More: [overview](docs/architecture/overview.md) · [backend](docs/architecture/backend.md) ·
[database](docs/architecture/database.md) · [storage](docs/architecture/storage.md) ·
[frontend](docs/architecture/frontend.md).

## Tech stack and why

| Choice | Why | Alternatives considered |
|---|---|---|
| **FastAPI** + Pydantic v2 | typed contract → OpenAPI, explicit DI, lean service | Django/DRF/GeoDjango ([ADR-001](docs/decisions/adr-001-framework.md)) |
| **PostgreSQL + PostGIS** (Supabase) | durable jobs + results; `ST_AsMVT` vector tiles, GiST, spatial checks | plain Postgres + JSONB, GeoParquet ([ADR-004](docs/decisions/adr-004-database.md)) |
| **Postgres job queue** (`SKIP LOCKED`) | no extra broker, transactional enqueue, leases/fencing/retries | sync, BackgroundTasks, Celery/RQ + Redis, SQS ([ADR-002](docs/decisions/adr-002-processing-model.md)) |
| **pyogrio (GDAL)** | reference reader for KML/Shapefile; Arrow streaming of every layer | `geopandas.read_file` (first layer only, all in memory) ([ADR-009](docs/decisions/adr-009-streaming-instead-of-geodataframes.md)) |
| **Shapely 2** | vectorised GEOS: validity, repair, area, length, transforms | per-feature loops |
| **PyProj** | PROJ transformations, EPSG/ESRI CRS database, Karney geodesics | — |
| **SQLAlchemy 2 + psycopg 3 + Alembic** | Core control for queue/keyset/MVT SQL; migrations; pooler-safe | Django ORM, async stack ([ADR-010](docs/decisions/adr-010-sync-data-layer.md)) |
| **Local / S3 storage** | stateless workers, content-addressed blobs | bytes in Postgres, MinIO ([ADR-003](docs/decisions/adr-003-storage.md)) |
| **Vite + React + TS SPA** | static deploy, no SSR needs | Next.js ([ADR-007](docs/decisions/adr-007-frontend-and-map.md)) |
| **MapLibre + MVT** | GPU rendering, tiles from PostGIS, no API key | Leaflet, OpenLayers, whole-file GeoJSON |
| **TanStack Query / Virtual** | polling, infinite keyset paging, row virtualisation | hand-rolled fetch state |

Deliberately **not** used: Redis (nothing it would do better than Postgres here — [ADR-008](docs/decisions/adr-008-idempotency-and-caching.md)),
Celery, microservices, GeoPandas at runtime.

## Getting started

### Environment variables
All settings are environment variables (12-factor); [`backend/.env.example`](backend/.env.example) documents every one.
The essentials:

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://postgres:postgres@localhost:5432/geomeasure` | PostgreSQL + PostGIS (e.g. Supabase **Session pooler** URL) |
| `DB_SCHEMA` | `geomeasure` | dedicated schema for the app's tables |
| `STORAGE_BACKEND` / `STORAGE_LOCAL_ROOT` / `S3_BUCKET` | `local` / `var/storage` / — | where uploads are stored |
| `EMBEDDED_WORKER` | `false` | run a worker thread inside the API (single-process dev) |
| `MAX_UPLOAD_BYTES` | `104857600` | upload limit (100 MiB) |
| `MEASUREMENT_STRATEGY` | `local_equal_area` | or `utm` |
| `CORS_ALLOW_ORIGINS` | `["http://localhost:5173"]` | allowed browser origins |
| `TEST_DATABASE_URL` | — | enables the database-backed tests (isolated throwaway schema) |
| `VITE_API_BASE_URL` | empty (same origin) | frontend → API origin in production |

### Local development (no Docker)
Requirements: Python ≥ 3.12, Node ≥ 22, PostgreSQL with PostGIS (Supabase works; the migration enables PostGIS).

```bash
cd backend
pip install -c constraints.txt -e ".[dev]"
```
```bash
cp .env.example .env
```
Edit `.env` and set `DATABASE_URL` (for Supabase use the Session pooler URI and URL-encode the password), then:
```bash
alembic upgrade head
```
```bash
EMBEDDED_WORKER=true uvicorn app.main:create_app --factory --reload
```
In another terminal:
```bash
cd frontend && npm ci && npm run dev
```
Open http://localhost:5173 (the dev server proxies `/api` to `:8000`). API docs: http://localhost:8000/docs.
For a separate worker process instead of `EMBEDDED_WORKER`: `python -m app.worker`. Full guide:
[docs/deployment/local.md](docs/deployment/local.md).

### Docker
```bash
docker compose up --build
```
Starts PostGIS, runs migrations, the API, **two workers** and nginx serving the SPA on http://localhost:8080
(API docs at `/docs`). To use Supabase instead of the local database, put `DATABASE_URL=…` in a root `.env`.
Guide: [docs/deployment/docker.md](docs/deployment/docker.md).

### Running tests
```bash
cd backend
pytest -m "not slow"
```
```bash
pytest -m slow -s
```
```bash
ruff check app tests && mypy app
```
```bash
cd frontend && npm test && npm run lint && npm run typecheck && npm run build
```
Database tests run when `TEST_DATABASE_URL` is set; they create and drop their own schema via the real migrations, so
they are safe against a shared development database. CI runs everything against a PostGIS container
([.github/workflows/ci.yml](.github/workflows/ci.yml)).

### Sample data
[`samples/`](samples/) contains synthetic datasets: a multi-folder mine-site KML (polygons with a hole, a
self-intersecting polygon, MultiGeometry, lines, points with elevation, a placemark without geometry) and Shapefiles in
WGS 84, UTM 43N and Web Mercator, one without `.prj`, lines, points and edge cases.
Regenerate with `python scripts/generate_samples.py`. See [docs/testing/test-data.md](docs/testing/test-data.md).

## API

Interactive docs at `/docs` (Swagger UI) and `/redoc`; reference in [docs/api/](docs/api/overview.md).

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/files/` | upload `.kml` or `.zip`; optional `crs` field; `Idempotency-Key`, `Prefer: wait=N` headers |
| `GET` | `/api/files/` | recent uploads (cursor-paginated) |
| `GET` | `/api/files/{id}/` | file information and processing status |
| `GET` | `/api/files/{id}/measurements/` | per-feature measurements (filters, sorting, keyset pagination) |
| `GET` | `/api/files/{id}/features/` | features as GeoJSON (paginated) |
| `GET` | `/api/files/{id}/features/{n}/` | one feature incl. repaired geometry |
| `GET` | `/api/files/{id}/tiles/{z}/{x}/{y}.mvt` | vector tiles for the map |
| `GET` | `/api/jobs/{id}/` | job status, attempts, progress |
| `GET` | `/health`, `/ready`, `/api/capabilities` | liveness, readiness, limits |

### Example: upload (asynchronous)
```bash
curl -i -F "file=@samples/kml/parcel_block_wgs84.kml" http://localhost:8000/api/files/
```
```http
HTTP/1.1 202 Accepted
location: /api/files/38849f3b-02d8-4970-a544-a58833009fab/
retry-after: 1
x-request-id: 6b9ed0ebe5424d32bd746f4c4781f8de
```
```json
{
  "id": "38849f3b-02d8-4970-a544-a58833009fab",
  "filename": "parcel_block_wgs84.kml",
  "format": "KML",
  "size_bytes": 4300,
  "status": "PENDING",
  "feature_count": null,
  "crs": null,
  "job": { "id": "56eb6896-6ba4-41b2-a41e-5efa23ab5461", "status": "PENDING", "attempts": 0, "max_attempts": 3, "…": "…" },
  "links": { "self": "/api/files/38849f3b-…/", "measurements": "/api/files/38849f3b-…/measurements/", "…": "…" }
}
```
Add `-H "Prefer: wait=10"` to wait for processing: the response is then `201 Created` with the final result.

### Example: file information
```bash
curl http://localhost:8000/api/files/474b0681-3c4f-4055-9de7-c7e77c5c5b2e/
```
```json
{
  "id": "474b0681-3c4f-4055-9de7-c7e77c5c5b2e",
  "filename": "plots_utm43n.zip",
  "format": "SHAPEFILE",
  "feature_count": 6,
  "crs": "EPSG:32643",
  "crs_name": "WGS 84 / UTM zone 43N",
  "crs_source": "FILE",
  "status": "COMPLETED",
  "summary": { "total_features": 6, "measured": 6, "total_area_m2": 78040.32, "bbox": [77.209, 28.613899999999997, 77.2177, 28.614900000000006], "…": "…" },
  "warnings": [],
  "job": { "status": "COMPLETED", "attempts": 1, "duration_ms": 373, "…": "…" }
}
```

### Example: measurements
```bash
curl "http://localhost:8000/api/files/474b0681-3c4f-4055-9de7-c7e77c5c5b2e/measurements/?limit=2"
```
```json
{
  "file_id": "474b0681-3c4f-4055-9de7-c7e77c5c5b2e",
  "status": "COMPLETED",
  "crs": "EPSG:32643",
  "units": { "area": "m2", "length": "m" },
  "items": [
    {
      "feature_id": 0,
      "layer": "plots_utm43n",
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
      "issues": [],
      "error": null
    }
  ],
  "page": { "limit": 2, "next_cursor": "eyJzIjoiZmVhdHVyZV9pZCIsImkiOjF9", "total": 6 }
}
```
Unsupported geometries are explicit, never a crash:
`{"feature_id": 11, "geometry_type": "GeometryCollection", "measurement_status": "UNSUPPORTED", "measurement": null, "error": {"code": "MIXED_GEOMETRY_COLLECTION", …}}`.

Filters and sorting: `?status=MEASURED&geometry_type=Polygon&sort=-area_m2&limit=100&cursor=…`.

## Geospatial processing

* **Reading:** GDAL via pyogrio; every layer (a KML folder is a layer) streamed as Arrow batches of 5,000 features.
* **Feature status:** `MEASURED`, `NOT_APPLICABLE` (points), `UNSUPPORTED` (mixed collections), `FAILED` (with a code:
  `GEOMETRY_MISSING`, `GEOMETRY_INVALID_UNREPAIRABLE`, `CRS_MISSING`, `COORDINATES_OUT_OF_RANGE`, …).
* **Normalisation:** homogeneous GeometryCollections → Multi* and measured; Z ignored (planimetric) with a warning.
* **Validation & repair:** GEOS validity + reason; `make_valid(method="structure")`; original and repaired stored.
* Docs: [measurements](docs/geospatial/measurements.md) · [geometry handling](docs/geospatial/geometry-handling.md) ·
  [supported formats](docs/geospatial/supported-formats.md).

### CRS strategy
Measured with [`scripts/compare_projections.py`](backend/scripts/compare_projections.py) — relative difference from
the ellipsoidal geodesic reference:

| Case | UTM | **Local equal-area (default)** | Web Mercator |
|---|---:|---:|---:|
| 1 km square on a UTM central meridian | −0.080 % | −0.0000 % | +28.8 % |
| 1 km square near a UTM zone edge | +0.128 % | −0.0000 % | +28.8 % |
| 50 km square across two UTM zones | +0.134 % | −0.0008 % | +29.3 % |
| 10 km square at 85°N | undefined | −0.0000 % | +13,217 % |
| 10 km line near a zone edge | +0.064 % | −0.0005 % | +13.2 % |

Missing CRS is never guessed; the override `crs=EPSG:xxxx` exists for such files. Full reasoning:
[docs/geospatial/crs.md](docs/geospatial/crs.md), [ADR-005](docs/decisions/adr-005-crs-strategy.md).

### Measurement strategy
Units are explicit in field names (`area_m2`, `perimeter_m`, `length_m`). The API rounds to 0.01 m / 0.01 m²; the
database keeps full doubles; `relative_difference` (projected / geodesic − 1) is attached to every measurement and
values above 0.1 % are flagged `HIGH_PROJECTION_DISTORTION`.

## Error handling

* **Upload-time (4xx, nothing persisted):** `415 UNSUPPORTED_FILE_TYPE`, `413 FILE_TOO_LARGE`,
  `422 EMPTY_FILE / INVALID_KML / CONTENT_MISMATCH / INVALID_ZIP / SHAPEFILE_MISSING / SHAPEFILE_INCOMPLETE /
  MULTIPLE_SHAPEFILES / ZIP_UNSAFE_PATH / ZIP_BOMB_SUSPECTED / INVALID_CRS / IDEMPOTENCY_KEY_REUSED`, …
* **Processing (job):** dataset errors → `FAILED` (no retry); infrastructure errors → retried with backoff;
  per-feature problems → feature status, job `COMPLETED_WITH_ERRORS`.
* **Reads:** `404 FILE_NOT_FOUND`, `409 RESULTS_NOT_READY` (with `Retry-After`), `409 PROCESSING_FAILED`,
  `400 INVALID_CURSOR`.
* **One envelope everywhere**, with the request id; internals never leak:
```json
{"error": {"code": "SHAPEFILE_INCOMPLETE", "message": "The Shapefile is incomplete; missing component(s): .shx.",
           "details": {"missing": [".shx"], "shapefile": "parcels.shp"}, "request_id": "540e6cc1c2714c198a0a2ad7a4523a42"}}
```

## Security

Untrusted files are checked at the API **and again in the worker**: byte-counted size limit (chunked bodies
included), extension allow-list + magic bytes, ZIP inspection (traversal, absolute paths, symlinks, encryption, entry
count, total size, compression ratio) and extraction of only the Shapefile components to fixed flat names with a
streaming byte budget, defusedxml scan (XXE, billion laughs) before GDAL, KML NetworkLinks never fetched, vertex and
feature limits, display-only filenames, parameterised SQL, secrets as `SecretStr` and redacted in logs, non-root
containers, tables outside Supabase's auto-exposed `public` schema. Not implemented (documented): auth, rate limiting,
malware scanning. [docs/architecture/security.md](docs/architecture/security.md).

## Scalability and performance

* **Measured:** ~**7,400 features/s** for 100,000 polygons through the full pipeline (one process); small files finish
  in about a second end to end.
* Workers scale horizontally (`SKIP LOCKED`); memory is bounded by the batch for Shapefiles; reads use keyset
  pagination, stored summaries and PostGIS vector tiles; the UI virtualises and never downloads a whole dataset.
* Next steps with the numbers behind them — `COPY` inserts, chunked jobs for single huge files, pre-signed direct
  uploads, partitioning: [docs/architecture/scalability.md](docs/architecture/scalability.md).

## Observability

JSON logs (stdlib) with `request_id`, `job_id`, `worker_id` bound through `contextvars`; the job stores the request id
that created it, so worker logs correlate with the upload request. One access-log line per request with duration;
job started/finished/failed events with counts and duration; `X-Request-ID` on every response (also 500s);
`/health` (liveness, no dependencies) and `/ready` (database + storage).
[docs/architecture/observability.md](docs/architecture/observability.md).

## Testing

| Layer | Count | Highlights |
|---|---:|---|
| Backend unit | 198 | CRS precedence & axis order; UTM zone vs GeoPandas (Hypothesis); accuracy vs geodesics on 4 continents; Web Mercator trap; repair; zip-slip/bomb/symlink; XXE; failure isolation; middleware; storage (moto S3) |
| Backend integration (PostGIS) | 33 | SKIP LOCKED, lease takeover, fencing, retries, release, crash re-runs, dedup, idempotency, migration drift + round trip |
| Backend API (PostGIS) | 60 | full upload/processing/read contract, every rejection path, pagination, filters, tiles |
| Backend performance | 2 | 100k features, both strategies |
| Frontend | 18 | formatters, API client, validation, status component |

Backend coverage: **95 %** (statements + branches, `pytest --cov`). Tests found real bugs: missing request ids on
500s, a pagination bug that only appears with Supabase's float formatting, `0` instead of `null` totals. [docs/testing/strategy.md](docs/testing/strategy.md).

## Project structure

```
backend/
  app/
    api/            routes, schemas, middleware, errors, pagination, presenters
    services/       uploads (accept + enqueue), processing (execute a job)
    geoprocessing/  reader, crs, projection, geometry, measurement, processor, pipeline, summary
    ingestion/      sniffing, archive (zip safety), kml_safety, filenames
    db/             models, session, repositories (jobs = the queue, files, features)
    storage/        protocol, local, s3
    worker/         queue consumer (python -m app.worker)
    observability/  JSON logging, correlation ids
    container.py    composition root          main.py  app factory
  migrations/       Alembic
  tests/            unit · integration · api · performance
  scripts/          generate_samples · compare_projections · pin_constraints
frontend/src/
  pages/            home (upload + recent files), file (results)
  components/       title block, map, feature table/details, upload, shadcn/ui
  hooks/ lib/       TanStack Query hooks, API client, formatters, types
samples/            synthetic KML / Shapefile datasets
docs/               architecture · geospatial · api · deployment · decisions · testing · interview
```

## Design decisions

Recorded as ADRs in [docs/decisions/](docs/decisions/README.md): FastAPI (001), Postgres job queue (002),
content-addressed object storage (003), PostGIS on Supabase in a dedicated schema (004), local equal-area projection
with geodesic cross-check (005), geometry repair keeping the original (006), Vite SPA + MapLibre vector tiles (007),
fingerprint dedup + Idempotency-Key without Redis (008), streaming instead of GeoDataFrames (009), synchronous data
layer (010).

## Limitations

* No authentication, authorisation or rate limiting (out of scope; designed in [security.md](docs/architecture/security.md)).
* Upload limit 100 MiB; KML is parsed in memory by GDAL; KMZ, GeoPackage, GeoJSON inputs are not accepted.
* Measurements are planimetric (2D); Z is ignored. Antimeridian-crossing geometries are flagged, not split.
* Totals are sums of features (overlapping features count twice), not a dissolved footprint.
* A single file is processed by one worker; chunking across workers is designed, not built.
* Docker images are built in CI; on the development machine the stack was run natively against Supabase.

## Future scope

Authentication and multi-tenancy · pre-signed direct uploads and chunked processing for multi-GB files · `COPY`
inserts and table partitioning · queue-depth autoscaling, OpenTelemetry metrics and traces · 3D surface area and
volumes from Z values or a DEM (stockpiles!) · dissolved footprints and boundary intersections in PostGIS ·
antimeridian splitting · KMZ / GeoPackage / FlatGeobuf / GeoParquet inputs · export of results (CSV, GeoJSON,
GeoPackage) · a strict "reject invalid geometries" mode.

## What I learned

* **Projection choice is a measurable error budget, not a convention.** UTM — the textbook answer — is conformal, and
  its area error (−0.08 % to +0.2 %) is visible with a geodesic oracle; an equal-area projection centred near the
  feature removes it at the same cost. Measuring it beat arguing about it.
* **Library defaults can silently lose data.** `read_file` reads one layer; KML folders are layers.
* **"Works locally" is not "works on the real database".** Supabase's `extra_float_digits = 0`, IPv6-only direct
  hosts and transaction poolers each changed a design decision; testing against the real database found them.
* **Postgres can be a correct queue** if leases, fencing and idempotent re-runs are designed together — and that is
  where most of the subtle bugs would hide, so it deserved the most tests.
* **Untrusted files are an attack surface**: archive and XML formats need structural defences, not filters.
* **Honest edge semantics matter to users**: `null` vs `0`, original vs repaired geometry, "unknown CRS" vs a guess.

## Production deployment

Reference design for AWS (CloudFront + S3 SPA, ALB → ECS Fargate API, autoscaled ECS workers, RDS PostgreSQL/PostGIS
or Supabase, S3 uploads via IAM role, migrations as a one-off task, secrets in Secrets Manager, JSON logs to
CloudWatch): [docs/deployment/production.md](docs/deployment/production.md). The images, health checks, S3 backend and
configuration it relies on exist today.

## Interview talking points

25 questions answered from the actual code — CRS selection, missing CRS, invalid geometries, workers, crashes,
retries, dedup, scaling to millions of features and multi-GB files, PostGIS, object storage, frontend rendering, AWS —
in [docs/interview.md](docs/interview.md). Context on how the assignment relates to AEREO's public domain:
[docs/aereo-context.md](docs/aereo-context.md). Requirement-by-requirement evidence:
[docs/requirements-traceability.md](docs/requirements-traceability.md).

---

MIT licensed · author: ravi-aman
