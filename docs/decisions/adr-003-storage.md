# ADR-003: Object-storage abstraction with content-addressed keys

**Status:** Accepted

## Context
Uploads must outlive the request: a worker on another machine processes them, retries re-read them, and the original
file is the only lossless record of the input (source CRS coordinates, Z values, styling). Uploads can be hundreds of
MB, and GDAL needs a real file path.

## Decision
* A small `StorageBackend` protocol (`put_file`, `exists`, `download_to`, `delete`, `check`) in `app/storage/base.py`
  with two implementations: `LocalStorage` (filesystem; atomic write-then-rename) and `S3Storage` (boto3; multipart
  transfers; credentials from the default AWS chain). Selected by `STORAGE_BACKEND`.
* Keys are **content addressed**: `uploads/<sha256[:2]>/<sha256><ext>`. Writes are idempotent; identical uploads are
  stored once. User-supplied filenames never become keys or paths.
* Objects move as files/streams, never as `bytes` in memory.

## Alternatives considered
* **Local disk only** — simplest, but ties API and workers to one host (or a shared volume) and does not survive
  container replacement.
* **Bytes in PostgreSQL (`bytea`/large objects)** — transactional, but bloats the database, its backups and WAL,
  and makes the DB the bottleneck for large files.
* **MinIO in docker-compose** to mirror S3 locally — dropped: the community edition repository was archived in 2026
  and no longer publishes binaries/images. `S3Storage` is tested against `moto` instead, and the compose stack uses a
  shared volume.

## Trade-offs
* Two code paths to keep equivalent — mitigated by the small protocol and the same test cases for both.
* Content addressing means the blob for a re-uploaded file is shared; deleting blobs must check references (a
  garbage-collection job is future work).
* A crash between the blob write and the DB commit leaves an unreferenced blob (harmless; collected by the same
  future GC). The reverse — a job pointing at a missing blob — cannot happen because the blob is written first.

## Consequences
* Production needs no shared volume; API and worker containers are stateless.
* S3 lifecycle rules can archive old uploads to cheaper tiers.
