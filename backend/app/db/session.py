"""Engine and session factory.

Sync SQLAlchemy with psycopg 3, deliberately:

* the worker is CPU-bound and synchronous; the API's DB work is short indexed queries, which FastAPI runs
  in its threadpool when endpoints are plain ``def``. One sync data layer serves both.
* psycopg 3 supports async too, so moving the API to ``create_async_engine`` later is a contained change.

``prepare_threshold=None`` disables server-side prepared statements, which transaction-mode connection
poolers (PgBouncer, Supabase Supavisor on port 6543) cannot route. The app holds no other session state
(no ``SET``, no ``LISTEN``, schema via ``schema_translate_map``), so it works behind any pooler mode.
"""

from __future__ import annotations

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings


def create_db_engine(settings: Settings, *, schema: str | None = None) -> Engine:
    return create_engine(
        settings.sqlalchemy_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout_s,
        pool_pre_ping=True,  # poolers/NATs drop idle connections; validate on checkout
        pool_recycle=1800,
        connect_args={
            "prepare_threshold": None,
            "connect_timeout": settings.db_connect_timeout_s,
            "application_name": "geomeasure",
        },
        execution_options={"schema_translate_map": {None: schema or settings.db_schema}},
    )


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
