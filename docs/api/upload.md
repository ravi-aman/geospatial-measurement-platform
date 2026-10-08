# `POST /api/files/`: upload a dataset

Accepts one KML file or one zipped ESRI Shapefile and validates it. It stores the bytes and queues the dataset
for processing. Route: `upload_file` in `backend/app/api/routes/files.py`. Logic:
`UploadService.accept` in `backend/app/services/uploads.py`. Conventions shared by all endpoints (errors,
request ids, trailing slashes) are in [overview.md](overview.md).

## Request

```
POST /api/files/
Content-Type: multipart/form-data; boundary=...
```

| Form field | Required | Description |
|---|---|---|
| `file` | yes | The dataset: a `.kml` file, or a `.zip` holding exactly one Shapefile (`.shp`, `.shx`, `.dbf` required; `.prj`, `.cpg` optional). The format is decided by the filename extension, confirmed by the file's leading bytes. The part's `Content-Type` is not used for detection. |
| `crs` | no | CRS override for data without a CRS or with a wrong one. Format `AUTHORITY:CODE` with authority `EPSG` or `ESRI` (case-insensitive) and a 4-6 digit code, e.g. `EPSG:32643`. It must resolve to a geographic or projected CRS; for a compound CRS the horizontal part is used. PROJ strings and WKT are rejected. An empty value is ignored. The override applies to every layer and takes precedence over the declared CRS (see [CRS handling](../geospatial/crs.md)). |

