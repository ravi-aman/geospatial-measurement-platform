# ADR-010: Synchronous SQLAlchemy 2 + psycopg 3 for both API and worker

**Status:** Accepted

## Context
FastAPI supports async endpoints, and async database drivers are popular. The worker, however, is CPU-bound and
synchronous (GDAL/GEOS/PROJ), and both processes share repositories.

## Decision
One synchronous data layer (SQLAlchemy 2 ORM/Core on psycopg 3). API endpoints are plain `def`; FastAPI runs them in
its threadpool, while multipart parsing and the middleware stack stay async.

## Why
* The API's database work is short, indexed queries; the threadpool handles the concurrency this service needs.
* A single code path for API and worker — no duplicated async/sync repositories.
* psycopg 3 supports async too: switching the API to `create_async_engine` later is a contained change in
  `app/db/session.py` and the repositories, if profiling ever shows threadpool saturation.

## Trade-offs
* Each in-flight request holds a thread; the `Prefer: wait` path holds one for up to 30 s (capped and documented).
* At very high request concurrency an async stack would use fewer resources; that is not this service's bottleneck
  (processing is).
