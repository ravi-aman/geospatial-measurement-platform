"""Upload (``files``) persistence."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.db.models import FileUpload


class FileRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        job_id: uuid.UUID,
        original_filename: str,
        content_type: str | None,
        size_bytes: int,
        content_sha256: str,
        idempotency_key: str | None,
        request_id: str | None,
    ) -> FileUpload:
        upload = FileUpload(
            id=uuid.uuid4(),
            job_id=job_id,
            original_filename=original_filename,
            content_type=content_type,
            size_bytes=size_bytes,
            content_sha256=content_sha256,
            idempotency_key=idempotency_key,
            request_id=request_id,
        )
        self.session.add(upload)
        self.session.flush()
        return upload

    def get(self, file_id: uuid.UUID) -> FileUpload | None:
        return self.session.get(FileUpload, file_id)

    def get_by_idempotency_key(self, key: str) -> FileUpload | None:
        return self.session.execute(select(FileUpload).where(FileUpload.idempotency_key == key)).scalar_one_or_none()

    def list_recent(self, *, limit: int, before: tuple[datetime, uuid.UUID] | None = None) -> list[FileUpload]:
        """Newest first, keyset-paginated on (created_at, id)."""
        stmt = select(FileUpload).order_by(FileUpload.created_at.desc(), FileUpload.id.desc()).limit(limit)
        if before is not None:
            created_at, file_id = before
            stmt = stmt.where(
                or_(
                    FileUpload.created_at < created_at,
                    and_(FileUpload.created_at == created_at, FileUpload.id < file_id),
                )
            )
        return list(self.session.execute(stmt).scalars())
