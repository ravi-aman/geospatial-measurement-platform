# Local development (without Docker)

Run the API, the worker and the frontend directly on a workstation against any PostgreSQL + PostGIS database
(Supabase or a local install). For the containerised stack see [docker.md](docker.md).

> Honest note: the author's machine could not reach PyPI, so the development environment was built from
> conda-forge (micromamba). The `pip` path below is the one CI uses on every push (`uv pip install -c
> constraints.txt -e ".[dev]"`), so it is exercised, just not on the author's workstation.

## Prerequisites

| Tool | Version | Notes |
|---|---|---|
| Python | 3.12+ (`requires-python = ">=3.12"`) | CI, the Docker image and the test runs use 3.13. 3.12 is not exercised in CI. |
| Node.js | 24 | Same major as CI and the `node:24-alpine` build image. |
| PostgreSQL + PostGIS | PostgreSQL 14+ / PostGIS 3+ (per `.env.example`) | Tested against Supabase (PostgreSQL 17.11, PostGIS 3.3.7) and `postgis/postgis:17-3.5` in CI. |

No system GDAL/GEOS/PROJ is required: they ship inside the `pyogrio`, `shapely` and `pyproj` wheels.

## Backend

All backend commands run from `backend/`: settings are read from a `.env` file in the **current working
directory** (`SettingsConfigDict(env_file=".env")`), and Alembic reads the same settings.

```bash
cd backend
python -m venv .venv
source .venv/bin/activate              # Windows: .venv\Scripts\activate
pip install -c constraints.txt -e ".[dev]"
cp .env.example .env                   # then set DATABASE_URL (see "Supabase" below)
alembic upgrade head                   # creates the schema, enables PostGIS, creates tables
uvicorn app.main:create_app --factory --reload
```

`create_app` is a factory (`--factory`), so importing `app.main` has no side effects. The API listens on
`http://localhost:8000`; interactive docs are at `/docs` and `/redoc` while `EXPOSE_DOCS=true`.

### Running the worker

Uploads are processed by a worker that polls the job table every `WORKER_POLL_INTERVAL_S` (1 s). Two options:

| Option | How | When |
|---|---|---|
| Embedded worker thread | `EMBEDDED_WORKER=true` (set in `.env.example`; the code default is `false`) | Single-process development. The API wakes the thread right after each upload. |
| Separate process | `python -m app.worker` (or the `geomeasure-worker` console script) in a second terminal | Same topology as Docker/production. Ctrl+C triggers a graceful stop. |

Both can run at the same time; jobs are claimed with `FOR UPDATE SKIP LOCKED`, so no job is processed twice.
If neither runs, uploads stay `PENDING` indefinitely.

### Smoke test

```bash
curl -s localhost:8000/health          # {"status":"ok"} - no dependencies
curl -s localhost:8000/ready           # 200 when database and storage are reachable, else 503
curl -s -H "Prefer: wait=10" \
     -F file=@../samples/kml/parcel_block_wgs84.kml \
     localhost:8000/api/files/         # 201 with final results if processing finishes within 10 s
```

Uploaded bytes land in `STORAGE_LOCAL_ROOT` (default `backend/var/storage`, git-ignored).

## Frontend

```bash
cd frontend
npm ci
npm run dev                            # http://localhost:5173
```

The Vite dev server proxies `/api`, `/health`, `/ready`, `/docs` and `/openapi.json` to the backend, so the
browser sees a single origin and CORS does not apply in development (`/redoc` is not proxied).

| Variable | Read by | Default | Purpose |
|---|---|---|---|
| `VITE_DEV_API_TARGET` | `vite.config.ts` via `process.env` | `http://localhost:8000` | Proxy target. Must be set in the shell (`VITE_DEV_API_TARGET=http://localhost:9000 npm run dev`); Vite does not load `.env` files into `process.env` for its own config. |
| `VITE_API_BASE_URL` | app code (`import.meta.env`, build time) | empty | Leave empty locally. Only for an API on another origin. |
| `VITE_BASEMAP_STYLE_LIGHT` / `VITE_BASEMAP_STYLE_DARK` | map component (build time) | OpenFreeMap `positron` / `dark` | MapLibre style URLs; no API key needed. |

If the frontend calls the API directly from another origin, add that origin to the backend's
`CORS_ALLOW_ORIGINS` (a JSON list, default `["http://localhost:5173"]`).

## Supabase specifics

The development and demo database is Supabase. Points that matter:

1. **Use the Session pooler.** Supabase's direct host (`db.<project-ref>.supabase.co`) is IPv6-only; on an
   IPv4-only network it is unreachable. In the dashboard choose *Connect -> Session pooler* and copy host,
   port and user (the pooler user has the form `postgres.<project-ref>`).
