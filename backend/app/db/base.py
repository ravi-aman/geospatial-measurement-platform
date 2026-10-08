"""Declarative base.

Tables are declared *without* a schema. The engine maps ``None`` to ``settings.db_schema`` through
SQLAlchemy's ``schema_translate_map``, which gives us:

* a dedicated schema in production (Supabase exposes the ``public`` schema through its auto-generated REST
  API; tables in a separate schema are not reachable that way), and
* per-test-session schemas for isolated integration tests against a shared database,

without string-building SQL or relying on ``search_path`` (which is session state that connection poolers
in transaction mode do not preserve).
"""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
