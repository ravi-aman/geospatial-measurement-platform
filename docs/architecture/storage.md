# Object storage

Uploaded files are kept as immutable objects outside the database. The database stores only the key
(`processing_jobs.storage_key`). Code: `backend/app/storage/`. Decision record:
[ADR-003](../decisions/adr-003-storage.md).

## Contract

Business logic depends on one small protocol (`app/storage/base.py`):

| Method | Semantics |
|---|---|
| `put_file(key, source, content_type=None)` | Store the local file `source` under `key`. Idempotent for the same key and bytes. |
| `exists(key) -> bool` | Whether the object exists. |
| `download_to(key, destination) -> Path` | Copy the object to a local path the caller owns. |
| `delete(key)` | Remove the object (not used by application code today). |
| `check()` | Raise `StorageError` if the backend is unusable; used by `GET /ready`. |

The contract is **file-oriented**: objects move as files or streams, never as `bytes` in memory, because uploads
can be 100 MiB and GDAL needs a real local path anyway. Every backend failure is raised as `StorageError`, which
the worker classifies as transient (retry with backoff).

`build_storage(settings)` selects the implementation from `STORAGE_BACKEND`; `boto3` is imported only when S3 is
selected.

## Keys

```
uploads/<first 2 hex chars of sha256>/<sha256><extension>      e.g. uploads/0f/0f2d1c...d916.zip
```

- **Content-addressed.** Identical bytes map to one object, so storing is naturally idempotent and duplicate
  uploads cost no extra space. The two-character prefix keeps directories small on local disks.
- **Never derived from user input.** The client filename is display metadata only; traversal through keys is
  impossible by construction.
- **Validated anyway.** `validate_key` accepts only `^[a-z0-9][a-z0-9/._-]{0,511}$` and rejects `..`, a leading `/`
  and `//`.

The upload service calls `exists()` first and skips `put_file()` when the object is already there. Two concurrent
uploads of the same bytes may both write; since the content is identical, the result is the same.

## Implementations

### `LocalStorage` (development, single host, Docker Compose shared volume)

- Root `STORAGE_LOCAL_ROOT` (default `var/storage`, `/data/storage` in the image), created on start-up.
- Paths are resolved and checked with `is_relative_to(root)` as a second guard after key validation.
- **Atomic writes**: copy to a temp file in the target directory (`mkstemp`), then `replace()` onto the final name,
  so readers never observe a partial object; the temp file is removed on failure.
- `check()` verifies the root is writable.
- Sharing between API and workers requires a shared filesystem (the Compose `uploads` volume). It does not suit
  multi-host deployments.

### `S3Storage` (production; any S3-compatible service)

- Settings: `S3_BUCKET` (required), `S3_PREFIX` (optional, slashes trimmed), `S3_REGION`, `S3_ENDPOINT_URL` (for
  non-AWS S3-compatible services).
- Credentials are **not** application configuration: boto3 resolves them from its default chain (ECS task role,
  instance profile, `AWS_*` environment variables).
- Client retries: `{"max_attempts": 5, "mode": "adaptive"}`.
- `upload_file` / `download_file` (boto3 managed transfers; multipart for large objects), `head_object` for
  `exists` (404/`NoSuchKey`/`NotFound` mean "missing", any other error is a `StorageError`), `head_bucket` for
  `check`.
- Objects get a `ContentType` chosen by the upload service from the validated extension (`application/zip` or
  `application/vnd.google-earth.kml+xml`). No encryption parameters are sent; the bucket's default encryption
  applies.
- Tested only against moto (`tests/unit/test_storage.py`), not against real AWS.

## Where storage is used

| Step | Call | Failure handling |
|---|---|---|
| Upload (API) | `exists()`, then `put_file()` from the spooled temp file | A `StorageError` is not an `AppError`, so the client receives a generic 500 `INTERNAL_ERROR` with the request id; nothing is enqueued. |
| Processing (worker) | `download_to(key, <job temp dir>/upload.bin)` | `StorageError` -> job retried with backoff (`INFRASTRUCTURE_ERROR`), then `FAILED` with `retryable=true` after `JOB_MAX_ATTEMPTS`. Re-uploading the file requeues such a job. |
| Readiness | `check()` | `/ready` reports `"storage": "unavailable"` and returns 503. |

The upload is written to storage **before** the database transaction that creates the job and the `files` row. If
that transaction fails, the object stays in storage without a referencing row (an orphan). Because keys are
content-addressed, a retry of the same upload reuses it.

## Properties and limitations

| Property | Status |
|---|---|
| Immutability | Objects are never rewritten with different bytes (same key implies same content). |
| Integrity check on read | Not implemented: the worker does not re-hash the downloaded object against `content_sha256`. |
| Deletion / retention | Not implemented: nothing calls `delete()`; there is no lifecycle policy in the repository. Deleting an object would make its job impossible to re-run. |
| Orphan cleanup | Not implemented (see above). |
| Direct-to-storage uploads (pre-signed URLs) | Not implemented; every upload passes through the API. Listed in [scalability.md](scalability.md) for files larger than the 100 MiB limit. |
| Encryption | Relies on the bucket default for S3, on the host filesystem for local storage. |

Production IAM policy and bucket settings: [../deployment/production.md](../deployment/production.md#uploads-s3).
