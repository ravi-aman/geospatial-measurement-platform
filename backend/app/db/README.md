# `app.db` - persistence and the job queue

SQLAlchemy 2 models, engine/session setup, repositories (the only code that builds SQL) and, next to it, the Alembic
migrations in `backend/migrations/`. The schema itself (tables, columns, indexes, constraints) is documented in
[docs/architecture/database.md](../../../docs/architecture/database.md); queue behaviour in
[docs/architecture/processing.md](../../../docs/architecture/processing.md).

## Why it exists

Three kinds of state live in PostgreSQL: the upload records (`files`), the work queue plus result header
(`processing_jobs`) and the per-feature results with PostGIS geometry (`features`). Keeping every query in
repositories gives one place to reason about transactions, locking, indexes and SQL injection, and lets services,
the worker and read endpoints share the same tested queries.

## How it works

| File | Contents |
|---|---|
| `base.py` | `Base` with a constraint naming convention (`ix_`, `uq_`, `ck_`, `fk_`, `pk_`), so generated names are deterministic and the migration drift test can compare them. Tables are declared **without** a schema. |
| `models.py` | `ProcessingJob`, `FileUpload` (`files`), `Feature`. CHECK constraints are generated from the domain enums. |
| `session.py` | `create_db_engine(settings, schema=None)` and `create_session_factory(engine)`. |
| `repositories/jobs.py` | `JobRepository`: `get`, `get_or_create` (`INSERT ... ON CONFLICT (fingerprint) DO NOTHING`), `requeue`, and the worker side: `claim_next`, `heartbeat`, `complete`, `fail`, `release`. `ClaimedJob` and `JobOutcome` dataclasses. |
| `repositories/files.py` | `FileRepository`: `create`, `get`, `get_by_idempotency_key`, `list_recent` (keyset on `(created_at, id)`). |
| `repositories/features.py` | `FeatureRepository`: `delete_for_job`, `insert_records` (one `executemany` per batch, geometries via `ST_GeomFromWKB` with explicit SRID), `count`, `page` (keyset), `get`, `tile` (`ST_AsMVT`). `SortSpec`, `FeatureFilters`. |
| `../../migrations/env.py` | Target schema from `config.attributes["schema"]` or `DB_SCHEMA`; `CREATE SCHEMA IF NOT EXISTS`; `alembic_version` stored inside that schema; `transaction_per_migration=True`; `prepare_threshold=None`. |
| `../../migrations/versions/0001_initial_schema.py` | Enables PostGIS if missing (into `extensions` when that schema exists), creates the three tables and all indexes. `downgrade()` drops the tables but leaves the schema and PostGIS. |

**Schema routing.** The engine sets `execution_options={"schema_translate_map": {None: schema}}`, so every
statement is rendered against `DB_SCHEMA` (default `geomeasure`) or, in tests, a throwaway `test_<random>` schema.
No `search_path` is used: it is session state that transaction-mode poolers do not preserve.

**Engine options.** psycopg 3, `pool_size`/`max_overflow` from settings (5/5), `pool_timeout` 10 s,
`pool_pre_ping=True` and `pool_recycle=1800` (poolers and NATs drop idle connections), `connect_timeout` 10 s,
`application_name=geomeasure`, and `prepare_threshold=None` (no server-side prepared statements).

**Transactions belong to the caller.** Repositories never commit. Services and the worker use
`with session_factory.begin():` blocks; read endpoints get a per-request session from `app/api/deps.py` and only
read. The worker writes each feature batch in the same transaction as a fenced heartbeat.

**Fencing.** `claim_next` stores a token `<worker name>:<12 hex chars>` in `locked_by`. `heartbeat`, `complete`,
`fail` and `release` all filter on `id`, `locked_by = token` and `status = 'PROCESSING'`; a zero row count tells a
worker it lost the job.

## Inputs

- A `Session` from the caller.
- Domain values: `SourceFormat`, `JobStatus`, `FeatureRecord` (from `app.geoprocessing.models`, the only
  cross-package import), `ClaimedJob`, `JobOutcome`.
- Read parameters: `FeatureFilters(statuses, geometry_types, layer)`, `SortSpec` (`feature_index`, `area_m2`,
  `length_m`, ascending or descending), the last `feature_index` of the previous page, a page limit, tile `z/x/y`.

## Outputs

- ORM objects for jobs and files (`FileUpload.job` is eager-loaded with a join).
- SQLAlchemy rows for features, with GeoJSON strings produced by `ST_AsGeoJSON(geom, 9)` (9 decimals in degrees).
- `bytes` for vector tiles (empty bytes when no feature intersects the tile).
- `bool` from fenced updates; the resulting `JobStatus` from `fail` (`PENDING` while attempts remain).

