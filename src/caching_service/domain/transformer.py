"""The transformer: a slow external dependency that callers reach only through a protocol."""

import asyncio
import logging
from typing import Protocol

logger = logging.getLogger(__name__)


class Transformer(Protocol):
    """Anything that turns one string into another, possibly slowly and possibly failing.

    Timeouts, concurrency limits and caching are the caller's policy, so every
    implementation gets them without reimplementing them.
    """

    async def transform(self, value: str) -> str: ...


class SimulatedTransformer:
    """Stand-in for the external transformation service.

    Uppercases its input after an artificial delay. The delay is what makes caching worth
    having: without it, a cache hit and a miss would cost the same.
    """

    def __init__(self, latency_seconds: float) -> None:
        self._latency_seconds = latency_seconds

    async def transform(self, value: str) -> str:
        # Logged so a demo shows calls happening only on cache misses. The value itself may be
        # user data, so only its length is recorded.
        logger.info("transformer call chars=%d", len(value))
        await asyncio.sleep(self._latency_seconds)
        return value.upper()
