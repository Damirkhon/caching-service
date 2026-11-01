"""Readiness: whether the service can currently reach its database."""

import asyncio
import logging
from typing import Final

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

# Below typical container health-check timeouts, so a hung database reads as unhealthy
# instead of the probe itself timing out.
HEALTH_CHECK_TIMEOUT_SECONDS: Final = 2.0


class HealthService:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def database_is_reachable(self) -> bool:
        try:
            async with asyncio.timeout(HEALTH_CHECK_TIMEOUT_SECONDS):
                async with self._engine.connect() as connection:
                    await connection.execute(text("SELECT 1"))
        # Drivers raise their own connection errors (OSError subclasses) as well as
        # SQLAlchemy's wrapped ones.
        except (SQLAlchemyError, OSError, TimeoutError):
            logger.warning("database health check failed", exc_info=True)
            return False
        return True
