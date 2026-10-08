"""Object storage backends behind a single protocol."""

from __future__ import annotations

from app.config import Settings
from app.storage.base import StorageBackend, StorageError
from app.storage.local import LocalStorage

__all__ = ["StorageBackend", "StorageError", "build_storage"]


def build_storage(settings: Settings) -> StorageBackend:
    if settings.storage_backend == "s3":
        if not settings.s3_bucket:
            raise ValueError("S3_BUCKET is required when STORAGE_BACKEND=s3")
        from app.storage.s3 import S3Storage  # imported lazily: boto3 is only needed for S3

        return S3Storage(
            settings.s3_bucket,
            prefix=settings.s3_prefix,
            endpoint_url=settings.s3_endpoint_url,
            region=settings.s3_region,
        )
    return LocalStorage(settings.storage_local_root)
