"""Coalescing of concurrent calls that share a key."""

import asyncio
from collections.abc import Awaitable, Callable


class SingleFlight[K, V]:
    """Coalesce concurrent calls that share a key into one execution.

    Process-local by design: a single service process is the deployment target, and several
    replicas would still be correct, only making the occasional duplicate call.
    """

    def __init__(self) -> None:
        self._in_flight: dict[K, asyncio.Future[V]] = {}

    async def run(self, key: K, fn: Callable[[], Awaitable[V]]) -> V:
        # No await between the lookup and the insert, so on one event loop the pair is atomic
        # and needs no lock. Tests rely on this too: a caller counted as entering run() is
        # already waiting on the shared call. Keep any new await below the shield.
        flight = self._in_flight.get(key)
        # A failed flight stays registered until its release callback runs on the next loop
        # iteration; a caller arriving in between must retry rather than inherit the failure.
        if flight is None or _failed(flight):
            # A separate task, so the call outlives any one caller being cancelled.
            flight = asyncio.ensure_future(fn())
            self._in_flight[key] = flight
            flight.add_done_callback(lambda done: self._release(key, done))
        # Shielded: a cancelled caller (a client disconnecting) must not cancel the call that
        # other callers are still waiting on.
        return await asyncio.shield(flight)

    def _release(self, key: K, flight: asyncio.Future[V]) -> None:
        # Removed even on failure, so the next caller retries instead of reusing the error.
        # Only if still registered: a retry may already have replaced it.
        if self._in_flight.get(key) is flight:
            del self._in_flight[key]
        # Marks the exception as retrieved; if every caller was cancelled, nobody else would,
        # and asyncio would warn that it was never retrieved.
        if not flight.cancelled():
            flight.exception()


def _failed[T](flight: asyncio.Future[T]) -> bool:
    return flight.done() and (flight.cancelled() or flight.exception() is not None)
