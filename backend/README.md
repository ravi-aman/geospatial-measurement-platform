# Geomeasure backend

FastAPI service + worker that ingests KML / zipped Shapefiles, extracts every feature and measures area and
length in a locally projected metric CRS. See the [project README](../README.md) for the full picture.

```
app/
  api/            HTTP layer: routes, schemas, middleware, error envelope (no business logic)
  services/       use cases: accept an upload, execute a processing job
  geoprocessing/  framework-free geospatial core (read, CRS, validate, project, measure)
  ingestion/      untrusted-input defences (sniffing, zip safety, XML safety, filenames)
  db/             SQLAlchemy models, engine, repositories (incl. the Postgres job queue)
  storage/        object storage protocol + local / S3 implementations
  worker/         queue consumer process (python -m app.worker)
  observability/  JSON logging, correlation ids
migrations/       Alembic (schema-configurable; enables PostGIS)
tests/            unit / integration / api / performance
scripts/          sample-data generator, constraints pinning
```

Quick start (from this directory):

```bash
pip install -c constraints.txt -e ".[dev]"
cp .env.example .env          # set DATABASE_URL
alembic upgrade head
uvicorn app.main:create_app --factory --reload
python -m app.worker          # in a second terminal (or EMBEDDED_WORKER=true)
pytest                        # TEST_DATABASE_URL enables the database-backed tests
```

Module-level design notes live next to the code: [`app/geoprocessing/README.md`](app/geoprocessing/README.md),
[`app/ingestion/README.md`](app/ingestion/README.md), [`app/db/README.md`](app/db/README.md).
