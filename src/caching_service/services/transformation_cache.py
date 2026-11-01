"""Cache-aside over the transformer: every value transformed, with as few calls as possible."""

import asyncio
import logging
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from caching_service.db.repositories import TransformationRepository
from caching_service.domain.transformer import Transformer
from caching_service.errors import TransformerError
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
        timeout_seconds: float,
        max_concurrency: int,
        single_flight: SingleFlight[str, str] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._transformer = transformer
        self._timeout_seconds = timeout_seconds
        # Process-wide, because it protects the external service, not any single request.
        self._semaphore = asyncio.Semaphore(max_concurrency)
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

        outcomes = await asyncio.gather(
            *(self._transform_one(value) for value in misses), return_exceptions=True
        )
        fresh = {
            value: outcome
            for value, outcome in zip(misses, outcomes, strict=True)
            if isinstance(outcome, str)
        }
        failures = [
            _as_failure(outcome) for outcome in outcomes if isinstance(outcome, BaseException)
        ]

        try:
            # Stored before any error is raised, so a retry only pays for the values that
            # failed. A database error here takes precedence over transformer failures.
            if fresh:
                async with self._session_factory.begin() as session:
                    await TransformationRepository(session).add_many(fresh)
        finally:
            logger.info(
                "transform_many requested=%d unique=%d hits=%d misses=%d failed=%d",
                len(values),
                len(unique),
                len(cached),
                len(misses),
                len(failures),
            )
        if failures:
            # Anything but a transformer failure is a bug and takes precedence, so it surfaces
            # as an internal error with its traceback instead of a "please retry" response.
            raise next((f for f in failures if not isinstance(f, TransformerError)), failures[0])
        return cached | fresh

    async def _transform_one(self, value: str) -> str:
        return await self._flights.run(value, lambda: self._call_transformer(value))

    async def _call_transformer(self, value: str) -> str:
        # Inside single-flight, so callers waiting on someone else's call hold no slot; the
        # timeout starts once a slot is held, bounding the call rather than the queueing.
        async with self._semaphore:
            try:
                async with asyncio.timeout(self._timeout_seconds):
                    return await self._transformer.transform(value)
            except TimeoutError as exc:
                raise TransformerError from exc


def _as_failure(outcome: BaseException) -> BaseException:
    # A shared call cancelled underneath this request (e.g. at shutdown) failed it, but the
    # request itself was not cancelled: re-raising CancelledError would make it look as if it
    # had been, and skip the error responses entirely.
    if isinstance(outcome, asyncio.CancelledError):
        error = TransformerError()
        error.__cause__ = outcome
        return error
    return outcome
