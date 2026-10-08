"""Alembic environment.

The target schema is configurable (``DB_SCHEMA`` / ``config.attributes["schema"]``) so the same migrations
create the production schema and isolated per-run test schemas. The schema is created if missing and the
``alembic_version`` table lives inside it, so independent schemas never share migration state.
"""

from __future__ import annotations

import re

from alembic import context
from sqlalchemy import create_engine, pool, text

from app.config import get_settings
from app.db import models  # noqa: F401  (registers tables on Base.metadata)
from app.db.base import Base

config = context.config
settings = get_settings()


def _schema() -> str:
    schema = str(config.attributes.get("schema") or settings.db_schema)
    # Validated by Settings too; re-checked here because it is interpolated into DDL below.
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", schema):
        raise ValueError(f"invalid schema name: {schema!r}")
    return schema


def run_migrations_offline() -> None:
    context.configure(
        url=settings.sqlalchemy_url,
        target_metadata=Base.metadata,
        literal_binds=True,
        version_table_schema=_schema(),
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    schema = _schema()
    url = config.attributes.get("database_url") or settings.sqlalchemy_url
    engine = create_engine(url, poolclass=pool.NullPool, connect_args={"prepare_threshold": None})
    with engine.connect() as connection:
        connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
        connection.commit()
        context.configure(
            connection=connection,
            target_metadata=Base.metadata,
            version_table_schema=schema,
            transaction_per_migration=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
