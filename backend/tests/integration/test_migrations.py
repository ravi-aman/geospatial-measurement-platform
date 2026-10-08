"""Migrations: reversible, and the ORM models never drift from what the migrations actually create."""

from __future__ import annotations

import uuid
from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import MetaData, create_engine, inspect, text

from app.db.base import Base

BACKEND_DIR = Path(__file__).resolve().parents[2]


def alembic_config(url: str, schema: str) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.attributes["schema"] = schema
    config.attributes["database_url"] = url
    return config


def test_upgrade_downgrade_roundtrip_and_no_model_drift(db_url: str) -> None:
    schema = f"mig_{uuid.uuid4().hex[:10]}"
    config = alembic_config(db_url, schema)
    engine = create_engine(db_url, connect_args={"prepare_threshold": None})
    try:
        command.upgrade(config, "head")
        assert set(inspect(engine).get_table_names(schema=schema)) >= {"processing_jobs", "files", "features"}

        # Compare the migrated schema with the models placed in that same schema (reflection does not apply
        # schema_translate_map), restricted to our own tables.
        target = MetaData(naming_convention=Base.metadata.naming_convention)
        for table in Base.metadata.sorted_tables:
            table.to_metadata(target, schema=schema)

        def include_name(name: str | None, type_: str, parent_names: dict[str, str | None]) -> bool:
            if type_ == "schema":
                return name == schema
            if type_ == "table":
                return parent_names.get("schema_name") == schema and name != "alembic_version"
            return True

        with engine.connect() as conn:
            context = MigrationContext.configure(
                conn, opts={"include_schemas": True, "include_name": include_name, "target_metadata": target}
            )
            diffs = compare_metadata(context, target)
        assert diffs == [], f"models and migrations disagree: {diffs}"

        command.downgrade(config, "base")
        assert set(inspect(engine).get_table_names(schema=schema)) == {"alembic_version"}
        command.upgrade(config, "head")  # re-applying after a downgrade must work
    finally:
        with engine.begin() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()
