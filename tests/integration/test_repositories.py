import asyncio
import random
import re
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import Connection, event, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from caching_service.db.models import Payload, Transformation
from caching_service.db.repositories import PayloadRepository, TransformationRepository

pytestmark = pytest.mark.integration

# The longest string a request may contain. With 4-byte characters it is ~4 kB of UTF-8, above
# the ~2.7 kB PostgreSQL allows for a B-tree entry, so indexing raw text would fail here.
LONG_STRING_LENGTH = 1000
FOUR_BYTE_CODE_POINTS = range(0x1F300, 0x1FAFF)
SHA256_HEX = re.compile(r"[0-9a-f]{64}")

SessionFactory = async_sessionmaker[AsyncSession]


class StatementRecorder:
    def __init__(self) -> None:
        self.statements: list[str] = []
        self.parameters: list[Any] = []

    # Any: SQLAlchemy's cursor-event signature passes untyped DBAPI objects.
    def __call__(self, *args: Any) -> None:
        self.statements.append(args[2])
        self.parameters.append(args[3])


@pytest.fixture
def recorder(engine: AsyncEngine) -> Iterator[StatementRecorder]:
    recorder = StatementRecorder()
    event.listen(engine.sync_engine, "before_cursor_execute", recorder)
    yield recorder
    event.remove(engine.sync_engine, "before_cursor_execute", recorder)


@pytest.fixture
def statements(recorder: StatementRecorder) -> list[str]:
    return recorder.statements


def flatten(parameters: Any) -> list[Any]:
    # Any: drivers pass a dict, a tuple, or a list of either, depending on dialect and batching.
    if isinstance(parameters, dict):
        return list(parameters.values())
    if isinstance(parameters, list | tuple):
        return [value for item in parameters for value in flatten(item)]
    return [parameters]


async def seed(session_factory: SessionFactory, results: dict[str, str]) -> None:
    async with session_factory() as session, session.begin():
        await TransformationRepository(session).add_many(results)


async def test_get_many_returns_only_cached_entries(
    session_factory: SessionFactory, statements: list[str]
) -> None:
    await seed(session_factory, {"a": "A", "b": "B"})
    statements.clear()

    async with session_factory() as session, session.begin():
        cached = await TransformationRepository(session).get_many(["a", "b", "missing", "a"])

    assert cached == {"a": "A", "b": "B"}
    selects = [s for s in statements if s.lstrip().upper().startswith("SELECT")]
    assert len(selects) == 1


async def test_get_many_with_empty_input_skips_query(
    session_factory: SessionFactory, statements: list[str]
) -> None:
    async with session_factory() as session, session.begin():
        cached = await TransformationRepository(session).get_many([])

    assert cached == {}
    assert statements == []


async def test_add_many_ignores_existing_rows(session_factory: SessionFactory) -> None:
    await seed(session_factory, {"a": "A"})

    # "a" collides with an existing row; it must be skipped without raising or overwriting.
    await seed(session_factory, {"a": "changed", "b": "B"})

    async with session_factory() as session, session.begin():
        cached = await TransformationRepository(session).get_many(["a", "b"])
        rows = await session.scalar(select(func.count()).select_from(Transformation))

    assert cached == {"a": "A", "b": "B"}
    assert rows == 2


async def test_add_many_with_empty_results_skips_query(
    session_factory: SessionFactory, statements: list[str]
) -> None:
    async with session_factory() as session, session.begin():
        await TransformationRepository(session).add_many({})

    assert statements == []


async def test_add_many_inserts_rows_in_key_order(
    session_factory: SessionFactory, recorder: StatementRecorder
) -> None:
    # Concurrent writers must lock shared rows in one global order, or PostgreSQL can deadlock
    # two batches that hold the same keys in opposite order.
    values = [f"value-{i}" for i in range(50)]
    random.Random(0).shuffle(values)

    await seed(session_factory, {value: value.upper() for value in values})

    inserted = [
        value
        for statement, parameters in zip(recorder.statements, recorder.parameters, strict=True)
        if statement.lstrip().upper().startswith("INSERT")
        for value in flatten(parameters)
        if isinstance(value, str) and SHA256_HEX.fullmatch(value)
    ]
    assert len(inserted) == len(values)
    assert inserted == sorted(inserted)


async def test_add_many_handles_long_strings(session_factory: SessionFactory) -> None:
    # Random rather than repeated characters, so PostgreSQL cannot compress the value small.
    rng = random.Random(0)
    long_value = "".join(chr(rng.choice(FOUR_BYTE_CODE_POINTS)) for _ in range(LONG_STRING_LENGTH))
    await seed(session_factory, {long_value: long_value.upper()})

    async with session_factory() as session, session.begin():
        cached = await TransformationRepository(session).get_many([long_value])

    assert cached == {long_value: long_value.upper()}


async def test_add_many_keeps_near_duplicates_apart(session_factory: SessionFactory) -> None:
    await seed(session_factory, {"a": "A", "a ": "A ", "A": "A"})

    async with session_factory() as session, session.begin():
        cached = await TransformationRepository(session).get_many(["a", "a ", "A"])

    assert cached == {"a": "A", "a ": "A ", "A": "A"}


async def test_get_or_create_creates_then_reuses(session_factory: SessionFactory) -> None:
    async with session_factory() as session, session.begin():
        first_id, first_created = await PayloadRepository(session).get_or_create("A, B")
    async with session_factory() as session, session.begin():
        second_id, second_created = await PayloadRepository(session).get_or_create("A, B")
        stored = await PayloadRepository(session).get(first_id)

    assert first_created is True
    assert second_created is False
    assert second_id == first_id
    assert stored is not None
    assert stored.output == "A, B"


async def test_get_or_create_concurrent_same_output_returns_one_id(
    engine: AsyncEngine, session_factory: SessionFactory
) -> None:
    # Forces the contended path: the second writer issues its INSERT while the first writer's
    # transaction is still open, then the first commits. No sleeps involved.
    second_is_inserting = asyncio.Event()

    async def create_in_own_session() -> tuple[uuid.UUID, bool]:
        async with session_factory() as session, session.begin():
            return await PayloadRepository(session).get_or_create("A, B")

    async with session_factory() as first, first.begin():
        first_id, first_created = await PayloadRepository(first).get_or_create("A, B")
        first_connection = (await first.connection()).sync_connection

        # Any: SQLAlchemy's cursor-event signature passes untyped DBAPI objects.
        def on_execute(connection: Connection, *args: Any) -> None:
            if connection is not first_connection and args[1].lstrip().upper().startswith("INSERT"):
                second_is_inserting.set()

        event.listen(engine.sync_engine, "before_cursor_execute", on_execute)
        try:
            second = asyncio.create_task(create_in_own_session())
            await second_is_inserting.wait()
            assert not second.done()
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", on_execute)

    second_id, second_created = await second

    assert (first_created, second_created) == (True, False)
    assert second_id == first_id
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Payload)) == 1


async def test_get_returns_none_for_unknown_id(session_factory: SessionFactory) -> None:
    async with session_factory() as session:
        assert await PayloadRepository(session).get(uuid.uuid4()) is None
