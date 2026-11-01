"""The payload use cases: create a payload from two lists, and read one back."""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from caching_service.db.repositories import PayloadRepository
from caching_service.domain.payload import build_payload_output
from caching_service.errors import PayloadNotFoundError
from caching_service.services.transformation_cache import TransformationCache


@dataclass(frozen=True, slots=True)
class CreatedPayload:
    id: uuid.UUID
    # False when a payload with the same output already existed and its id was reused.
    created: bool


class PayloadService:
    """Holds no per-request state, so one instance can serve every request."""

    def __init__(
        self,
        cache: TransformationCache,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._cache = cache
        self._session_factory = session_factory

    async def create(self, list_1: Sequence[str], list_2: Sequence[str]) -> CreatedPayload:
        """Build the payload and store it, reusing the id of an identical earlier payload.

        Payloads are matched by output, not input: identical inputs always give identical
        outputs, and two inputs that render the same payload are indistinguishable anyway.
        Matching a repeated input costs no transformer calls, since every string is cached.
        """
        transformed = await self._cache.transform_many([*list_1, *list_2])
        output = build_payload_output(list_1, list_2, transformed)
        # Its own short transaction: if this write fails, the transformations cached above
        # are kept, so a retry costs no transformer calls.
        async with self._session_factory.begin() as session:
            payload_id, created = await PayloadRepository(session).get_or_create(output)
        return CreatedPayload(id=payload_id, created=created)

    async def get_output(self, payload_id: uuid.UUID) -> str:
        """Return the stored output; raises ``PayloadNotFoundError`` for an unknown id."""
        async with self._session_factory() as session:
            payload = await PayloadRepository(session).get(payload_id)
        if payload is None:
            raise PayloadNotFoundError(payload_id)
        return payload.output
