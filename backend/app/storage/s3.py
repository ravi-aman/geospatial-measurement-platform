"""S3-compatible object storage (AWS S3, or any service speaking the S3 API).

Credentials are *not* configuration of this app: boto3 resolves them from the standard chain (IAM task role
on ECS, instance profile, ``AWS_*`` env vars), so no secret ever needs to be in our settings.
``upload_file`` / ``download_file`` use multipart transfers automatically for large objects.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.storage.base import StorageError, validate_key


class S3Storage:
    name = "s3"

    def __init__(
        self,
        bucket: str,
        *,
        prefix: str = "",
        endpoint_url: str | None = None,
        region: str | None = None,
        client: Any | None = None,
    ) -> None:
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self._client = client or boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=region,
            config=Config(retries={"max_attempts": 5, "mode": "adaptive"}),
        )

    def _key(self, key: str) -> str:
        validate_key(key)
        return f"{self.prefix}/{key}" if self.prefix else key

    def put_file(self, key: str, source: Path, content_type: str | None = None) -> None:
        extra = {"ContentType": content_type} if content_type else None
        try:
            self._client.upload_file(str(source), self.bucket, self._key(key), ExtraArgs=extra)
        except (BotoCoreError, ClientError) as exc:
            raise StorageError(f"S3 upload failed for {key}: {exc}") from exc

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self.bucket, Key=self._key(key))
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return False
            raise StorageError(f"S3 head failed for {key}: {exc}") from exc
        except BotoCoreError as exc:
            raise StorageError(f"S3 head failed for {key}: {exc}") from exc
        return True

    def download_to(self, key: str, destination: Path) -> Path:
        try:
            self._client.download_file(self.bucket, self._key(key), str(destination))
        except (BotoCoreError, ClientError) as exc:
            raise StorageError(f"S3 download failed for {key}: {exc}") from exc
        return destination

    def delete(self, key: str) -> None:
        try:
            self._client.delete_object(Bucket=self.bucket, Key=self._key(key))
        except (BotoCoreError, ClientError) as exc:
            raise StorageError(f"S3 delete failed for {key}: {exc}") from exc

    def check(self) -> None:
        try:
            self._client.head_bucket(Bucket=self.bucket)
        except (BotoCoreError, ClientError) as exc:
            raise StorageError(f"S3 bucket not reachable: {exc}") from exc
