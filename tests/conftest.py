import logging
import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from caching_service.config import ENV_PREFIX
from caching_service.db.models import Base
from caching_service.db.session import create_engine, create_session_factory
from tests.factories import make_settings

TEST_DATABASE_URL_ENV: str = "TEST_DATABASE_URL"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # A developer's exported CACHE_SERVICE_* variables must not leak into any test.
    for name in os.environ:
        if name.startswith(ENV_PREFIX):
            monkeypatch.delenv(name)


@pytest.fixture(autouse=True)
def restore_package_log_level() -> Iterator[None]:
    # Starting an app configures the package logger, which is process-global state; without
    # restoring it, one test's app would change what later tests' log captures see.
    package = logging.getLogger("caching_service")
    level = package.level
    yield
    package.setLevel(level)


@pytest.fixture
def database_url(tmp_path: Path) -> str:
    # A fresh SQLite file per test by default; TEST_DATABASE_URL runs the same suite on Postgres.
    return os.environ.get(TEST_DATABASE_URL_ENV) or f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"


@pytest.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    # Built through the production factory so tests exercise the real per-dialect setup.
    # create_all is much faster than migrations; a separate test proves the two agree.
    engine = create_engine(make_settings(database_url=database_url))
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(engine)
