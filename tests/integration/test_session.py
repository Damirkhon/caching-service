from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from caching_service.db.models import Payload
from caching_service.db.session import (
    SQLITE_BUSY_TIMEOUT_MS,
    create_engine,
)
from tests.factories import make_settings

pytestmark = pytest.mark.integration


async def test_sqlite_engine_creates_missing_parent_directory(tmp_path: Path) -> None:
    db_file = tmp_path / "nested" / "dir" / "cache.db"
    engine = create_engine(make_settings(database_url=f"sqlite+aiosqlite:///{db_file}"))
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    finally:
        await engine.dispose()

    assert db_file.exists()


async def test_sqlite_connections_use_wal_and_busy_timeout(tmp_path: Path) -> None:
    engine = create_engine(make_settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 'c.db'}"))
    try:
        async with engine.connect() as connection:
            journal_mode = await connection.scalar(text("PRAGMA journal_mode"))
            busy_timeout = await connection.scalar(text("PRAGMA busy_timeout"))
    finally:
        await engine.dispose()

    assert journal_mode == "wal"
    assert busy_timeout == SQLITE_BUSY_TIMEOUT_MS


async def test_in_memory_sqlite_needs_no_directory() -> None:
    engine = create_engine(make_settings(database_url="sqlite+aiosqlite:///:memory:"))
    try:
        async with engine.connect() as connection:
            assert await connection.scalar(text("SELECT 1")) == 1
    finally:
        await engine.dispose()


async def test_postgres_engine_pings_pooled_connections() -> None:
    # Building the engine does not connect, so this needs no running Postgres.
    engine = create_engine(
        make_settings(database_url="postgresql+asyncpg://user:pw@localhost/cache")
    )
    try:
        assert engine.pool._pre_ping is True
    finally:
        await engine.dispose()


async def test_session_factory_keeps_values_after_commit(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session, session.begin():
        payload = Payload(output_hash="h" * 64, output="A, B")
        session.add(payload)

    # Expired attributes would need a lazy reload, which raises outside a transaction in async.
    assert payload.output == "A, B"
    assert payload.id is not None
