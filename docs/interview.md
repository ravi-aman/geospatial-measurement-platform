# Interview guide

Answers grounded in the code. Each answer separates **Implemented** (in this repository, tested) from
**Production extension** (designed or recommended, not built). File references are relative to `backend/` unless noted.

---

### 1. Why FastAPI?
**Implemented.** The API is a typed contract: Pydantic response models (`app/api/schemas.py`) generate the OpenAPI
docs at `/docs`, validate every input, and the frontend's TypeScript types mirror them. `Depends` gives explicit
dependency injection, which is how tests build the app against an isolated schema and temp storage
(`app/container.py`, `tests/conftest.py`). Django's admin/templates/auth would be unused weight; SQLAlchemy Core gives
direct control over `FOR UPDATE SKIP LOCKED`, keyset pagination and `ST_AsMVT`. GeoDjango was a real alternative
(it is strong for GIS CRUD) — see [ADR-001](decisions/adr-001-framework.md).

### 2. Why GeoPandas?
**Implemented — deliberately *not* at runtime.** `geopandas.read_file()` reads only the first layer (a KML with folders
is several layers; our sample would lose 9 of 13 features) and materialises everything in memory. The service streams
every layer with pyogrio's Arrow reader and does the math with Shapely 2 + pyproj — the libraries GeoPandas is built
on. GeoPandas stays in the test suite as an independent oracle (`estimate_utm_crs`) and to write sample Shapefiles.
[ADR-009](decisions/adr-009-streaming-instead-of-geodataframes.md).

### 3. Why Shapely?
**Implemented.** Shapely 2 exposes GEOS as vectorised NumPy ufuncs: `from_wkb`, `transform`, `area`, `length`,
`bounds`, `is_valid`, `is_valid_reason`, `make_valid(method="structure")`, `orient_polygons` all run on whole arrays,
which is why the pipeline does ~7,400 features/s.

### 4. Why PyProj?
**Implemented.** It is the Python binding to PROJ — the reference implementation of CRS definitions and
transformations (EPSG database, ESRI WKT matching, datum shifts). It also provides `Geod`, Karney's geodesic
algorithms, used as the independent accuracy reference for every measurement. Transformers use `always_xy=True`
(EPSG:4326 is officially lat/lon) and are cached per projection.

### 5. Why not calculate in EPSG:4326 directly?
Degrees are angles. One degree of longitude is 111 km at the equator, 98 km at Delhi, 0 at the poles; "square
degrees" have no physical meaning. A test shows a 0.01° × 0.01° square has `.area == 1e-4` in degrees but ~1.08 km²
on the ellipsoid. EPSG:3857 is no fix either: +29 % area at Delhi, +13,000 % at 85°N
(`scripts/compare_projections.py`).

### 6. How is the projected CRS selected?
**Implemented.** Per feature, a Lambert Azimuthal Equal-Area projection centred on the 1° cell containing the feature
(own centre if > 2° wide). Equal-area → areas exact anywhere; linear error ~(d/R)²/8 (0.0008 % at 50 km); works at the
poles. Measured: UTM is off −0.08 % … +0.13 % for 1–50 km features, LAEA < 0.001 %. `MEASUREMENT_STRATEGY=utm` switches
to per-feature UTM (zone choice property-tested against GeoPandas). Every measurement also gets a Karney geodesic
reference and `relative_difference`; > 0.1 % raises `HIGH_PROJECTION_DISTORTION`. [crs.md](geospatial/crs.md).

### 7. What happens if the CRS is missing?
**Implemented.** We never guess (coordinates like (45.2, 12.8) can be metres on a local grid). Features are still
extracted with attributes and raw coordinates (`geom_raw`, returned as `raw_geometry` with `geometry_crs: null`);
polygons/lines are `FAILED` with `CRS_MISSING`; the file is `COMPLETED_WITH_ERRORS` with a warning telling the user to
re-upload with `crs=EPSG:xxxx`. A wrong `.prj` claiming 4326 for metre coordinates is caught as
`COORDINATES_OUT_OF_RANGE`. Totals are `null`, not `0`.

### 8. How are invalid geometries handled?
**Implemented.** Validated with GEOS; the reason is recorded (`Self-intersection[…]`). Polygons are repaired with
`make_valid(method="structure", keep_collapsed=False)`; the original is stored in `geom`, the measured repaired
geometry in `geom_repaired`, and the feature is flagged `GEOMETRY_REPAIRED`. Nothing usable left → `FAILED`
(`GEOMETRY_INVALID_UNREPAIRABLE`). PostGIS confirms: `ST_IsValid(geom)=false`, `ST_IsValid(geom_repaired)=true`
in an integration test. [ADR-006](decisions/adr-006-geometry-repair.md).

