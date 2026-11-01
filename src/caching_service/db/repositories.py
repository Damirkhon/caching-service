"""Repositories over a caller-owned session.

They never commit: transaction boundaries belong to the services, which decide how long a
transaction may stay open.
"""

import uuid
from collections.abc import Collection, Mapping

from sqlalchemy import Insert, select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.ext.asyncio import AsyncSession

from caching_service.db.models import Base, Payload, Transformation
from caching_service.domain.payload import fingerprint


class TransformationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_many(self, inputs: Collection[str]) -> dict[str, str]:
        """Return ``{input: output}`` for the inputs that are cached, in a single query."""
        if not inputs:
            return {}
        hashes = {fingerprint(value) for value in inputs}
        result = await self._session.execute(
            select(Transformation.input, Transformation.output).where(
                Transformation.input_hash.in_(hashes)
            )
        )
        return dict(result.all())

    async def add_many(self, results: Mapping[str, str]) -> None:
        """Store results, silently skipping rows another writer stored first.

        A concurrent duplicate is harmless: the transformer is deterministic, so both writers
        hold the same value.
        """
        if not results:
            return
        # Sorted by key so concurrent writers lock overlapping rows in the same order. In
        # request order, two batches sharing keys in opposite order deadlock on PostgreSQL,
        # where a conflicting insert waits for the other transaction to finish.
        rows = sorted(
            (
                {"input_hash": fingerprint(value), "input": value, "output": output}
                for value, output in results.items()
            ),
            key=lambda row: row["input_hash"],
        )
        # Passing rows separately lets SQLAlchemy batch them into fixed-size statements,
        # instead of compiling and preparing a new statement for every batch size.
        await self._session.execute(_insert_ignoring_conflicts(self._session, Transformation), rows)


class PayloadRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, payload_id: uuid.UUID) -> Payload | None:
        return await self._session.get(Payload, payload_id)

    async def get_or_create(self, output: str) -> tuple[uuid.UUID, bool]:
        """Return ``(id, created)`` for the payload with this output.

        Race-safe without application locks: the unique output hash lets the database pick a
        single winner, and every other writer reads the winner's id.
        """
        output_hash = fingerprint(output)
        insert = (
            _insert_ignoring_conflicts(self._session, Payload)
            .values(output_hash=output_hash, output=output)
            .returning(Payload.id)
        )
        created_id = (await self._session.execute(insert)).scalar_one_or_none()
        if created_id is not None:
            return created_id, True

        # Under READ COMMITTED (the default on both databases) this new statement sees the row
        # the winning writer just committed; a stricter isolation level would not.
        existing = select(Payload.id).where(Payload.output_hash == output_hash)
        return (await self._session.execute(existing)).scalar_one(), False


def _insert_ignoring_conflicts(session: AsyncSession, model: type[Base]) -> Insert:
    # The one place that knows ON CONFLICT DO NOTHING is spelled per dialect.
    dialect_name = session.get_bind().dialect.name
    match dialect_name:
        case "postgresql":
            return postgresql.insert(model).on_conflict_do_nothing()
        case "sqlite":
            return sqlite.insert(model).on_conflict_do_nothing()
        case _:
            raise NotImplementedError(dialect_name)
