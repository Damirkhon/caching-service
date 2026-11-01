import asyncio
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Connection, make_url, text
from sqlalchemy.ext.asyncio import create_async_engine

from caching_service.db.models import Base
from caching_service.db.session import create_engine
from tests.factories import make_settings

pytestmark = pytest.mark.integration

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


@pytest.fixture
def migration_database_url(database_url: str) -> Iterator[str]:
    """A database no other test touches, so migrations start from an empty schema."""
    url = make_url(database_url)
    if url.get_backend_name() == "sqlite":
        yield database_url
        return

    # On Postgres, a throwaway database next to the shared test database (needs CREATEDB).
    name = f"migrations_{uuid.uuid4().hex}"
    asyncio.run(_execute_autocommit(database_url, f'CREATE DATABASE "{name}"'))
    try:
        yield url.set(database=name).render_as_string(hide_password=False)
    finally:
        # FORCE: the server may still list just-closed connections, which would block a plain DROP.
        asyncio.run(_execute_autocommit(database_url, f'DROP DATABASE "{name}" WITH (FORCE)'))


async def _execute_autocommit(database_url: str, statement: str) -> None:
    engine = create_async_engine(database_url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as connection:
            await connection.execute(text(statement))
    finally:
        await engine.dispose()


def alembic_config(monkeypatch: pytest.MonkeyPatch, database_url: str) -> Config:
    # No ini file, so env.py leaves the test session's logging configuration alone.
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    monkeypatch.setenv("CACHE_SERVICE_DATABASE_URL", database_url)
    return config


async def schema_diff(database_url: str) -> list[object]:
    def compare(connection: Connection) -> list[object]:
        return list(compare_metadata(MigrationContext.configure(connection), Base.metadata))

    engine = create_engine(make_settings(database_url=database_url))
    try:
        async with engine.connect() as connection:
            return await connection.run_sync(compare)
    finally:
        await engine.dispose()


async def table_names(database_url: str) -> set[str]:
    def names(connection: Connection) -> set[str]:
        return set(connection.dialect.get_table_names(connection))

    engine = create_engine(make_settings(database_url=database_url))
    try:
        async with engine.connect() as connection:
            return await connection.run_sync(names)
    finally:
        await engine.dispose()


# Synchronous on purpose: env.py starts its own event loop with asyncio.run().
def test_migrations_upgrade_head_matches_models(
    monkeypatch: pytest.MonkeyPatch, migration_database_url: str
) -> None:
    command.upgrade(alembic_config(monkeypatch, migration_database_url), "head")

    assert asyncio.run(schema_diff(migration_database_url)) == []


def test_migrations_downgrade_to_base_and_back(
    monkeypatch: pytest.MonkeyPatch, migration_database_url: str
) -> None:
    config = alembic_config(monkeypatch, migration_database_url)

    command.upgrade(config, "head")
    command.downgrade(config, "base")
    assert asyncio.run(table_names(migration_database_url)) == {"alembic_version"}

    command.upgrade(config, "head")
    assert asyncio.run(table_names(migration_database_url)) >= {"payloads", "transformations"}
