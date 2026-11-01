"""Engine and session factories, with the per-dialect tuning each database needs."""

from pathlib import Path
from typing import Any, Final

from sqlalchemy import URL, event, make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from caching_service.config import Settings

# How long a SQLite writer waits for the lock before failing with "database is locked".
SQLITE_BUSY_TIMEOUT_MS: Final = 5000


def create_engine(settings: Settings) -> AsyncEngine:
    url = make_url(settings.database_url)
    if url.get_backend_name() != "sqlite":
        # Detects connections dropped by a database restart instead of failing a request.
        return create_async_engine(url, pool_pre_ping=True)

    _create_parent_directory(url)
    engine = create_async_engine(url)
    event.listen(engine.sync_engine, "connect", _configure_sqlite_connection)
    return engine


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # Services return loaded values after their transaction closes, and async code cannot
    # lazily reload expired attributes.
    return async_sessionmaker(engine, expire_on_commit=False)


def _create_parent_directory(url: URL) -> None:
    # SQLite creates the database file on first connect, but not the directory holding it.
    if url.database and url.database != ":memory:":
        Path(url.database).parent.mkdir(parents=True, exist_ok=True)


# Any: SQLAlchemy passes the driver's raw DBAPI connection and its pool record, neither typed.
def _configure_sqlite_connection(dbapi_connection: Any, _connection_record: Any) -> None:
    # WAL lets readers run alongside the single writer; the busy timeout makes a concurrent
    # writer wait for the lock instead of failing immediately.
    # The timeout goes first: switching a new database to WAL itself needs the write lock.
    cursor = dbapi_connection.cursor()
    cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()
