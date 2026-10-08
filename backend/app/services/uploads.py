"""Upload acceptance: validate at the boundary, store immutably, enqueue transactionally.

Flow (docs/architecture/data-flow.md):

1. Stream the body to a private temp file while computing SHA-256 and enforcing the size limit
   (``Content-Length`` is not trusted - it can lie or be absent with chunked encoding).
2. Cheap structural validation: extension allow-list + magic bytes, ZIP central-directory inspection,
   CRS override syntax. Bad input fails fast with 4xx and nothing is persisted.
3. Store the bytes under a content-addressed key (``uploads/ab/abcd...ef.kml``) - writes are idempotent
   and identical content is stored once.
4. One DB transaction: get-or-create the job by *fingerprint* (content + options + processor version) and
   insert the ``files`` row. Enqueueing is the job row itself, so there is no dual-write between a
   database and a separate broker that could disagree after a crash.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import tempfile
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app import PROCESSOR_VERSION
from app.config import Settings
from app.db.repositories import FileRepository, JobRepository
from app.domain.enums import JobStatus, SourceFormat
from app.domain.errors import InvalidRequestError, PayloadTooLargeError, UploadRejectedError
from app.geoprocessing.crs import describe, parse_crs_override
from app.ingestion.archive import ZipLimits, inspect_shapefile_zip
from app.ingestion.filenames import file_extension, sanitize_display_filename
from app.ingestion.sniffing import SNIFF_BYTES, detect_format, require_supported_extension
from app.storage import StorageBackend

_CHUNK = 1024 * 1024
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._:\-]{8,255}$")


@dataclass(frozen=True, slots=True)
class IncomingUpload:
    filename: str | None
    content_type: str | None
    stream: BinaryIO
    crs: str | None = None
    idempotency_key: str | None = None
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class UploadResult:
    file_id: uuid.UUID
    job_id: uuid.UUID
    replayed: bool  # True: idempotent replay of an earlier request, nothing new was created
    job_reused: bool  # True: identical content+options were already processed/queued (dedup cache hit)


@dataclass(frozen=True, slots=True)
class SpooledUpload:
    path: Path
    size: int
    sha256: str
    head: bytes


@contextlib.contextmanager
def spool_upload(stream: BinaryIO, *, max_bytes: int, temp_dir: Path | None) -> Iterator[SpooledUpload]:
    fd, name = tempfile.mkstemp(prefix="geomeasure-upload-", dir=temp_dir)
    path = Path(name)
    try:
        hasher = hashlib.sha256()
        size = 0
        head = bytearray()
        with os.fdopen(fd, "wb") as out:
            while chunk := stream.read(_CHUNK):
                size += len(chunk)
                if size > max_bytes:
                    raise PayloadTooLargeError(
                        f"The file exceeds the maximum upload size of {max_bytes} bytes.",
                        details={"max_upload_bytes": max_bytes},
                    )
                if len(head) < SNIFF_BYTES:
                    head.extend(chunk[: SNIFF_BYTES - len(head)])
                hasher.update(chunk)
                out.write(chunk)
        if size == 0:
            raise UploadRejectedError("The uploaded file is empty.", code="EMPTY_FILE")
        yield SpooledUpload(path, size, hasher.hexdigest(), bytes(head))
    finally:
        with contextlib.suppress(FileNotFoundError):
            path.unlink()


def job_fingerprint(
    *, content_sha256: str, source_format: SourceFormat, crs_override: str | None, measurement_strategy: str
) -> str:
    material = {
        "content": content_sha256,
        "format": source_format.value,
        "crs": crs_override,
        "processor_version": PROCESSOR_VERSION,
        "strategy": measurement_strategy,
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()


class UploadService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        storage: StorageBackend,
        settings: Settings,
        on_enqueued: Callable[[], None] | None = None,
    ) -> None:
        self._sessions = session_factory
        self._storage = storage
        self._settings = settings
        self._on_enqueued = on_enqueued
        self._zip_limits = ZipLimits(
            settings.max_zip_entries, settings.max_zip_uncompressed_bytes, settings.max_zip_compression_ratio
        )

    def accept(self, upload: IncomingUpload) -> UploadResult:
        filename = sanitize_display_filename(upload.filename)
        extension = file_extension(filename)
        require_supported_extension(extension)  # cheapest check first: reject before copying/hashing
        override = parse_crs_override(upload.crs)
        override_id = describe(override)[0] if override is not None else None
        idempotency_key = self._validate_idempotency_key(upload.idempotency_key)

        with spool_upload(
            upload.stream, max_bytes=self._settings.max_upload_bytes, temp_dir=self._settings.temp_dir
        ) as spooled:
            source_format = detect_format(extension, spooled.head)
            if source_format is SourceFormat.SHAPEFILE:
                inspect_shapefile_zip(spooled.path, self._zip_limits)

            if idempotency_key is not None:
                replay = self._replay(idempotency_key, spooled.sha256, override_id)
                if replay is not None:
                    return replay

            storage_key = f"uploads/{spooled.sha256[:2]}/{spooled.sha256}{extension}"
            if not self._storage.exists(storage_key):
                content_type = "application/zip" if extension == ".zip" else "application/vnd.google-earth.kml+xml"
                self._storage.put_file(storage_key, spooled.path, content_type)

            try:
                result = self._register(
                    upload, filename, spooled, source_format, storage_key, override_id, idempotency_key
                )
            except IntegrityError:
                # Two concurrent requests with the same Idempotency-Key: the loser replays the winner.
                if idempotency_key is None:
                    raise
                replay = self._replay(idempotency_key, spooled.sha256, override_id)
                if replay is None:
                    raise
                return replay

        if self._on_enqueued is not None:
            self._on_enqueued()
        return result

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _validate_idempotency_key(value: str | None) -> str | None:
        if value is None:
            return None
        key = value.strip().strip('"')  # tolerate the structured-header quoted form
        if not _IDEMPOTENCY_KEY.fullmatch(key):
            raise InvalidRequestError(
                "Idempotency-Key must be 8-255 characters of [A-Za-z0-9._:-].", code="INVALID_IDEMPOTENCY_KEY"
            )
        return key

    def _replay(self, key: str, sha256: str, crs_override: str | None) -> UploadResult | None:
        with self._sessions() as session:
            existing = FileRepository(session).get_by_idempotency_key(key)
            if existing is None:
                return None
            if existing.content_sha256 != sha256 or existing.job.crs_override != crs_override:
                raise UploadRejectedError(
                    "This Idempotency-Key was already used with a different file or CRS override.",
                    code="IDEMPOTENCY_KEY_REUSED",
                )
            return UploadResult(existing.id, existing.job_id, replayed=True, job_reused=True)

    def _register(
        self,
        upload: IncomingUpload,
        filename: str,
        spooled: SpooledUpload,
        source_format: SourceFormat,
        storage_key: str,
        override_id: str | None,
        idempotency_key: str | None,
    ) -> UploadResult:
        fingerprint = job_fingerprint(
            content_sha256=spooled.sha256,
            source_format=source_format,
            crs_override=override_id,
            measurement_strategy=self._settings.measurement_strategy,
        )
        with self._sessions.begin() as session:
            jobs = JobRepository(session)
            job, created = jobs.get_or_create(
                fingerprint=fingerprint,
                content_sha256=spooled.sha256,
                source_format=source_format,
                storage_key=storage_key,
                crs_override=override_id,
                processor_version=PROCESSOR_VERSION,
                measurement_strategy=self._settings.measurement_strategy,
                max_attempts=self._settings.job_max_attempts,
                request_id=upload.request_id,
            )
            if not created and job.status == JobStatus.FAILED and job.error_retryable:
                jobs.requeue(job.id, upload.request_id)
            file = FileRepository(session).create(
                job_id=job.id,
                original_filename=filename,
                content_type=(upload.content_type or "")[:255] or None,
                size_bytes=spooled.size,
                content_sha256=spooled.sha256,
                idempotency_key=idempotency_key,
                request_id=upload.request_id,
            )
            return UploadResult(file.id, job.id, replayed=False, job_reused=not created)
