"""Initial schema: processing_jobs, files, features (+ PostGIS).

Revision ID: 0001
Revises:
Create Date: 2026-10-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geometry
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JOB_STATUSES = "'PENDING', 'PROCESSING', 'COMPLETED', 'COMPLETED_WITH_ERRORS', 'FAILED'"
MEASUREMENT_STATUSES = "'MEASURED', 'NOT_APPLICABLE', 'UNSUPPORTED', 'FAILED'"

# Supabase keeps extensions in the `extensions` schema (on the default search_path); plain PostgreSQL
# installs into the default schema. Either way `geometry` resolves without schema-qualifying it.
ENABLE_POSTGIS = """
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'postgis') THEN
    IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'extensions') THEN
      EXECUTE 'CREATE EXTENSION postgis WITH SCHEMA extensions';
    ELSE
      EXECUTE 'CREATE EXTENSION postgis';
    END IF;
  END IF;
END
$$;
"""


def _schema() -> str:
    return op.get_context().version_table_schema  # type: ignore[no-any-return]


def _ts(name: str, *, nullable: bool = True, default: bool = False) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable,
                     server_default=sa.func.now() if default else None)


def upgrade() -> None:
    schema = _schema()
    op.execute(ENABLE_POSTGIS)

    op.create_table(
        "processing_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("source_format", sa.String(16), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("crs_override", sa.String(32)),
        sa.Column("processor_version", sa.Integer(), nullable=False),
        sa.Column("measurement_strategy", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        _ts("run_after", nullable=False, default=True),
        sa.Column("locked_by", sa.String(128)),
        _ts("lease_expires_at"),
        sa.Column("request_id", sa.String(64)),
        _ts("started_at"),
        _ts("finished_at"),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("total_features", sa.Integer()),
        sa.Column("processed_features", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("measured_features", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("not_applicable_features", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unsupported_features", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_features", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("crs", sa.String(64)),
        sa.Column("crs_name", sa.Text()),
        sa.Column("crs_source", sa.String(32)),
        sa.Column("crs_wkt", sa.Text()),
        sa.Column("summary", postgresql.JSONB()),
        sa.Column("warnings", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_message", sa.Text()),
        sa.Column("error_retryable", sa.Boolean(), nullable=False, server_default=sa.false()),
        _ts("created_at", nullable=False, default=True),
        _ts("updated_at", nullable=False, default=True),
        sa.UniqueConstraint("fingerprint", name="uq_processing_jobs_fingerprint"),
        sa.CheckConstraint(f"status IN ({JOB_STATUSES})", name="ck_processing_jobs_status_valid"),
        sa.CheckConstraint("source_format IN ('KML', 'SHAPEFILE')", name="ck_processing_jobs_source_format_valid"),
        sa.CheckConstraint("attempts >= 0 AND max_attempts >= 1", name="ck_processing_jobs_attempts_valid"),
        schema=schema,
    )
    op.create_index("ix_processing_jobs_content_sha256", "processing_jobs", ["content_sha256"], schema=schema)
    op.create_index("ix_processing_jobs_claimable", "processing_jobs", ["run_after"], schema=schema,
                    postgresql_where=sa.text("status = 'PENDING'"))
    op.create_index("ix_processing_jobs_leases", "processing_jobs", ["lease_expires_at"], schema=schema,
                    postgresql_where=sa.text("status = 'PROCESSING'"))

    op.create_table(
        "files",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("job_id", sa.Uuid(), sa.ForeignKey(f"{schema}.processing_jobs.id", ondelete="RESTRICT",
                                                      name="fk_files_job_id_processing_jobs"), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(255)),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(255)),
        sa.Column("request_id", sa.String(64)),
        _ts("created_at", nullable=False, default=True),
        sa.UniqueConstraint("idempotency_key", name="uq_files_idempotency_key"),
        sa.CheckConstraint("size_bytes > 0", name="ck_files_size_positive"),
        schema=schema,
    )
    op.create_index("ix_files_job_id", "files", ["job_id"], schema=schema)
    op.create_index("ix_files_created_at_id", "files", ["created_at", "id"], schema=schema)

    op.create_table(
        "features",
        sa.Column("job_id", sa.Uuid(), sa.ForeignKey(f"{schema}.processing_jobs.id", ondelete="CASCADE",
                                                      name="fk_features_job_id_processing_jobs"), nullable=False),
        sa.Column("feature_index", sa.Integer(), nullable=False),
        sa.Column("layer", sa.Text(), nullable=False),
        sa.Column("source_fid", sa.BigInteger()),
        sa.Column("name", sa.Text()),
        sa.Column("geometry_type", sa.String(32)),
        sa.Column("has_z", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("vertex_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("geom", Geometry("GEOMETRY", srid=4326, spatial_index=False)),
        sa.Column("geom_raw", Geometry("GEOMETRY", srid=-1, spatial_index=False)),
        sa.Column("geom_repaired", Geometry("GEOMETRY", srid=4326, spatial_index=False)),
        sa.Column("properties", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("is_valid", sa.Boolean()),
        sa.Column("validity_reason", sa.Text()),
        sa.Column("measurement_status", sa.String(32), nullable=False),
        sa.Column("area_m2", sa.Double()),
        sa.Column("perimeter_m", sa.Double()),
        sa.Column("length_m", sa.Double()),
        sa.Column("projected_crs", sa.Text()),
        sa.Column("projection_method", sa.String(32)),
        sa.Column("geodesic_area_m2", sa.Double()),
        sa.Column("geodesic_length_m", sa.Double()),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_message", sa.Text()),
        sa.Column("issues", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.PrimaryKeyConstraint("job_id", "feature_index", name="pk_features"),
        sa.CheckConstraint(f"measurement_status IN ({MEASUREMENT_STATUSES})",
                           name="ck_features_measurement_status_valid"),
        sa.CheckConstraint("geom IS NULL OR geom_raw IS NULL", name="ck_features_one_geometry_representation"),
        sa.CheckConstraint("area_m2 IS NULL OR area_m2 >= 0", name="ck_features_area_non_negative"),
        sa.CheckConstraint("length_m IS NULL OR length_m >= 0", name="ck_features_length_non_negative"),
        schema=schema,
    )
    op.create_index("ix_features_geom", "features", ["geom"], schema=schema, postgresql_using="gist")
    op.create_index("ix_features_job_id_area_m2", "features", ["job_id", "area_m2"], schema=schema)
    op.create_index("ix_features_job_id_length_m", "features", ["job_id", "length_m"], schema=schema)


def downgrade() -> None:
    schema = _schema()
    op.drop_table("features", schema=schema)
    op.drop_table("files", schema=schema)
    op.drop_table("processing_jobs", schema=schema)
