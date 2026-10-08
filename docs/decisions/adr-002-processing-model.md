# ADR-002: Asynchronous processing on a PostgreSQL-backed job queue

**Status:** Accepted

## Context
Processing cost is proportional to features × vertices and is CPU-bound (GDAL, GEOS, PROJ). A small KML finishes in
milliseconds; a 1M-feature Shapefile takes minutes. The assignment says `POST /api/files/` "uploads and processes"
the file and the example file resource shows `status: COMPLETED`, i.e. the contract already contains a status.

Requirements derived from that: requests must not hold a connection for minutes, processing must survive a crashed
process, one heavy file must not degrade the API for everyone else, and throughput must scale by adding workers.

## Decision
* `POST /api/files/` validates, stores the bytes, inserts a job row in the **same transaction** as the file row, and
  returns `202 Accepted` + `Location`. Clients poll `GET /api/files/{id}/` (or send `Prefer: wait=N`, RFC 7240, to
  get a synchronous-feeling `201` for small files).
* Jobs live in PostgreSQL (`processing_jobs`). Workers (`python -m app.worker`) claim with
  `FOR UPDATE SKIP LOCKED`, hold a **lease** extended by a heartbeat after every batch, and **fence** all writes on
  their per-attempt token. Transient failures retry with exponential backoff; dataset errors fail immediately.
* A worker thread can run inside the API process (`EMBEDDED_WORKER=true`) for single-process deployments — same
  code path.

## Alternatives considered
| Option | Why not |
|---|---|
| Synchronous processing in the request | Long requests hit proxy timeouts (ALB default 60 s), occupy API workers, lose work on a crash, no retries, no isolation of heavy files. |
| FastAPI `BackgroundTasks` | Runs in the API process after the response: lost on restart/deploy, no retries, no visibility, competes with request handling for CPU. |
| Celery / RQ + Redis or RabbitMQ | Mature, but adds a broker to run and monitor, and creates a **dual-write** problem: the DB commit and the broker publish are not atomic (needs an outbox to be correct). Job state would live in two places. |
| Library on Postgres (procrastinate, pgqueuer) | Reasonable; the core claim/lease/fence logic is ~150 lines and is the most interview-relevant part of the system, so it is implemented and tested explicitly here. |
| Managed queue (SQS) + workers | The right move at larger scale (see [scalability](../architecture/scalability.md)); the job row would remain the source of truth and SQS would only carry wake-ups. |

## Why
* **No new infrastructure**: PostgreSQL is already required (PostGIS).
* **Transactional enqueue**: a job exists if and only if its file row exists.
* **Restart safety**: expired leases are reclaimed; a crash-looping job (e.g. OOM) is failed after `max_attempts`
  instead of looping forever; SIGTERM releases the job without consuming an attempt.
* **Idempotent re-runs**: each attempt deletes its previous partial rows first, and every batch insert happens in the
  same transaction as the fenced heartbeat, so a stale worker can never interleave rows with a newer attempt.
* **Pooler compatible**: workers poll (1 s) instead of `LISTEN/NOTIFY`, which transaction-mode poolers (Supabase
  Supavisor, PgBouncer) cannot carry.

## Trade-offs
* Polling adds up to 1 s of latency per job (the embedded worker is woken immediately after an upload).
* A Postgres queue is comfortable to thousands of jobs per second — far beyond this workload (jobs are heavy, few) —
  but it shares the database with reads; a very large fleet of workers would move wake-ups to SQS.
* Clients must handle `202` + polling; the frontend does (TanStack Query `refetchInterval`).

## Consequences
* Tests cover claim exclusivity (`SKIP LOCKED` does not block a second worker), lease takeover, fencing of a stale
  worker, retry/backoff, exhaustion, graceful release and duplicate-free re-runs (`tests/integration/test_job_queue.py`).
