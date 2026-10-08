# Docker Compose stack

`docker-compose.yml` at the repository root runs the whole system: PostGIS, a one-shot migration, the API, two
workers and the web frontend behind nginx.

> **Verification status.** Both images are built by CI on every push (`docker` job in
> `.github/workflows/ci.yml`: `docker/build-push-action` with `push: false`). The first CI run on `main`
> built both images successfully. CI does **not** start the containers, and the Compose stack has not been run on
> the author's machine (Docker Desktop could not start there and PyPI is unreachable from that network). Everything below is derived from the Compose file, the two
> Dockerfiles and `frontend/nginx.conf`; treat it as a reviewed configuration, not a verified runbook.

## Services

| Service | Image / command | Ports | Depends on | Purpose |
|---|---|---|---|---|
| `db` | `postgis/postgis:17-3.5` | none published | - | PostgreSQL + PostGIS, data in volume `pgdata`. Healthcheck: `pg_isready`. Dev credentials `geomeasure/geomeasure`. |
| `migrate` | backend image, `alembic upgrade head` | - | `db` healthy | Creates the schema, enables PostGIS, applies migrations, exits. `restart: "no"`. |
| `api` | backend image, default `CMD` (uvicorn) | `expose: 8000` (internal only) | `migrate` completed successfully | HTTP API. `EMBEDDED_WORKER=false`, `CORS_ALLOW_ORIGINS=["http://localhost:8080"]`. |
| `worker` | backend image, `python -m app.worker` | - | `migrate` completed successfully | Queue consumers. `deploy.replicas: 2`, `stop_grace_period: 30s`. |
| `web` | frontend image (nginx) | `8080:80` | `api` (start order only) | Serves the SPA and reverse-proxies the API, so the browser sees one origin. |

Volumes: `pgdata` (database) and `uploads`, mounted at `/data/storage` in `api`, `migrate` and `worker`. The
shared volume is how workers read what the API stored (`STORAGE_BACKEND=local`); production uses S3 instead
(see [production.md](production.md)).

Environment shared by every backend container (YAML anchor `x-backend`):

| Variable | Value |
|---|---|
| `DATABASE_URL` | `${DATABASE_URL:-postgresql+psycopg://geomeasure:geomeasure@db:5432/geomeasure}` |
| `DB_SCHEMA` | `${DB_SCHEMA:-geomeasure}` |
| `STORAGE_BACKEND` / `STORAGE_LOCAL_ROOT` | `local` / `/data/storage` |
| `LOG_FORMAT` / `LOG_LEVEL` | `json` / `INFO` |

All other settings take their code defaults (see `backend/app/config.py`).

## Run

```bash
docker compose up --build              # web UI: http://localhost:8080, API docs: http://localhost:8080/docs
docker compose ps
docker compose down                    # stop, keep volumes
docker compose down -v                 # stop and delete the database and uploaded files
```

The API port is not published; reach it through nginx on 8080 (`/api/...`, `/health`, `/ready`, `/docs`,
`/redoc`, `/openapi.json` are proxied).

## Images

**Backend** (`backend/Dockerfile`), one image for both roles:

- Multi-stage on `python:3.13-slim`. The build stage installs dependencies into `/opt/venv` with
  `pip install -c constraints.txt .` (dependency layer cached separately from the source), then the package.
- Runtime stage: non-root user `app` (uid/gid 10001), `/data/storage` owned by it, `LOG_FORMAT=json`. The
  `app` package is installed in `/opt/venv`; `alembic.ini`, `migrations/` and `scripts/` are copied to `/app`.
  `tests/`, `var/` and `.env*` are excluded by `.dockerignore`.
- `CMD`: `uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000 --proxy-headers
  --forwarded-allow-ips * --no-access-log` (the app writes its own access log line with the request id).
- `HEALTHCHECK`: HTTP GET `http://127.0.0.1:8000/health` every 15 s.
- No system geospatial libraries: GDAL, GEOS and PROJ come from the `pyogrio`, `shapely` and `pyproj` wheels.

**Web** (`frontend/Dockerfile`):

- `node:24-alpine` build stage: `npm ci`, `npm run build`. Build arg `VITE_API_BASE_URL` (default empty =
  same origin).
