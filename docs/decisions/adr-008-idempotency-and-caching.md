# ADR-008: Fingerprint-based deduplication + Idempotency-Key; no Redis

**Status:** Accepted

## Context
Two different problems are often conflated:
1. **Retried requests** — the client's connection drops after the server accepted the upload; the client resends.
   It must not create a second file.
2. **Repeated content** — the same survey uploaded again (perhaps under another name). Re-processing it wastes CPU.

## Decision
* **Idempotency-Key header** (IETF `draft-ietf-httpapi-idempotency-key-header`): the first request with a key creates
  the file; a retry with the same key and the same content returns the original file with `200` and
  `Idempotent-Replayed: true`; the same key with different content is `422 IDEMPOTENCY_KEY_REUSED`. A unique
  constraint on `files.idempotency_key` makes concurrent duplicates safe (the loser replays the winner). The frontend
  sends a fresh `crypto.randomUUID()` per upload.
* **Job fingerprint** = SHA-256 of (content SHA-256, format, CRS override, `PROCESSOR_VERSION`, measurement strategy),
  unique in `processing_jobs`. A new upload with an existing fingerprint creates a new *file* row (its own filename and
  timestamp) pointing at the existing *job*: no re-processing, instant results. A job that failed for transient
  reasons is re-queued on re-upload; a permanently failed one is returned as is.
* `PROCESSOR_VERSION` is bumped whenever results would change (it has been: v2 changed summary semantics), so cached
  results are never stale.

## Why no Redis
Both mechanisms need durable, transactional state next to the data they protect, which PostgreSQL already provides
(unique constraints, `ON CONFLICT DO NOTHING`). Redis would add a component, a consistency boundary and an eviction
policy without removing any of that logic. HTTP-level caching of immutable tiles (`Cache-Control`, `ETag`) covers the
read side; a CDN can cache them in production.

## Trade-offs
* Deduplication is exact-content only (a re-saved file with different bytes is a new job).
* Shared jobs mean deleting a file must not delete a job still referenced by other files (deletion is future work).
