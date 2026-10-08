# ADR-001: FastAPI instead of Django + DRF

**Status:** Accepted

## Context
The assignment allows Django + DRF or FastAPI. The service is an API (no server-rendered pages, no admin users,
no auth in scope) with a typed request/response contract, file uploads and a background worker. The geospatial core
must be usable without the web framework (tests, worker).

## Decision
FastAPI with Pydantic v2 models for every request and response; SQLAlchemy 2 + Alembic for persistence.

## Alternatives considered
* **Django + DRF + GeoDjango.** Batteries included: ORM with PostGIS fields, admin, migrations, mature ecosystem.
  GeoDjango is a genuine advantage for GIS CRUD (it is what Aereo's public FOSS4G talk mentions).
* **Flask.** Minimal, but validation, OpenAPI and async support would all be add-ons.

## Why FastAPI
* **Typed contract → OpenAPI for free.** Response models (`app/api/schemas.py`) are the documentation; `/docs` is
  always in sync with the code, and the frontend types mirror them.
* **Explicit dependency injection** (`Depends`) keeps routes thin and makes the composition root
  (`app/container.py`) testable with an isolated schema and temp storage.
* **Lean runtime for a service** that is mostly "validate upload → enqueue → serve read models". Django's admin,
  templates, sessions and auth would be unused weight.
* SQLAlchemy Core gives precise control over the queries that matter here (`FOR UPDATE SKIP LOCKED`, keyset
  pagination, `ST_AsMVT`), which the Django ORM would push into raw SQL anyway.

## Trade-offs
* No admin UI; no built-in auth. Auth is out of the assignment's scope and is documented as future work.
* GeoDjango's spatial lookups are replaced by GeoAlchemy2 + explicit PostGIS functions — more code, more control.

## Consequences
* Routes are plain `def` functions running in FastAPI's threadpool (see [ADR-010](adr-010-sync-data-layer.md)).
* All errors go through one envelope (`app/api/errors.py`), all inputs are Pydantic-validated.