### 9. How does large-file processing scale?
**Implemented:** Arrow batches (5,000 features) bound memory for Shapefiles; vectorised per-batch work; one job per
worker process; workers scale horizontally via `SKIP LOCKED`; keyset pagination, stored summaries and MVT tiles keep
reads cheap. **Production extension:** `COPY` for inserts, chunk jobs for single huge files, pre-signed direct S3
uploads. [scalability.md](architecture/scalability.md).

### 10. Why background workers?
**Implemented.** Processing is CPU-bound and variable (ms to minutes). Requests would hit proxy timeouts and lose work
on a crash; `BackgroundTasks` run in the API process with no retries. Workers give crash recovery (leases),
retries, isolation of heavy files from the API, and independent scaling. `Prefer: wait=N` keeps the simple synchronous
experience for small files.

### 11. Why not microservices?
One bounded context (ingest → measure → serve). The only part with different scaling needs — processing — is already
a separate process type of the same image. Microservices would add network hops, distributed transactions and
contract versioning for no benefit. Boundaries are enforced by package layering instead
([overview.md](architecture/overview.md)).

### 12. How would you process 1 GB / 10 GB files?
**Today:** rejected by the 100 MiB upload limit (deliberate). **Production extension:** clients upload directly to S3
with pre-signed multipart URLs (the API never proxies bytes); the job references the object; workers read through
GDAL's `/vsis3/` or download to local disk; the file is split into chunk jobs by feature ranges (the Shapefile driver
has random access); recommend GeoParquet/FlatGeobuf for such sizes; KML stays small because GDAL parses it as a DOM.

### 13. How would you process millions of features?
**Implemented:** batches, vectorisation, bounded memory, keyset reads, MVT, ~2–3 CPU-minutes per million polygons
per worker. **Production extension:** chunked jobs across many workers with a final merge of summaries; `COPY`
inserts; partition `features` by job; pre-built tile caches/PMTiles for large finished jobs; move cold geometries to
GeoParquet.

### 14. How would you horizontally scale workers?
**Implemented:** any number of `python -m app.worker` processes (compose runs 2 replicas) safely share the queue —
`FOR UPDATE SKIP LOCKED` never double-claims or blocks. **Production extension:** ECS service autoscaled on queue
depth (`count(*) WHERE status='PENDING'` published as a CloudWatch metric).

### 15. How would you avoid duplicate processing?
**Implemented:** (1) **Idempotency-Key** header — retries replay the original (`200`, `Idempotent-Replayed: true`);
same key + different content → `422`; a unique constraint makes concurrent retries safe. (2) **Job fingerprint** =
SHA-256(content hash, format, CRS override, `PROCESSOR_VERSION`, strategy), unique in the DB: re-uploading identical
content creates a new file row pointing at the existing job — instant results, no CPU. `PROCESSOR_VERSION` is bumped
when results change (it was, to 2). (3) Claims are exclusive. [ADR-008](decisions/adr-008-idempotency-and-caching.md).

### 16. How would you handle worker crashes?
**Implemented.** The lease (`WORKER_LEASE_S`) expires and another worker reclaims the job. Each attempt first deletes
the previous attempt's partial rows. Every batch insert shares a transaction with a heartbeat fenced on the attempt's
token, so a stalled-then-resumed worker writes nothing. A job that crashes the worker repeatedly (e.g. OOM) is failed
with `MAX_ATTEMPTS_EXCEEDED` instead of looping. SIGTERM releases the job without consuming an attempt. All tested.

### 17. How would you retry failed jobs?
**Implemented.** Infrastructure errors → `PENDING` with `run_after = 10 s × 2^(attempt−1) × jitter`, up to 3 attempts;
dataset errors → `FAILED` immediately (`error_retryable=false`) because retrying cannot fix bad input. Re-uploading a
transiently failed file re-queues it. **Production extension:** a dead-letter view and an admin "retry" endpoint.

### 18. How would you monitor processing?
**Implemented:** JSON logs with `request_id` (also on the job, so worker logs correlate with the upload request),
`job_id`, `worker_id`; job start/finish/failure events with duration and per-status counts; `X-Request-ID` on every
response including 500s; `/health` (liveness) and `/ready` (DB + storage); job progress via `GET /api/jobs/{id}/`.
**Production extension:** OpenTelemetry traces, metrics (queue depth, job duration histogram, failure rate by code),
alerts on queue age and `FAILED` rate.