| Header | Required | Description |
|---|---|---|
| `Idempotency-Key` | no | 8-255 characters of `[A-Za-z0-9._:-]`. Surrounding whitespace and double quotes are stripped first, so the structured-header form `"key"` is accepted. See [Idempotency-Key](#idempotency-key). |
| `Prefer` | no | RFC 7240. `wait=N` asks the server to wait up to N seconds for processing to finish. See [Prefer: wait](#prefer-wait). |
| `X-Request-ID` | no | Correlation id, see [overview](overview.md#request-ids). |

The filename is display metadata only. Directory components (`/` and `\`) and control characters are removed,
Unicode is NFC-normalised, whitespace is collapsed, and the name is truncated to 255 characters keeping the
extension. An empty result becomes `upload`. Storage keys come from the content hash, never from the filename.

### Limits (defaults; current values via `GET /api/capabilities`)

| Limit | Default | Setting | Enforced |
|---|---|---|---|
| File size | 100 MiB (104,857,600 bytes) | `MAX_UPLOAD_BYTES` | upload |
| Request body (file + multipart framing) | file limit + 64 KiB | derived | upload (middleware) |
| ZIP entries | 200 | `MAX_ZIP_ENTRIES` | upload and worker |
| ZIP total uncompressed size (declared, then actual) | 1 GiB | `MAX_ZIP_UNCOMPRESSED_BYTES` | upload and worker |
| ZIP per-entry compression ratio | 1000 | `MAX_ZIP_COMPRESSION_RATIO` | upload and worker |
| Features per file | 1,000,000 | `MAX_FEATURES_PER_FILE` | worker (job fails `TOO_MANY_FEATURES`) |
| Vertices per feature | 1,000,000 | `MAX_VERTICES_PER_FEATURE` | worker (feature fails `GEOMETRY_TOO_COMPLEX`) |

## Responses

| Status | When | Headers | Body |
|---|---|---|---|
| `200 OK` | Idempotent replay: same `Idempotency-Key`, same content | `Location`, `Idempotent-Replayed: true` | File resource of the original upload, in its current state |
| `201 Created` | The job is terminal when the response is built: a `Prefer: wait` finished in time, or a [deduplication](#deduplication) hit on an already finished job | `Location`; `Preference-Applied` if a wait was requested | File resource. `status` is `COMPLETED`, `COMPLETED_WITH_ERRORS` **or `FAILED`**. 201 means "terminal", not "succeeded". |
| `202 Accepted` | Job is `PENDING` or `PROCESSING` | `Location`, `Retry-After: 1`; `Preference-Applied` if a wait was requested but timed out | File resource |
| `400` | `INVALID_IDEMPOTENCY_KEY`; `BAD_REQUEST` (body not parseable) | | Error envelope |
| `413` | `FILE_TOO_LARGE` | | Error envelope |
| `415` | `UNSUPPORTED_FILE_TYPE` | | Error envelope |
| `422` | Upload rejected (codes below) or `VALIDATION_ERROR` | | Error envelope |
| `500` | `INTERNAL_ERROR`, e.g. the storage backend failed while saving the upload | | Error envelope |

`Location` is the root-relative file URL (`/api/files/{id}/`), identical to `links.self`. The body is
the same file resource returned by `GET /api/files/{id}/` ([field reference](files.md#get-apifilesfile_id)).
OpenAPI declares 202 as the default status.

## Validation pipeline

Checks run in this order (first failure wins). A rejected upload persists nothing: the temporary file is
deleted, and the storage write and database rows happen only after every check has passed.

| # | Where | Check | Failure |
|---|---|---|---|
| 1 | `BodySizeLimitMiddleware` | Declared `Content-Length` > `MAX_UPLOAD_BYTES` + 64 KiB | `413 FILE_TOO_LARGE`, `details.max_request_bytes`, `Connection: close` |
| 2 | same, while the body is read | Bytes actually received exceed the same limit (chunked or understated length) | `413 FILE_TOO_LARGE` |
| 3 | FastAPI | Body parseable as multipart; `file` part present | `400 BAD_REQUEST`; `422 VALIDATION_ERROR` |
| 4 | `accept` | Filename sanitised, extension taken (lower-cased last suffix) | never fails |
| 4b | `require_supported_extension` | Extension is `.kml` or `.zip` (checked before any bytes are copied or hashed) | `415 UNSUPPORTED_FILE_TYPE`, `details.extension`, `details.allowed` |
| 5 | `parse_crs_override` | `crs` syntax, known to PROJ, geographic or projected | `422 INVALID_CRS` (`details.crs` for syntax/type errors) |
| 6 | `_validate_idempotency_key` | `Idempotency-Key` syntax | `400 INVALID_IDEMPOTENCY_KEY` |
| 7 | `spool_upload` | Stream the file part to a private temp file, computing SHA-256; size <= `MAX_UPLOAD_BYTES` | `413 FILE_TOO_LARGE`, `details.max_upload_bytes` |
| 8 | `spool_upload` | Size > 0 | `422 EMPTY_FILE` |
| 9 | `detect_format` | Re-derives the format from the (already allowed) extension | — |
| 10 | `detect_format` (`.zip`) | Not the empty-archive signature `PK\x05\x06` | `422 SHAPEFILE_MISSING` |
| 11 | `detect_format` (`.zip`) | Starts with the ZIP local-header signature `PK\x03\x04` | `422 CONTENT_MISMATCH` |
| 12 | `detect_format` (`.kml`) | Does not start with `PK\x03\x04` (KMZ is not supported) | `422 CONTENT_MISMATCH` |
| 13 | `detect_format` (`.kml`) | In the first 64 KiB (UTF-8 BOM allowed): optional XML declaration, comments or processing instructions, then root element `<kml>` (namespace prefix allowed); no `<!DOCTYPE` / `<!ENTITY` | `422 INVALID_KML` |
| 14 | `inspect_shapefile_zip` (`.zip`, central directory only, nothing extracted) | Opens as a ZIP | `422 INVALID_ZIP`, `details.reason` |
| 15 | | Entry count <= 200 | `422 ZIP_TOO_MANY_ENTRIES`, `details.entries`, `details.max_entries` |
| 16 | | Per entry, in archive order: name not absolute, no drive letter, no `..`, no NUL | `422 ZIP_UNSAFE_PATH`, `details.entry` |
| 17 | | ... not a symbolic link | `422 ZIP_UNSAFE_ENTRY`, `details.entry` |
| 18 | | ... not encrypted | `422 ZIP_ENCRYPTED`, `details.entry` |
| 19 | | ... running total of declared sizes <= 1 GiB (directories skipped) | `422 ZIP_TOO_LARGE`, `details.max_uncompressed_bytes` |
| 20 | | ... declared ratio <= 1000 | `422 ZIP_BOMB_SUSPECTED`, `details.entry` |
| 21 | | `__MACOSX/`, `._*`, `.DS_Store`, `Thumbs.db`, `desktop.ini` are ignored. At least one `.shp` | `422 SHAPEFILE_MISSING` |
| 22 | | Exactly one `.shp` | `422 MULTIPLE_SHAPEFILES`, `details.shapefiles` |
| 23 | | `.shx` and `.dbf` with the same path and stem as the `.shp` (case-insensitive) | `422 SHAPEFILE_INCOMPLETE`, `details.missing`, `details.shapefile` |
| 24 | `_replay` (only with `Idempotency-Key`) | Key unused, or used with the same SHA-256 | replay `200`; `422 IDEMPOTENCY_KEY_REUSED` |
| 25 | storage | Save the bytes under `uploads/<sha256[:2]>/<sha256><ext>` unless already present | `500 INTERNAL_ERROR` |
| 26 | one DB transaction | Get or create the job by fingerprint; requeue it if `FAILED` and retryable; insert the `files` row | `500 INTERNAL_ERROR` |
| 27 | route | Optional `Prefer: wait`; pick 200 / 201 / 202 | |

Consequences of the order: the extension allow-list is the first content check, so a `.geojson` is rejected with `415` before the CRS or idempotency key is parsed and before the body is copied and hashed; `crs` and `Idempotency-Key` syntax are checked before the (more expensive) content sniffing. The worker validates again before reading with GDAL: a streaming `defusedxml` scan of KML, and
extraction of the ZIP components with a byte budget and CRC checks. Those failures make the job `FAILED`
([job errors](files.md#job-errors)); they are not HTTP errors. Threat model:
[../architecture/security.md](../architecture/security.md). Format details:
[../geospatial/supported-formats.md](../geospatial/supported-formats.md).

## Idempotency-Key

Lets a client retry an upload (timeout, dropped connection) without creating a duplicate file.

- **Syntax**: `^[A-Za-z0-9._:-]{8,255}$` after stripping whitespace and `"`. Otherwise `400
  INVALID_IDEMPOTENCY_KEY`. A UUID (as the frontend sends, `crypto.randomUUID()`) is valid.
- **Storage**: the key is stored on the `files` row under a unique constraint. Keys never expire and share one
  global namespace (there are no clients or tenants).
- **Replay**: the same key with the same content (SHA-256 of the bytes) returns `200 OK` with
  `Idempotent-Replayed: true` and `Location`. The body is the originally created file (same `id`) in its
  current state. Nothing is created, and `Prefer` is ignored.
- **Conflict**: the same key with different content returns `422 IDEMPOTENCY_KEY_REUSED`.
- **Content and CRS override are compared.** The same key with different bytes *or* a different `crs` is
  `422 IDEMPOTENCY_KEY_REUSED` (the override is part of the request payload). A different filename with the same
  bytes and `crs` replays the original. Use a new key for a deliberate re-upload.
- The replay check runs after content validation (step 24), so a retry must send the full body again, and the
  body must pass validation.
- **Concurrency**: when two requests with the same key race, the unique constraint lets one insert. The other
  replays it (`200`), or gets `IDEMPOTENCY_KEY_REUSED` if its content differs.

## Prefer: wait

- `wait=N` (integer seconds) is matched anywhere in the header, case-insensitively. If the header also
  contains `respond-async`, the server does not wait.
- Effective wait = `min(N, UPLOAD_WAIT_MAX_S)`; the default cap is 30 s, configurable 0-120. `wait=0` means no
  wait.
- The server checks the job every 0.25 s and responds as soon as it is terminal (`201`). At the deadline it
  responds `202` with `Retry-After: 1`.
- `Preference-Applied: wait=<effective seconds>` is sent whenever a wait was performed, whether or not
  processing finished. Example: `Prefer: wait=100` gives `Preference-Applied: wait=30`.
- Not applied to idempotent replays.
- The endpoint is synchronous, so the request occupies one server worker thread while it waits. For large
  files, prefer `202` plus polling.

## Deduplication

Each upload gets its own `files` row (own `id`, `filename`, `created_at`). The processing **job** is shared
by every upload with the same fingerprint:

```
fingerprint = sha256(json({content: sha256(bytes), format, crs: <normalised override or null>,
                           processor_version: PROCESSOR_VERSION, strategy: MEASUREMENT_STRATEGY}))
```

| Existing job with this fingerprint | Result of the new upload (without `Prefer: wait`) |
|---|---|
| none | new job, `PENDING`, `202` |
| `PENDING` / `PROCESSING` | new file row on the existing job, `202` |
| `COMPLETED` / `COMPLETED_WITH_ERRORS` | new file row, results immediately available, `201` (no `Prefer` needed) |
| `FAILED`, `error.retryable = true` (transient failures exhausted the attempts) | job requeued: `PENDING`, `attempts` 0, `error` cleared; `202` |
| `FAILED`, `error.retryable = false` (dataset error, `MAX_ATTEMPTS_EXCEEDED`) | not requeued; `201` with `status: FAILED` |

- The filename is not part of the fingerprint. The `crs` override is, in normalised form (`epsg:32643` and
  `EPSG:32643` are the same), so the same bytes with and without `crs` are two jobs (see the examples below).
- Stored bytes are content-addressed, so identical content is stored once.
- Rationale: [ADR-008](../decisions/adr-008-idempotency-and-caching.md).

## Examples

All examples use `BASE=http://localhost:8000` and the files in `samples/`. Responses below are captured from
a running instance; header names are lower-case as emitted by uvicorn.

### Upload and wait for the result (`201`)

```bash
curl -sS -i -X POST "$BASE/api/files/" \
  -H 'Prefer: wait=10' \
  -F 'file=@samples/shapefile/plots_utm43n.zip'
```

```http
HTTP/1.1 201 Created
date: Thu, 08 Oct 2026 02:14:29 GMT
server: uvicorn
content-length: 1462
content-type: application/json
preference-applied: wait=10
location: /api/files/474b0681-3c4f-4055-9de7-c7e77c5c5b2e/
x-content-type-options: nosniff
x-frame-options: DENY
referrer-policy: no-referrer
cross-origin-opener-policy: same-origin
vary: Origin
x-request-id: 43ba275daa844f9c8274470767802e5b
```

The body is byte-for-byte the file resource shown in [files.md](files.md#get-apifilesfile_id)
(`status: COMPLETED`, `feature_count: 6`, `crs: EPSG:32643`).

### Asynchronous upload (`202`) and polling

```bash
curl -sS -i -X POST "$BASE/api/files/" -F 'file=@samples/kml/parcel_block_wgs84.kml'
```

```http
HTTP/1.1 202 Accepted
date: Thu, 08 Oct 2026 02:14:31 GMT
server: uvicorn
content-length: 1014
content-type: application/json
location: /api/files/38849f3b-02d8-4970-a544-a58833009fab/
retry-after: 1
x-content-type-options: nosniff
x-frame-options: DENY
referrer-policy: no-referrer
cross-origin-opener-policy: same-origin
vary: Origin
x-request-id: 6b9ed0ebe5424d32bd746f4c4781f8de
```

```json
{
  "id": "38849f3b-02d8-4970-a544-a58833009fab",
  "filename": "parcel_block_wgs84.kml",
  "format": "KML",
  "size_bytes": 4300,
  "sha256": "fa2a972d5ab0156e5be075c4aeea6766136727cc18c15e0a1492a75975b79780",
  "status": "PENDING",
  "feature_count": null,
  "crs": null,
  "crs_name": null,
  "crs_source": null,
  "measurement_strategy": "local_equal_area",
  "summary": null,
  "warnings": [],
  "job": {
    "id": "56eb6896-6ba4-41b2-a41e-5efa23ab5461",
    "status": "PENDING",
    "attempts": 0,
    "max_attempts": 3,
    "progress": {"processed_features": 0, "total_features": null},
    "created_at": "2026-10-08T02:14:30.494663Z",
    "started_at": null,
    "finished_at": null,
    "duration_ms": null,
    "error": null
  },
  "created_at": "2026-10-08T02:14:30.494663Z",
  "links": {
    "self": "/api/files/38849f3b-02d8-4970-a544-a58833009fab/",
    "measurements": "/api/files/38849f3b-02d8-4970-a544-a58833009fab/measurements/",
    "features": "/api/files/38849f3b-02d8-4970-a544-a58833009fab/features/",
    "tiles": "/api/files/38849f3b-02d8-4970-a544-a58833009fab/tiles/{z}/{x}/{y}.mvt",
    "job": "/api/jobs/56eb6896-6ba4-41b2-a41e-5efa23ab5461/"
  }
}
```

Poll until terminal (requires `jq`):

```bash
LOC=/api/files/38849f3b-02d8-4970-a544-a58833009fab/
until s=$(curl -sS "$BASE$LOC" | jq -r .status); [[ $s =~ ^(COMPLETED|COMPLETED_WITH_ERRORS|FAILED)$ ]]; do
  sleep 1
done; echo "$s"
```

### Shapefile without `.prj`, then with a `crs` override

```bash
curl -sS -X POST "$BASE/api/files/" -F 'file=@samples/shapefile/plots_no_prj.zip'
curl -sS -X POST "$BASE/api/files/" -F 'file=@samples/shapefile/plots_no_prj.zip' -F 'crs=EPSG:32643'
```

The file resources after processing (captured with `GET /api/files/{id}/`, trimmed):

```jsonc
// without crs: features extracted, nothing measured
{
  "id": "672e6dd6-963c-4232-a02d-d4e382524cdb",
  "filename": "plots_no_prj.zip",
  "status": "COMPLETED_WITH_ERRORS",
  "feature_count": 6,
  "crs": null, "crs_name": null, "crs_source": null,
  "summary": {"total_features": 6, "measured": 0, "failed": 6, "bbox": null,
              "error_counts": {"CRS_MISSING": 6} /* … other summary fields trimmed */},
  "warnings": [
    {"code": "CRS_MISSING",
     "message": "The dataset declares no CRS (e.g. a Shapefile without .prj). Features were extracted but area/length cannot be measured safely. Re-upload with the 'crs' parameter to measure."}
  ]
  // … job, links trimmed
}
// with crs=EPSG:32643: same bytes (same sha256), different fingerprint, so a separate job
{
  "id": "26f3d2d0-18d2-48dc-9a6b-d464c9844102",
  "status": "COMPLETED",
  "feature_count": 6,
  "crs": "EPSG:32643", "crs_name": "WGS 84 / UTM zone 43N", "crs_source": "USER_OVERRIDE",
  "summary": {"measured": 6, "failed": 0, "total_area_m2": 78040.32 /* … */},
  "warnings": []
  // … trimmed
}
```

### Rejections

```bash
curl -sS -X POST "$BASE/api/files/" -F 'file=@parcels.geojson'                        # 415
curl -sS -X POST "$BASE/api/files/" -F 'file=@samples/kml/parcel_block_wgs84.kml' -F 'crs=WGS84'   # 422
```

```json
{"error":{"code":"UNSUPPORTED_FILE_TYPE","message":"Only .kml files and .zip archives containing a Shapefile are accepted.","details":{"extension":".geojson","allowed":[".kml",".zip"]},"request_id":"d15176ecfbcc46d6814e33cfd3ea6ea0"}}
```

```json
{"error":{"code":"INVALID_CRS","message":"crs must be an authority code such as 'EPSG:32643'.","details":{"crs":"WGS84"},"request_id":"8d687c89ce834ab0a2aebee1b57a4f9d"}}
```

### Safe retry with `Idempotency-Key`

```bash
KEY=$(uuidgen)
curl -sS -i -X POST "$BASE/api/files/" -H "Idempotency-Key: $KEY" -F 'file=@samples/kml/parcel_block_wgs84.kml'
# Retry after a network failure: same key, same file
curl -sS -i -X POST "$BASE/api/files/" -H "Idempotency-Key: $KEY" -F 'file=@samples/kml/parcel_block_wgs84.kml'
```

The second call returns `200 OK` with `idempotent-replayed: true` and the same `id` as the first (covered by
`tests/api/test_upload_api.py::TestAccepted::test_idempotent_replay`). Sending a different file with the same
key returns `422 IDEMPOTENCY_KEY_REUSED`.
