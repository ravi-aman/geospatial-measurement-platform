# Security

Uploaded geospatial files are **untrusted input** parsed by complex native code (GDAL, expat, GEOS). The design goal is
defence in depth: reject what is obviously hostile at the boundary, re-validate in the worker, constrain resources,
and make the dangerous operations (paths, XML, archives) structurally impossible rather than merely filtered.

## Threat model and defences

| Threat | Defence (where) | Test |
|---|---|---|
| Oversized uploads, disk filling via chunked bodies without `Content-Length` | ASGI middleware counts **actual** body bytes and aborts at `MAX_UPLOAD_BYTES` + 64 KiB; declared `Content-Length` checked first; the upload service enforces the exact file limit while spooling (`app/api/middleware.py`, `app/services/uploads.py`) | `test_chunked_body_without_length_is_counted`, size-limit API tests |
| Extension / MIME spoofing | extension allow-list **and** magic bytes / XML root sniffing; client `Content-Type` is recorded but never trusted (`app/ingestion/sniffing.py`) | spoofed `.kml`/`.zip` tests |
| Path traversal / zip-slip | entry names never become paths: only Shapefile components are extracted, to fixed flat names in a private temp dir; unsafe names (`..`, absolute, drive letters, NUL) are rejected anyway (`app/ingestion/archive.py`) | 6 traversal variants |
| Zip bombs | ≤ 200 entries, ≤ 1 GiB declared total, ≤ 1000:1 per-entry ratio, and a **streaming byte budget** during extraction (headers can lie); CRC verified | bomb/ratio/total/lying-header tests |
| Symlinks, encrypted members | rejected | tests |
| XXE, billion laughs, SSRF via XML | streaming `defusedxml` scan forbids DTDs, entities and external references before GDAL opens the file (`app/ingestion/kml_safety.py`) | XXE + billion-laughs tests |
| KML `<NetworkLink>` (remote fetch) | GDAL does not fetch them; they are counted and reported, never followed | test |
| Resource exhaustion in processing | feature limit (1M), vertex limit per geometry (1M), lease + max attempts stop crash loops, one job per worker process | tests |
| Malicious attribute values | NUL bytes stripped, non-JSON values normalised; values are stored as JSONB and rendered as text by React (no HTML injection) | unit tests (`test_geo_properties.py`) |
| Unsafe filenames | sanitised for display only (path components and control characters removed, NFC, 255 chars); storage keys come from the SHA-256 | tests |
| SQL injection | all SQL via SQLAlchemy bound parameters; the only interpolated identifier (schema) is validated against `^[a-z_][a-z0-9_]{0,62}$` | settings test |
| CRS strings referencing external resources | the `crs` override accepts only `AUTHORITY:CODE` | tests |
| Leaking internals | one error envelope; unhandled exceptions → generic 500 with request id; stack traces only in server logs | test asserts no internal text in 500 body |
| Secrets | `DATABASE_URL` is a `SecretStr` (never in reprs/logs); `.env` git-ignored; logs redact keys like `password`, `token`, `database_url`; S3 credentials only from the AWS default chain | tests |
| Data exposed by Supabase's auto REST API | tables live in a dedicated schema that PostgREST does not expose | — |
| Browser hardening | `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, COOP; CORS limited to configured origins | tests |
| Container | non-root user (uid 10001), slim base, no compilers in the runtime stage | — |
| Dependencies | versions pinned (`constraints.txt`, `package-lock.json`); Dependabot; CI audit job (`pip-audit`, `npm audit`); unexpected packages are investigated (e.g. the shadcn CLI's `cn` package was verified to be first-party before keeping it) | CI |

## Defence in depth: validated twice
The API performs cheap structural checks (magic bytes, ZIP central directory) so obviously bad files never reach
storage or the queue. The worker **re-validates** before GDAL touches the file (full XML scan, ZIP inspection,
streamed extraction) because the worker must not trust what is in storage either.

## Not implemented (production requirements, documented honestly)
* **Authentication / authorisation** and per-tenant isolation — out of the assignment's scope. Files are addressed by
  random UUIDs, which is not access control. Production: OIDC/JWT or API keys at the gateway, an `owner_id` on
  `files`, row-level checks in repositories.
* **Rate limiting** — belongs at the gateway / load balancer (e.g. per-key upload quotas).
* **Malware scanning** of uploads (e.g. ClamAV on the bucket) — relevant if files are ever served back.
* **Sandboxing GDAL** in a separate low-privilege process with seccomp — today GDAL runs in the worker process.
