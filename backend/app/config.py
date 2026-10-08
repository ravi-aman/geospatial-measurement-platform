"""Application configuration.

All runtime configuration comes from environment variables (12-factor), optionally loaded from a local
``.env`` file during development. Secrets (the database URL) are wrapped in ``SecretStr`` so they are never
rendered in logs, reprs or error messages.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MiB = 1024 * 1024
_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ------------------------------------------------------------------ service
    app_name: str = "Geospatial Measurement API"
    environment: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["json", "console"] = "json"
    cors_allow_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    expose_docs: bool = True

    # ------------------------------------------------------------------ database
    database_url: SecretStr = SecretStr("postgresql+psycopg://postgres:postgres@localhost:5432/geomeasure")
    db_schema: str = "geomeasure"
    db_pool_size: int = Field(default=5, ge=1, le=50)
    db_max_overflow: int = Field(default=5, ge=0, le=50)
    db_pool_timeout_s: float = Field(default=10.0, gt=0)
    db_connect_timeout_s: int = Field(default=10, ge=1)

    # ------------------------------------------------------------------ storage
    storage_backend: Literal["local", "s3"] = "local"
    storage_local_root: Path = Path("var/storage")
    s3_bucket: str | None = None
    s3_prefix: str = ""
    s3_endpoint_url: str | None = None
    s3_region: str | None = None
    temp_dir: Path | None = None  # None -> OS default temp directory

    # ------------------------------------------------------------------ upload limits (untrusted input)
    max_upload_bytes: int = Field(default=100 * MiB, ge=1)
    max_zip_entries: int = Field(default=200, ge=1)
    max_zip_uncompressed_bytes: int = Field(default=1024 * MiB, ge=1)
    max_zip_compression_ratio: float = Field(default=1000.0, gt=1)

    # ------------------------------------------------------------------ processing
    max_features_per_file: int = Field(default=1_000_000, ge=1)
    max_vertices_per_feature: int = Field(default=1_000_000, ge=4)
    processing_batch_size: int = Field(default=5_000, ge=1, le=100_000)
    measurement_strategy: Literal["local_equal_area", "utm"] = "local_equal_area"
    distortion_warning_threshold: float = Field(default=0.001, gt=0)  # 0.1 % projected-vs-geodesic

    # ------------------------------------------------------------------ job queue / worker
    worker_poll_interval_s: float = Field(default=1.0, gt=0)
    worker_lease_s: int = Field(default=300, ge=5)
    job_max_attempts: int = Field(default=3, ge=1, le=20)
    job_retry_base_delay_s: float = Field(default=10.0, ge=0)
    embedded_worker: bool = False
    upload_wait_max_s: float = Field(default=30.0, ge=0, le=120)

    # ------------------------------------------------------------------ read API
    page_size_default: int = Field(default=100, ge=1)
    page_size_max: int = Field(default=500, ge=1)
    feature_page_size_max: int = Field(default=200, ge=1)
    tile_max_features: int = Field(default=20_000, ge=1)

    @field_validator("db_schema")
    @classmethod
    def _schema_is_identifier(cls, value: str) -> str:
        if not _IDENTIFIER.fullmatch(value):
            raise ValueError("db_schema must be a lowercase SQL identifier ([a-z_][a-z0-9_]*)")
        return value

    @field_validator("s3_prefix")
    @classmethod
    def _normalise_prefix(cls, value: str) -> str:
        return value.strip("/")

    @property
    def sqlalchemy_url(self) -> str:
        return self.database_url.get_secret_value()


@lru_cache
def get_settings() -> Settings:
    return Settings()
