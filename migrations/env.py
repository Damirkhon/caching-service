"""Alembic environment: runs migrations with the service's own settings and engine factory."""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import make_url
from sqlalchemy.engine import Connection

from caching_service.config import Settings
from caching_service.db.models import Base
from caching_service.db.session import create_engine

config = context.config
# Keep loggers the application configured before invoking migrations in-process.
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

settings = Settings()
target_metadata = Base.metadata


def _is_sqlite() -> bool:
    return make_url(settings.database_url).get_backend_name() == "sqlite"


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=_is_sqlite(),
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    # SQLite cannot ALTER most column properties in place; batch mode rebuilds the table.
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=_is_sqlite(),
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    # The service's engine factory, so a fresh SQLite file gets its directory and pragmas.
    engine = create_engine(settings)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
