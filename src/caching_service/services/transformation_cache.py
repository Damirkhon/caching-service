"""Cache-aside over the transformer: every value transformed, with as few calls as possible."""

import asyncio
import logging
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from caching_service.db.repositories import TransformationRepository
from caching_service.domain.transformer import Transformer
from caching_service.services.single_flight import SingleFlight

logger = logging.getLogger(__name__)


class TransformationCache:
    """Returns transformed values, calling the transformer only for values never seen before.

    Meant to be created once per process and shared by every request: single-flight only
    coalesces calls made through the same instance.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        transformer: Transformer,
        *,
        single_flight: SingleFlight[str, str] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._transformer = transformer
        self._flights: SingleFlight[str, str] = (
            SingleFlight() if single_flight is None else single_flight
        )

    async def transform_many(self, values: Sequence[str]) -> dict[str, str]:
        """Return ``{value: transformed}`` for every distinct value in ``values``."""
        # Deduplicated in first-seen order: a value repeated within a request costs one call.
        unique = list(dict.fromkeys(values))

        # A short read with no transaction left open: nothing below may hold a connection
        # while the slow transformer runs.
        async with self._session_factory() as session:
            cached = await TransformationRepository(session).get_many(unique)
        misses = [value for value in unique if value not in cached]

        fresh: dict[str, str] = {}
        if misses:
            outputs = await asyncio.gather(*(self._transform_one(value) for value in misses))
            fresh = dict(zip(misses, outputs, strict=True))
            async with self._session_factory.begin() as session:
                await TransformationRepository(session).add_many(fresh)

        logger.info(
            "transform_many requested=%d unique=%d hits=%d misses=%d",
            len(values),
            len(unique),
            len(cached),
            len(misses),
        )
        return cached | fresh

    async def _transform_one(self, value: str) -> str:
        return await self._flights.run(value, lambda: self._transformer.transform(value))