2. **Change the driver prefix** to SQLAlchemy's psycopg 3 dialect: `postgresql+psycopg://...` (the dashboard
   shows `postgresql://`).
3. **URL-encode the password.** Characters such as `@ : / ? # %` otherwise break URL parsing:

   ```bash
   python -c "import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=''))" 'p@ss/w:rd'
   ```

4. **TLS.** The application does not set `sslmode`; libpq's default is `prefer`. Append `?sslmode=require` to
   insist on TLS (libpq parameters in the URL are passed through by SQLAlchemy/psycopg).
5. **Dedicated schema.** Tables live in `DB_SCHEMA` (default `geomeasure`), applied via SQLAlchemy's
   `schema_translate_map`. Supabase's auto-generated REST API exposes the `public` schema, so the application
   tables are not reachable through it. The migration creates the schema if it is missing.
6. **PostGIS is enabled by the migration** (`0001_initial_schema`): if the extension is absent it runs
   `CREATE EXTENSION postgis`, into the `extensions` schema when that schema exists (Supabase), otherwise
   into the default schema. The connecting role needs permission to create extensions; Supabase's
   `postgres` role has it.
7. **Pooler compatibility.** psycopg's server-side prepared statements are disabled (`prepare_threshold=None`)
   and the app keeps no session state (no `SET`, no `LISTEN`), so it is designed to also work through the
   transaction pooler (port 6543). Only the Session pooler was used during development.

Resulting `.env` line (placeholders in angle brackets):

```bash
DATABASE_URL=postgresql+psycopg://postgres.<project-ref>:<url-encoded-password>@<pooler-host>:5432/postgres?sslmode=require
```

## Running tests

```bash
cd backend
pytest -m "not slow"                   # what CI runs first; DB-backed tests are skipped without a database
pytest -m slow -s                      # 2 throughput tests on 100k synthetic polygons (prints features/s)
ruff check app tests migrations scripts && ruff format --check app tests migrations scripts
mypy app                               # strict mode, configured in pyproject.toml
```

Plain `pytest` runs everything, including the slow tests (there is no default deselection).

Database-backed tests (integration and API, 97 of 299) need `TEST_DATABASE_URL`, set in the environment or in
`backend/.env`. They create a throwaway schema `test_<random>` with the real Alembic migrations and drop it at
the end of the session, so pointing them at a shared development database is safe. If the test process is
killed, the `finally` block does not run and a `test_*` (or `mig_*`, from the migration test) schema is left
behind; drop it manually.

| Variable | Effect |
|---|---|
| `TEST_DATABASE_URL` | Enables the database tests. Same URL format as `DATABASE_URL`. |
| `REQUIRE_DB_TESTS=1` | Fail instead of skip when `TEST_DATABASE_URL` is missing (set in CI). |

Frontend checks (the same four steps CI runs):

```bash
cd frontend
npm run lint && npm run typecheck && npm test && npm run build
```

See [../testing/strategy.md](../testing/strategy.md) for what each layer covers.

## Regenerating the sample data

```bash
cd backend
python scripts/generate_samples.py     # writes ../samples (needs the dev extras: GeoPandas)
```

The script deletes the sub-directories of `samples/` before writing. KML output is deterministic; the ZIP
files are not byte-identical between runs (ZIP entries carry file modification times), so regenerated ZIPs
get new SHA-256 hashes and are processed as new jobs. Contents are described in
[../testing/test-data.md](../testing/test-data.md).

## Pinning dependencies

`constraints.txt` pins the full dependency closure (71 packages) to the versions the test suite passed with.
Regenerate it inside an environment where the tests pass:

```bash
cd backend
python scripts/pin_constraints.py
```

The script walks the installed metadata of the runtime and `dev` requirements, skips platform-specific
packages (`colorama`, `pywin32`, `win32-setctime`, `tzdata`, `uvloop`) and adds `psycopg-binary` at the
same version as `psycopg`. CI and the backend Docker image install with `-c constraints.txt`.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `/ready` returns 503 with `"database": "unavailable"` | Wrong `DATABASE_URL`, unencoded password characters, or the IPv6-only direct Supabase host on an IPv4 network. The log line carries only the exception type. |
| Uploads stay `PENDING` | No worker: `EMBEDDED_WORKER=false` and no `python -m app.worker` running. |
| Settings seem ignored | Command not run from `backend/`, so `.env` was not found. |
| Console vs JSON logs | `.env.example` sets `LOG_FORMAT=console`; the code default (and the Docker image) is `json`. |
| Browser CORS error | Frontend calling the API from an origin missing from `CORS_ALLOW_ORIGINS`. Use the Vite proxy instead. |