### 19. How would you secure uploads?
**Implemented:** byte-counting size limit (not trusting `Content-Length`), extension + magic bytes, ZIP inspection
(traversal, symlinks, encryption, entry count, total size, compression ratio) and extraction to fixed flat names with a
streaming byte budget, defusedxml scan against XXE/billion laughs before GDAL, NetworkLinks never fetched, vertex and
feature limits, filenames display-only, re-validation in the worker, non-root container. **Not implemented:** auth,
rate limiting, malware scanning, GDAL sandboxing. [security.md](architecture/security.md).

### 20. Why PostGIS?
**Implemented.** It makes map delivery scale: `ST_AsMVT` + GiST serve vector tiles for any dataset size, clipped and
quantised per zoom, cacheable. It also provides an independent validity check and future spatial queries (bbox,
intersections, `ST_Union` footprints). Geometry is stored in WGS 84; unknown-CRS coordinates go to an SRID-less column
so nothing is mislabelled. [ADR-004](decisions/adr-004-database.md).

### 21. Why object storage?
**Implemented.** Uploads must outlive requests and be readable by workers on other hosts; the original file is the
lossless record. `StorageBackend` protocol with local and S3 implementations; content-addressed keys
(`uploads/ab/<sha256>.kml`) give idempotent writes and dedup; storing blobs in Postgres would bloat the DB and backups.
[ADR-003](decisions/adr-003-storage.md).

### 22. How would you reduce database load?
**Implemented:** summaries computed while streaming (file info is one row), keyset pagination (no OFFSET scans),
counts only on the first page, partial indexes for the queue, tiles cacheable (`Cache-Control`, `ETag`), page-size
caps, geometry only when asked for. **Production extension:** `COPY` inserts, read replicas for tiles/pages, CDN tile
cache, partitioning, PgBouncer/Supavisor transaction pooling (the app is already pooler-safe).

### 23. How would you optimise frontend rendering?
**Implemented:** map from vector tiles (never whole-file GeoJSON); table virtualised with TanStack Virtual and loaded
page-by-page over keyset cursors (infinite scroll); filters applied server-side for the table and as MapLibre layer
filters for the map (no re-fetch); map bundle lazy-loaded (initial JS 452 kB); polling stops when the job is terminal.

### 24. How would you deploy this to AWS?
**Designed, not provisioned:** CloudFront + S3 for the SPA; ALB → ECS Fargate API (liveness `/health`, readiness
`/ready`); ECS worker service autoscaled on queue depth with 30 s stop timeout for graceful release; RDS PostgreSQL +
PostGIS (or Supabase); S3 uploads via IAM task role; migrations as a one-off ECS task before each deploy; secrets in
Secrets Manager; JSON logs to CloudWatch. The images, health checks, config and S3 backend already exist.
[production.md](deployment/production.md).

### 25. What would you change for true production scale?
Auth and tenancy; pre-signed direct uploads; chunked processing of huge files; `COPY` inserts; queue-depth autoscaling
and metrics/tracing; partitioning and geometry tiering; a strict "reject invalid geometries" mode for regulated
workflows; antimeridian splitting; 3D measurements from Z/DEM (surface area, stockpile volumes); GeoPackage /
FlatGeobuf / KMZ inputs; blob garbage collection and retention policies.

---

## Things worth mentioning unprompted (found by tests, not by guessing)

1. **Supabase renders floats with 15 significant digits** (`extra_float_digits = 0`). A keyset-pagination API test
   failed only against Supabase because a float cursor no longer equalled the stored value. Fix: cursors carry the last
   `feature_index`; the DB resolves the sort value.
2. **500 responses had no request id** because Starlette's `ServerErrorMiddleware` sits outside custom middleware;
   unhandled errors are now rendered inside the request-context middleware.
3. **KML folders are layers** — the "read the first layer" default would have silently lost data.
4. **Totals `0` vs `null`** — a file whose polygons could not be measured reported 0 m²; now `null`, with
   `PROCESSOR_VERSION` bumped so cached results are recomputed.
5. **MapLibre 6 + Vite**: the tile worker failed under dependency pre-bundling, and MapLibre's CSS collapsed an
   absolutely positioned container to 0 px — both found in browser testing, both fixed.