- `nginx:1.29-alpine` runtime with `frontend/nginx.conf`: `client_max_body_size 110m` (just above the API's
  100 MiB limit), `/api/` proxied with `proxy_request_buffering off` (uploads stream to the API),
  `proxy_read_timeout 60s`, `X-Forwarded-*` and `X-Request-ID $request_id` set; `/assets/` cached for a year
  (`immutable`); every other path falls back to `index.html` with `Cache-Control: no-cache`.

## Using Supabase (or another managed PostgreSQL) instead of `db`

Create a git-ignored `.env` next to `docker-compose.yml`. Compose reads it for variable substitution, and the
file only influences `DATABASE_URL` and `DB_SCHEMA`, the two variables the Compose file references:

```bash
DATABASE_URL=postgresql+psycopg://postgres.<project-ref>:<url-encoded-password>@<pooler-host>:5432/postgres?sslmode=require
DB_SCHEMA=geomeasure
```

```bash
docker compose up --build
```

`migrate` then runs against Supabase and enables PostGIS there if needed. The local `db` container still
starts (it is a dependency of `migrate`) but is unused. URL-encoding the password also avoids Compose
interpreting a literal `$` in it. Connection details are in [local.md#supabase-specifics](local.md#supabase-specifics).

Other settings placed in the root `.env` are **not** passed into the containers (there is no `env_file:`
directive); add them to the `environment:` blocks in `docker-compose.yml` instead.

## Scaling workers

```bash
docker compose up -d --scale worker=4
```

Workers are interchangeable: each claims one job at a time with `SELECT ... FOR UPDATE SKIP LOCKED`, so any
number can share the queue without coordination. Throughput scales with worker processes because
processing is CPU-bound (one job per process). Each process opens its own SQLAlchemy pool (`DB_POOL_SIZE=5`,
`DB_MAX_OVERFLOW=5`), which matters against connection-limited managed databases.

On `docker compose stop`/`down`, workers get SIGTERM and up to 30 s (`stop_grace_period`). The worker stops at
the next batch boundary (the stop flag is checked before each batch is written), releases the job back to
`PENDING` without consuming an attempt, and exits. If it is killed first, the job's lease
(`WORKER_LEASE_S=300`) expires and another worker re-runs it from scratch (partial rows are deleted first).

## Logs

Backend containers log one JSON object per line on stdout.

```bash
docker compose logs -f api worker
docker compose logs worker | grep '"msg": "job failed"'
docker compose logs api worker | grep '"request_id": "<id from the X-Request-ID response header>"'
```

nginx assigns `X-Request-ID` per proxied request; the API echoes it, stores it on the job, and the worker binds
it to the job's log lines, so one id follows an upload from HTTP request to processing. Field reference:
[../architecture/observability.md](../architecture/observability.md).

## Restricted networks (PyPI mirror)

The backend build stage accepts a package index URL:

```bash
docker compose build --build-arg PIP_INDEX_URL=https://mirror.example.com/simple
# or a single image:
docker build --build-arg PIP_INDEX_URL=https://mirror.example.com/simple -t geomeasure-backend:local backend
```

It only affects the build stage. The frontend image has no equivalent argument; `npm ci` uses the default
registry (an `.npmrc` would be needed; not implemented).

## Known gaps

| Gap | Effect |
|---|---|
| Worker (and `migrate`) containers inherit the image `HEALTHCHECK`, which calls `/health` on port 8000. The worker runs no HTTP server. | Worker containers will be reported `unhealthy` by Docker. Nothing depends on their health, so they keep running. No override in `docker-compose.yml`. |
| `nginx.conf` sets `add_header` at server level and again inside `location /` and `location /assets/`. nginx does not inherit `add_header` into a block that defines its own. | `X-Content-Type-Options` and `Referrer-Policy` are not sent for the SPA and asset responses (API responses carry the API's own security headers). |
| No `restart:` policies. | A crashed `api` or `worker` container stays down. |
| KML driver in the PyPI wheels | Development and tests used conda-forge GDAL with the `LIBKML` driver. Which KML driver the `pyogrio` wheel in the image provides has not been verified in a running container. |
| `/docs` exposed, dev database credentials | Acceptable for a local stack only. |
