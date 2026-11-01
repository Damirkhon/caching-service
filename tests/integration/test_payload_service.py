import asyncio
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from caching_service.db.models import Payload
from caching_service.domain.transformer import Transformer
from caching_service.errors import PayloadNotFoundError, TransformerError
from caching_service.services.payloads import PayloadService
from caching_service.services.transformation_cache import TransformationCache
from tests.fakes import CountingTransformer, FailingTransformer

pytestmark = pytest.mark.integration

SessionFactory = async_sessionmaker[AsyncSession]

TIMEOUT_SECONDS = 5.0
MAX_CONCURRENCY = 10

SAMPLE_LIST_1 = ["first string", "second string", "third string"]
SAMPLE_LIST_2 = ["other string", "another string", "last string"]
SAMPLE_OUTPUT = (
    "FIRST STRING, OTHER STRING, SECOND STRING, ANOTHER STRING, THIRD STRING, LAST STRING"
)


def make_service(session_factory: SessionFactory, transformer: Transformer) -> PayloadService:
    cache = TransformationCache(
        session_factory,
        transformer,
        timeout_seconds=TIMEOUT_SECONDS,
        max_concurrency=MAX_CONCURRENCY,
    )
    return PayloadService(cache, session_factory)


async def payload_count(session_factory: SessionFactory) -> int:
    async with session_factory() as session:
        return await session.scalar(select(func.count()).select_from(Payload)) or 0


async def test_create_then_read_returns_sample_output(session_factory: SessionFactory) -> None:
    transformer = CountingTransformer()
    service = make_service(session_factory, transformer)

    created = await service.create(SAMPLE_LIST_1, SAMPLE_LIST_2)

    assert created.created is True
    assert await service.get_output(created.id) == SAMPLE_OUTPUT
    assert sorted(transformer.calls) == sorted(SAMPLE_LIST_1 + SAMPLE_LIST_2)


async def test_create_same_input_reuses_id_without_transformer_calls(
    session_factory: SessionFactory,
) -> None:
    first = await make_service(session_factory, CountingTransformer()).create(
        SAMPLE_LIST_1, SAMPLE_LIST_2
    )
    transformer = CountingTransformer()

    second = await make_service(session_factory, transformer).create(SAMPLE_LIST_1, SAMPLE_LIST_2)

    assert second.id == first.id
    assert second.created is False
    assert transformer.calls == []


async def test_create_different_input_same_output_reuses_id(
    session_factory: SessionFactory,
) -> None:
    service = make_service(session_factory, CountingTransformer())
    first = await service.create(["a"], ["b"])

    second = await service.create(["A"], ["b"])

    assert second.id == first.id
    assert second.created is False


async def test_create_swapped_lists_yields_new_id_from_cache(
    session_factory: SessionFactory,
) -> None:
    first = await make_service(session_factory, CountingTransformer()).create(
        ["a", "b"], ["c", "d"]
    )
    transformer = CountingTransformer()
    service = make_service(session_factory, transformer)

    swapped = await service.create(["c", "d"], ["a", "b"])

    assert swapped.id != first.id
    assert swapped.created is True
    assert await service.get_output(swapped.id) == "C, A, D, B"
    assert transformer.calls == []


async def test_get_output_unknown_id_raises(session_factory: SessionFactory) -> None:
    service = make_service(session_factory, CountingTransformer())

    with pytest.raises(PayloadNotFoundError):
        await service.get_output(uuid.uuid4())


async def test_create_transformer_failure_writes_no_payload(
    session_factory: SessionFactory,
) -> None:
    service = make_service(session_factory, FailingTransformer(fail_on={"bad"}))

    with pytest.raises(TransformerError):
        await service.create(["ok"], ["bad"])

    assert await payload_count(session_factory) == 0
    # The string that did transform is cached, so a retry only pays for the failed one.
    retry = CountingTransformer()
    await make_service(session_factory, retry).create(["ok"], ["bad"])
    assert retry.calls == ["bad"]


async def test_concurrent_creates_of_the_same_payload_share_one_id(
    session_factory: SessionFactory,
) -> None:
    service = make_service(session_factory, CountingTransformer())

    results = await asyncio.gather(
        *(service.create(SAMPLE_LIST_1, SAMPLE_LIST_2) for _ in range(5))
    )

    assert len({result.id for result in results}) == 1
    assert sum(result.created for result in results) == 1
    assert await payload_count(session_factory) == 1
