"""Cache-aside over the transformer: every value transformed, with as few calls as possible."""

import asyncio
import logging
from collections.abc import Sequence
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from caching_service.db.repositories import TransformationRepository
from caching_service.domain.transformer import Transformer
from caching_service.errors import TransformerError
from caching_service.services.single_flight import SingleFlight

logger = logging.getLogger(__name__)

# Unstored results normally leave within one request's lifetime; this only bounds the ones
# orphaned by a cancelled request or a failed write. Evicting one can cost a duplicate call,
# never a wrong result.
MAX_UNSTORED_RESULTS: Final = 10_000


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
        # Results that are back from the transformer but not yet committed. A request stores
        # its results only after all of its misses finish, while each shared call is released
        # as soon as it returns; without this map, a concurrent request would call again in
        # between. Entries leave once a write containing them has committed (or when evicted).
        self._unstored: dict[str, str] = {}

    async def transform_many(self, values: Sequence[str]) -> dict[str, str]:
        """Return ``{value: transformed}`` for every distinct value in ``values``."""
        # Deduplicated in first-seen order: a value repeated within a request costs one call.
        unique = list(dict.fromkeys(values))

        # A short read with no transaction left open: nothing below may hold a connection
        # while the slow transformer runs.
        # Taken before the read as well as after it: a result another request commits, and so
        # stops holding, while this read runs is still found. Only a value still being
        # transformed when the read starts, and stored before it ends, can be missed and cost
        # one duplicate call.
        held_before_read = {v: self._unstored[v] for v in unique if v in self._unstored}
        async with self._session_factory() as session:
            cached = await TransformationRepository(session).get_many(unique)
        # Finished elsewhere but not yet stored: reused, and stored again by this request,
        # which is harmless (writes ignore existing rows) and saves a result whose own request
        # was cancelled before writing it.
        held = held_before_read | self._unstored
        borrowed = {value: held[value] for value in unique if value not in cached and value in held}
        misses = [value for value in unique if value not in cached and value not in borrowed]

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

        to_store = borrowed | fresh
        try:
            # Stored before any error is raised, so a retry only pays for the values that
            # failed. A database error here takes precedence over transformer failures.
            if to_store:
                async with self._session_factory.begin() as session:
                    await TransformationRepository(session).add_many(to_store)
                for value in to_store:
                    self._unstored.pop(value, None)
        finally:
            logger.info(
                "transform_many requested=%d unique=%d hits=%d misses=%d failed=%d",
                len(values),
                len(unique),
                len(cached) + len(borrowed),
                len(misses),
                len(failures),
            )
        if failures:
            # Anything but a transformer failure is a bug and takes precedence, so it surfaces
            # as an internal error with its traceback instead of a "please retry" response.
            raise next((f for f in failures if not isinstance(f, TransformerError)), failures[0])
        return cached | borrowed | fresh

    async def _transform_one(self, value: str) -> str:
        return await self._flights.run(value, lambda: self._call_transformer(value))

    async def _call_transformer(self, value: str) -> str:
        # Inside single-flight, so callers waiting on someone else's call hold no slot; the
        # timeout starts once a slot is held, bounding the call rather than the queueing.
        async with self._semaphore:
            try:
                async with asyncio.timeout(self._timeout_seconds):
                    result = await self._transformer.transform(value)
            except TimeoutError as exc:
                raise TransformerError from exc
        # Recorded before the shared call completes, so it is visible to other requests by
        # the time single-flight stops coalescing calls for this value.
        self._unstored[value] = result
        while len(self._unstored) > MAX_UNSTORED_RESULTS:
            # Oldest first: dicts keep insertion order.
            del self._unstored[next(iter(self._unstored))]
        return result


def _as_failure(outcome: BaseException) -> BaseException:
    # A shared call cancelled underneath this request (e.g. at shutdown) failed it, but the
    # request itself was not cancelled: re-raising CancelledError would make it look as if it
    # had been, and skip the error responses entirely.
    if isinstance(outcome, asyncio.CancelledError):
        error = TransformerError()
        error.__cause__ = outcome
        return error
    return outcome
