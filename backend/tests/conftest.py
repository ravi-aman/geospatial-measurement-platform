"""Shared fixtures.

Database tests run against ``TEST_DATABASE_URL`` (env var or ``backend/.env``) inside a *throwaway schema*
created by the real Alembic migrations and dropped at the end of the session - they never touch the
application schema, so they are safe to run against a shared development database. Each test starts from
empty tables. Without ``TEST_DATABASE_URL`` the DB tests are skipped, unless ``REQUIRE_DB_TESTS=1`` (CI),
in which case they fail loudly instead of silently passing.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import Engine, text

from app.config import Settings
from app.container import Container
from app.db.session import create_db_engine, create_session_factory
from app.main import create_app
from app.storage.local import LocalStorage
from app.worker.runner import Worker

BACKEND_DIR = Path(__file__).resolve().parents[1]


class _TestEnv(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")
    test_database_url: str | None = None
    require_db_tests: bool = False


TEST_ENV = _TestEnv()


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if "db_schema" in getattr(item, "fixturenames", ()):
            item.add_marker(pytest.mark.db)


def make_settings(**overrides: Any) -> Settings:
    """Settings isolated from the developer's .env (``_env_file=None``)."""
    base: dict[str, Any] = {
        "log_format": "console",
        "log_level": "WARNING",
        "job_retry_base_delay_s": 0.0,
        "worker_poll_interval_s": 0.05,
        "worker_lease_s": 60,
        "processing_batch_size": 3,  # small batches exercise multi-batch code paths with tiny files
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[call-arg]


# ---------------------------------------------------------------------------- database
@pytest.fixture(scope="session")
def db_url() -> str:
    url = TEST_ENV.test_database_url
    if not url:
        if TEST_ENV.require_db_tests:
            pytest.fail("REQUIRE_DB_TESTS is set but TEST_DATABASE_URL is not configured")
        pytest.skip("TEST_DATABASE_URL not configured")
    return url


@pytest.fixture(scope="session")
def db_schema(db_url: str) -> Iterator[str]:
    schema = f"test_{uuid.uuid4().hex[:10]}"
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.attributes["schema"] = schema
    config.attributes["database_url"] = db_url
    command.upgrade(config, "head")
    engine = create_db_engine(make_settings(database_url=db_url), schema=schema)
    try:
        yield schema
    finally:
        with engine.begin() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()


@pytest.fixture(scope="session")
def db_engine(db_url: str, db_schema: str) -> Iterator[Engine]:
    engine = create_db_engine(make_settings(database_url=db_url, db_pool_size=4), schema=db_schema)
    yield engine
    engine.dispose()


@pytest.fixture
def clean_db(db_engine: Engine, db_schema: str) -> None:
    with db_engine.begin() as conn:
        conn.execute(text(f'TRUNCATE "{db_schema}".features, "{db_schema}".files, "{db_schema}".processing_jobs'))


@pytest.fixture
def settings_factory(tmp_path: Path, db_url: str, db_schema: str) -> Callable[..., Settings]:
    def factory(**overrides: Any) -> Settings:
        values: dict[str, Any] = {
            "database_url": db_url,
            "db_schema": db_schema,
            "storage_local_root": tmp_path / "storage",
        }
        values.update(overrides)
        return make_settings(**values)

    return factory


@pytest.fixture
def settings(settings_factory: Callable[..., Settings]) -> Settings:
    return settings_factory()


@pytest.fixture
def container(settings: Settings, db_engine: Engine, clean_db: None) -> Iterator[Container]:
    built = Container(
        settings=settings,
        engine=db_engine,
        session_factory=create_session_factory(db_engine),
        storage=LocalStorage(settings.storage_local_root),
    )
    yield built
    if built.worker is not None:
        built.worker.stop()


@pytest.fixture
def client(container: Container) -> Iterator[TestClient]:
    with TestClient(create_app(container.settings, container)) as test_client:
        yield test_client


@pytest.fixture
def run_worker(container: Container) -> Callable[[], int]:
    def run() -> int:
        return Worker(container.settings, container.session_factory, container.storage, name="test").run_until_idle()

    return run


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    path = tmp_path / "work"
    path.mkdir()
    return path


@pytest.fixture(autouse=True)
def _no_proxy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Make sure stray AWS credentials on a developer machine never reach real S3 from tests.
    for var in ("AWS_PROFILE", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
