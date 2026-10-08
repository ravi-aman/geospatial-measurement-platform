# `app.ingestion` - untrusted upload handling

Filename sanitising, content sniffing, ZIP inspection/extraction and KML XML-safety scanning. The package depends only
on `app.domain` (enums, errors) and `defusedxml`; it has no FastAPI, database or storage imports, so every rule is
unit-tested in isolation. Threat model: [docs/architecture/security.md](../../../docs/architecture/security.md).

## Why it exists

Uploads are attacker-controlled bytes that end up being parsed by native code (GDAL, expat). Path traversal through
archive entry names, decompression bombs, XML entity attacks and spoofed file types are cheap to attempt and expensive
to suffer. Collecting the defences in one framework-free package keeps them small, reviewable and testable, and lets
both the API and the worker call the same functions.

## How it works

| Module | Function | Role |
|---|---|---|
| `filenames.py` | `sanitize_display_filename`, `file_extension`, `safe_stem` | Display-safe name (path components removed for both `/` and `\`, NFC, control characters stripped, whitespace collapsed, max 255 chars keeping the extension); lower-cased extension; ASCII stem for local temp files. |
| `sniffing.py` | `detect_format(extension, head)` | Extension allow-list (`.kml` -> KML, `.zip` -> SHAPEFILE) **and** leading bytes: a `.zip` must start with `PK\x03\x04`; a `.kml` must not be a ZIP and must look like XML whose root element is `<kml>` (BOM, XML declaration, comments and processing instructions allowed; `<!DOCTYPE`/`<!ENTITY` in the head rejected). |
| `archive.py` | `inspect_shapefile_zip(path, limits)` | Reads only the ZIP central directory: entry count, unsafe names, symlinks, encryption flag, cumulative declared size, per-entry compression ratio; skips `__MACOSX/`, `._*`, `.DS_Store`, `Thumbs.db`, `desktop.ini`; requires exactly one `.shp` with `.shx` and `.dbf` next to it (case-insensitive, any folder); picks up optional `.prj` and `.cpg`; lists every other entry as ignored. |
| `archive.py` | `extract_shapefile(path, archive, dest, limits)` | Streams only the selected components to fixed names `<stem>.<ext>` in a private directory (opened with exclusive create, `xb`), counting bytes per entry and against a total budget; CRC errors surface as `BadZipFile`. |
| `kml_safety.py` | `scan_kml(path)` | Streaming `defusedxml` `iterparse` with `forbid_dtd`, `forbid_entities`, `forbid_external`; checks the root is `<kml>`; counts `<Placemark>` (cleared as it goes) and `<NetworkLink>` elements. |

Two call sites:

1. **API, fail fast** (`app/services/uploads.py`, `UploadService.accept`): sanitise the filename, take its extension,
   spool the body to a temp file (size limit, SHA-256, first 64 KiB kept as `head`), `detect_format`, and for ZIPs
   `inspect_shapefile_zip`. Any rejection is a 4xx and nothing is stored or enqueued.
2. **Worker, re-validate** (`app/services/processing.py`, `JobExecutor._prepare_dataset`): after downloading the
   stored object, KML gets the full `scan_kml`; ZIPs are inspected again and then extracted. The worker does not trust
   storage contents either.

## Inputs

| Function | Input |
|---|---|
| `sanitize_display_filename` | Client filename from the multipart part (`str` or `None`). |
| `detect_format` | Lower-cased extension of the sanitised name; up to `SNIFF_BYTES` (64 KiB) of content. |
| `inspect_shapefile_zip`, `extract_shapefile` | Path to the spooled/downloaded archive; `ZipLimits(max_entries, max_uncompressed_bytes, max_compression_ratio)` built from `MAX_ZIP_ENTRIES` (200), `MAX_ZIP_UNCOMPRESSED_BYTES` (1 GiB), `MAX_ZIP_COMPRESSION_RATIO` (1000). |
| `scan_kml` | Path to the downloaded KML. |

## Outputs

- A display filename (stored in `files.original_filename`; never used for paths or keys).
- `SourceFormat.KML` or `SourceFormat.SHAPEFILE`.
- `ShapefileArchive(stem, components, ignored_entries)`; the worker turns `ignored_entries` into a
  `ZIP_ENTRIES_IGNORED` file warning (first 10 names listed, plus a count).
- The path of the extracted `.shp`, ready for GDAL.
- `KmlScan(placemarks, network_links)`; a non-zero `network_links` becomes a `KML_NETWORK_LINKS_IGNORED` warning.

## Failure modes

Raised as `UploadRejectedError` (HTTP 422 unless noted) at upload time. In the worker the same inspection errors are
converted to `DatasetError`, which fails the job permanently (no retry).

| Code | Raised by | Cause |
|---|---|---|
| `UNSUPPORTED_FILE_TYPE` (415) | `detect_format` | Extension not `.kml` or `.zip` (includes `.kmz`, `.geojson`, a bare `.shp`). |
| `EMPTY_FILE` | `detect_format` / spooling | Zero bytes. |
| `CONTENT_MISMATCH` | `detect_format` | `.zip` without ZIP magic; `.kml` that is a ZIP (KMZ). |
| `INVALID_KML` | `detect_format`, `scan_kml` | Head is not a `<kml>` document; in the worker: not well-formed, wrong root, empty. |
| `SHAPEFILE_MISSING` | `detect_format`, `inspect_shapefile_zip` | Empty archive, or no `.shp`. |
| `INVALID_ZIP` | `inspect_shapefile_zip`, `extract_shapefile` | Unreadable central directory; corrupt member or CRC mismatch during extraction. |
| `ZIP_TOO_MANY_ENTRIES`, `ZIP_TOO_LARGE`, `ZIP_BOMB_SUSPECTED` | `inspect_shapefile_zip`, `extract_shapefile` | Entry count, declared total size or per-entry ratio over the limit; at extraction, an entry producing more than its declared size or the total budget exhausted. |
| `ZIP_UNSAFE_PATH`, `ZIP_UNSAFE_ENTRY`, `ZIP_ENCRYPTED` | `inspect_shapefile_zip` | `..`, absolute, drive-letter or NUL names; symlink entries; encrypted entries. |
| `MULTIPLE_SHAPEFILES`, `SHAPEFILE_INCOMPLETE` | `inspect_shapefile_zip` | More than one `.shp`; `.shx` or `.dbf` missing next to the `.shp`. |
| `KML_UNSAFE` (worker only) | `scan_kml` | DTD, entity declaration or external reference. Defence in depth: a DTD must precede the root element, which the upload-time sniff already rejects, so this is reached only by content that bypassed the API check. |

Size limits (`FILE_TOO_LARGE`, 413) are enforced outside this package, by `BodySizeLimitMiddleware` and the upload
spooler. XML well-formedness is checked only in the worker: a KML whose first 64 KiB look right but which is
malformed further on is accepted (202) and the job then fails with `INVALID_KML`.

## Design decisions

- **Entry names never become paths.** Components are written to `<safe_stem>.<ext>` in a directory the worker owns,
  so traversal is impossible by construction. Unsafe names are still rejected so a hostile archive is reported as
  such instead of being silently "fixed".
- **Validate twice.** Cheap structural checks at the API keep junk out of storage and the queue; the worker repeats
  them (plus the full XML scan and streamed extraction) before GDAL opens anything.
- **Content decides, not metadata.** Extension and magic bytes must agree; the client `Content-Type` is recorded but
  never trusted.
- **Declared sizes are not trusted.** Header-based limits fail fast; the streaming byte counters during extraction
  are the real guarantee.
- **Extract only what GDAL reads** (`.shp`, `.shx`, `.dbf`, `.prj`, `.cpg`). Spatial indexes, `.shp.xml` metadata
  and unrelated files are left in the archive and reported as ignored.
- **One dataset per archive.** Several `.shp` files are rejected rather than merged or chosen arbitrarily.
- **NetworkLinks are reported, never followed.** GDAL does not fetch them either; the warning tells the user their
  remote content is not in the result.

## Scaling considerations

- ZIP inspection reads the central directory only (at most `MAX_ZIP_ENTRIES` records); no data is decompressed at
  upload time.
- Spooling and extraction copy in 1 MiB chunks; only the 64 KiB head is held in memory.
- `scan_kml` streams and clears each `<Placemark>`, so memory stays small. GDAL's KML driver then parses the whole
  document in memory (see `app/geoprocessing/reader.py`), so KML size is effectively bounded by `MAX_UPLOAD_BYTES`.
- An upload is written to disk twice in the API (Starlette's multipart spool, then the hashing spool) and the worker
  reads the full object again. This is linear I/O, acceptable at the 100 MiB limit; larger files would need
  direct-to-storage uploads (*future*).

## Security considerations

- Limits are configuration (`MAX_ZIP_*`), validated by pydantic (`ge=1`, ratio `> 1`).
- The ratio check uses declared sizes per entry (`file_size / max(compress_size, 1)`); the streaming budget backs it
  up when headers lie.
- Display filenames strip C0/C1 control characters, which prevents log and terminal injection through filenames.
- Residual risk: GDAL still parses `.shp`/`.dbf`/KML content in the worker process, unsandboxed. Defects in GDAL
  are mitigated only by the non-root container user and process isolation from the API.

## Testing

| File | Tests | Focus |
|---|---:|---|
| `tests/unit/test_ingestion_archive.py` | 25 | Inspection, six traversal variants, symlink, encryption flag, entry count, ratio and total-size bombs, lying header, extraction budget, CRC, flat extraction |
| `tests/unit/test_ingestion_filenames_and_sniffing.py` | 29 | Filename sanitising and truncation, Unicode, extensions, spoofed KML/ZIP/executable, BOM and comment prefixes, DTD in the head |
| `tests/unit/test_ingestion_kml_safety.py` | 6 | XXE, billion laughs, NetworkLink counting, wrong root, malformed, empty |
| `tests/api/test_upload_api.py` | (subset) | Every rejection code over HTTP, including size limits |
| `tests/integration/test_processing_and_uploads.py` | (subset) | A rejected upload persists nothing |

Hostile inputs are generated in code by `tests/builders.py` (`zip_bytes`, `zip_with_symlink`,
`zip_with_encrypted_flag`, `kml_document(prolog=...)`).

## Future improvements

- KMZ support (a ZIP containing `doc.kml`), currently rejected as `CONTENT_MISMATCH`.
- Run the full `scan_kml` at upload time as well, so malformed KML is rejected with a 4xx instead of a failed job
  (costs a full streaming parse inside the request).
- Run GDAL in a separate low-privilege process (seccomp/namespace sandbox).
- Verify the stored object's SHA-256 in the worker after download (the hash is known but not re-checked today).
- Accept several Shapefiles per archive as separate layers.