## Failure modes

| Situation | Behaviour |
|---|---|
| Duplicate fingerprint (two uploads of the same content and options) | `ON CONFLICT DO NOTHING`, then select: both callers get the same job; `created` tells them apart. |
| Duplicate `idempotency_key` in a race | Unique constraint raises `IntegrityError`; `UploadService` catches it and replays the winner's response. |
| Stale worker writes after its lease was taken over | Fenced statements match 0 rows -> `False`; the executor raises `LeaseLostError` and stops. |
| Database unavailable | `OperationalError`/`DBAPIError`. In the worker: job failure recorded as `INFRASTRUCTURE_ERROR` (retryable) if possible, otherwise the lease expires; the polling loop backs off up to 30 s. In the API: generic 500, `/ready` returns 503. |
| Constraint violation (e.g. negative area) | Would indicate a bug. `IntegrityError` is a `DBAPIError`, so the worker treats it as transient and retries up to `JOB_MAX_ATTEMPTS`. |
| Cursor from a different sort order or malformed | Rejected before reaching SQL (`INVALID_CURSOR`, `app/api/pagination.py`). |

## Design decisions

- **Sync SQLAlchemy + psycopg 3** for both API and worker ([ADR-010](../../../docs/decisions/adr-010-sync-data-layer.md)).
- **PostgreSQL is the queue** ([ADR-002](../../../docs/decisions/adr-002-processing-model.md)): enqueueing is the
  job row inserted in the same transaction as the `files` row, so there is no dual write to a broker.
- **Keyset pagination with an index-only cursor.** The cursor carries the last `feature_index`; the sort value is
  looked up server-side by primary key. Sending float sort values through clients broke paging on Supabase
  (`extra_float_digits=0`). The predicate handles `NULLS LAST` ordering explicitly.
- **Measurements live on `features`** (1:1 with the feature); a separate table would add a join to every read.
- **`geom` vs `geom_raw`.** WGS 84 geometry (SRID 4326) only when the CRS is known; otherwise the raw coordinates go
  to `geom_raw` (no SRID). A CHECK constraint forbids both. `geom_repaired` holds the geometry actually measured
  when repair was needed.
- **GeoJSON and MVT are produced by PostGIS**, avoiding a decode/encode round trip in Python.

## Scaling considerations

- Partial indexes `ix_processing_jobs_claimable` (`status = 'PENDING'`) and `ix_processing_jobs_leases`
  (`status = 'PROCESSING'`) keep the claim query proportional to active jobs, not history.
- Inserts are one `executemany` per batch (default 5,000 rows). `COPY` would be faster for large files (*future*).
- `count()` runs only for the first page of a listing; later pages cost the same as the first.
- The GiST index `ix_features_geom` spans all jobs while tile queries filter by `job_id` too; with very many jobs a
  per-job spatial access path (partitioning or a `btree_gist` composite index) may be needed (*not measured*).
- Every API and worker process holds its own pool of up to `DB_POOL_SIZE + DB_MAX_OVERFLOW` connections.

## Security considerations

- All values are bound parameters. The only interpolated identifier is the schema name, validated against
  `^[a-z_][a-z0-9_]{0,62}$` in `Settings` and again in `migrations/env.py`.
- CHECK-constraint SQL is generated from enum values defined in code, not from input.
- `DATABASE_URL` is a `SecretStr`; it is never logged or rendered.
- Supabase exposes the `public` schema via its auto REST API; application tables live in a separate schema.
- No row-level security or tenant column exists, because the application has no authentication (*future*).

## Testing

- `tests/integration/test_job_queue.py` (11): claim, SKIP LOCKED without blocking, due times, lease takeover and
  fencing, retries and backoff, permanent failure, release, requeue.
- `tests/integration/test_migrations.py` (1): upgrade, `compare_metadata` drift check against the models,
  downgrade to base, upgrade again.
- `tests/integration/test_processing_and_uploads.py` and `tests/api/` exercise every repository method against real
  PostGIS (keyset paging, sort with NULLs across pages, filters, tiles, `ST_IsValid` of stored geometries).
- Isolation: each test session migrates its own schema; tables are truncated before each test
  ([docs/testing/strategy.md](../../../docs/testing/strategy.md#database-isolation)).

## Future improvements

- `COPY`-based bulk insert for feature batches.
- Retention and deletion (no delete path exists today).
- Partitioning `features` and a dedicated access path for tiles at large scale.
- Async engine for the API if request concurrency ever requires it (psycopg 3 supports both).
- Read replicas for tiles and measurement pages (the code has a single engine today).
