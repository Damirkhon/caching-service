import asyncio
import logging
from typing import Any, NoReturn

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from caching_service.db.models import Transformation
from caching_service.domain.transformer import Transformer
from caching_service.errors import TransformerError
from caching_service.services.single_flight import SingleFlight
from caching_service.services.transformation_cache import TransformationCache
from tests.fakes import (
    BlockingTransformer,
    CountingTransformer,
    FailingTransformer,
    ObservedSingleFlight,
    SlowTransformer,
)

pytestmark = pytest.mark.integration

SessionFactory = async_sessionmaker[AsyncSession]

DEFAULT_TIMEOUT_SECONDS = 5.0
DEFAULT_MAX_CONCURRENCY = 10
# The timeout fires long before the slow fake would answer, so the test never waits for it.
SHORT_TIMEOUT_SECONDS = 0.05
SLOW_DELAY_SECONDS = 60.0


def make_cache(
    session_factory: SessionFactory,
    transformer: Transformer,
    single_flight: SingleFlight[str, str] | None = None,
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
) -> TransformationCache:
    return TransformationCache(
        session_factory,
        transformer,
        timeout_seconds=timeout_seconds,
        max_concurrency=max_concurrency,
        single_flight=single_flight,
    )


async def stored_count(session_factory: SessionFactory) -> int:
    async with session_factory() as session:
        return await session.scalar(select(func.count()).select_from(Transformation)) or 0


async def test_misses_are_transformed_and_stored(session_factory: SessionFactory) -> None:
    transformer = CountingTransformer()

    result = await make_cache(session_factory, transformer).transform_many(["a", "b", "c"])

    assert result == {"a": "A", "b": "B", "c": "C"}
    assert sorted(transformer.calls) == ["a", "b", "c"]
    assert await stored_count(session_factory) == 3


async def test_cached_values_are_looked_up_in_one_query(
    engine: AsyncEngine, session_factory: SessionFactory
) -> None:
    await make_cache(session_factory, CountingTransformer()).transform_many(["a", "b", "c"])
    selects: list[str] = []

    # Any: SQLAlchemy's cursor-event signature passes untyped DBAPI objects.
    def record(*args: Any) -> None:
        if args[2].lstrip().upper().startswith("SELECT"):
            selects.append(args[2])

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        await make_cache(session_factory, CountingTransformer()).transform_many(["a", "b", "c"])
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record)

    assert len(selects) == 1


async def test_repeated_values_are_served_from_cache_without_calls(
    session_factory: SessionFactory,
) -> None:
    await make_cache(session_factory, CountingTransformer()).transform_many(["a", "b", "c"])
    # A new instance, so the hits can only come from the database, not from memory.
    transformer = CountingTransformer()

    result = await make_cache(session_factory, transformer).transform_many(["a", "b", "c"])

    assert result == {"a": "A", "b": "B", "c": "C"}
    assert transformer.calls == []


async def test_partial_overlap_calls_only_for_new_values(session_factory: SessionFactory) -> None:
    await make_cache(session_factory, CountingTransformer()).transform_many(["a", "b"])
    transformer = CountingTransformer()

    result = await make_cache(session_factory, transformer).transform_many(["b", "c", "d"])

    assert result == {"b": "B", "c": "C", "d": "D"}
    assert sorted(transformer.calls) == ["c", "d"]


async def test_duplicates_within_a_request_cost_one_call_each(
    session_factory: SessionFactory,
) -> None:
    transformer = CountingTransformer()
    flights = ObservedSingleFlight()

    result = await make_cache(session_factory, transformer, flights).transform_many(
        ["x", "x", "y", "x"]
    )

    assert result == {"x": "X", "y": "Y"}
    assert sorted(transformer.calls) == ["x", "y"]
    # Deduplicated before reaching single-flight, which would otherwise mask the repeats.
    assert flights.callers == 2


async def test_concurrent_requests_for_the_same_miss_share_one_call(
    session_factory: SessionFactory,
) -> None:
    transformer = BlockingTransformer()
    flights = ObservedSingleFlight()
    cache = make_cache(session_factory, transformer, flights)

    requests = [asyncio.create_task(cache.transform_many(["k"])) for _ in range(2)]
    await flights.wait_for_callers(2)
    transformer.release.set()

    assert list(await asyncio.gather(*requests)) == [{"k": "K"}, {"k": "K"}]
    assert transformer.calls == ["k"]


async def test_near_duplicate_strings_are_separate_cache_entries(
    session_factory: SessionFactory,
) -> None:
    transformer = CountingTransformer()

    result = await make_cache(session_factory, transformer).transform_many(["a", "a "])

    assert result == {"a": "A", "a ": "A "}
    assert sorted(transformer.calls) == ["a", "a "]


async def test_no_connection_is_held_while_the_transformer_runs(
    engine: AsyncEngine, session_factory: SessionFactory
) -> None:
    transformer = BlockingTransformer()
    request = asyncio.create_task(make_cache(session_factory, transformer).transform_many(["a"]))

    await transformer.started.wait()
    checked_out = engine.sync_engine.pool.checkedout()  # type: ignore[attr-defined]  # QueuePool API, not on the base Pool type
    transformer.release.set()
    await request

    assert checked_out == 0


async def test_logs_counts_but_never_values(
    session_factory: SessionFactory, caplog: pytest.LogCaptureFixture
) -> None:
    await make_cache(session_factory, CountingTransformer()).transform_many(["seen"])

    with caplog.at_level(logging.INFO, logger="caching_service.services.transformation_cache"):
        await make_cache(session_factory, CountingTransformer()).transform_many(
            ["seen", "new", "new"]
        )

    (record,) = caplog.records
    # The exact message: counts only, so no request value can appear in it.
    assert record.getMessage() == "transform_many requested=3 unique=2 hits=1 misses=1 failed=0"


async def test_timeout_raises_transformer_error_and_caches_nothing(
    session_factory: SessionFactory,
) -> None:
    transformer = SlowTransformer(delay=SLOW_DELAY_SECONDS)
    cache = make_cache(session_factory, transformer, timeout_seconds=SHORT_TIMEOUT_SECONDS)

    with pytest.raises(TransformerError) as exc_info:
        await cache.transform_many(["s"])

    assert isinstance(exc_info.value.__cause__, TimeoutError)
    assert await stored_count(session_factory) == 0


async def test_concurrent_calls_never_exceed_the_limit(session_factory: SessionFactory) -> None:
    max_concurrency = 2
    transformer = BlockingTransformer()
    cache = make_cache(session_factory, transformer, max_concurrency=max_concurrency)

    request = asyncio.create_task(cache.transform_many(["a", "b", "c", "d", "e"]))
    await transformer.wait_for_calls(max_concurrency)

    # The other three are queued on the limit, so no amount of scheduling lets them start.
    assert transformer.in_flight == max_concurrency

    transformer.release.set()
    assert await request == {v: v.upper() for v in "abcde"}
    assert len(transformer.calls) == 5
    assert transformer.peak == max_concurrency


async def test_successes_are_persisted_when_some_values_fail(
    session_factory: SessionFactory,
) -> None:
    failing = FailingTransformer(fail_on={"bad"})

    with pytest.raises(TransformerError):
        await make_cache(session_factory, failing).transform_many(["ok", "bad"])

    retry = CountingTransformer()
    result = await make_cache(session_factory, retry).transform_many(["ok", "bad"])

    assert result == {"ok": "OK", "bad": "BAD"}
    assert retry.calls == ["bad"]


async def test_unexpected_errors_propagate_unwrapped_after_persisting_successes(
    session_factory: SessionFactory,
) -> None:
    buggy = FailingTransformer(fail_on={"bug"}, error=RuntimeError)

    with pytest.raises(RuntimeError):
        await make_cache(session_factory, buggy).transform_many(["ok", "bug"])

    assert await stored_count(session_factory) == 1


async def test_failures_are_counted_in_the_log(
    session_factory: SessionFactory, caplog: pytest.LogCaptureFixture
) -> None:
    failing = FailingTransformer(fail_on={"bad"})

    with (
        caplog.at_level(logging.INFO, logger="caching_service.services.transformation_cache"),
        pytest.raises(TransformerError),
    ):
        await make_cache(session_factory, failing).transform_many(["ok", "bad"])

    (record,) = caplog.records
    assert record.getMessage() == "transform_many requested=2 unique=2 hits=0 misses=2 failed=1"


async def test_cancelled_request_does_not_cancel_a_shared_call(
    session_factory: SessionFactory,
) -> None:
    transformer = BlockingTransformer()
    flights = ObservedSingleFlight()
    cache = make_cache(session_factory, transformer, flights)

    # The leaver starts the shared call, the harder case: its cancellation must not take the
    # call down with it.
    leaver = asyncio.create_task(cache.transform_many(["k"]))
    await flights.wait_for_callers(1)
    survivor = asyncio.create_task(cache.transform_many(["k"]))
    await flights.wait_for_callers(2)

    leaver.cancel()
    with pytest.raises(asyncio.CancelledError):
        await leaver
    transformer.release.set()

    assert await survivor == {"k": "K"}
    assert transformer.calls == ["k"]
    assert await stored_count(session_factory) == 1


class MixedFailureTransformer:
    """Fails one value as the transformer would and another as a bug would."""

    async def transform(self, value: str) -> str:
        if value == "bad":
            raise TransformerError
        if value == "bug":
            raise RuntimeError
        return value.upper()


async def test_unexpected_error_wins_over_transformer_errors(
    session_factory: SessionFactory,
) -> None:
    cache = make_cache(session_factory, MixedFailureTransformer())

    with pytest.raises(RuntimeError):
        await cache.transform_many(["bad", "bug", "ok"])

    assert await stored_count(session_factory) == 1


class CancellingTransformer:
    """A call that ends cancelled, as an in-flight call does when the process shuts down."""

    async def transform(self, value: str) -> str:
        raise asyncio.CancelledError


async def test_shared_call_cancelled_underneath_a_request_is_a_transformer_error(
    session_factory: SessionFactory,
) -> None:
    with pytest.raises(TransformerError) as exc_info:
        await make_cache(session_factory, CancellingTransformer()).transform_many(["k"])

    assert isinstance(exc_info.value.__cause__, asyncio.CancelledError)


class UnwritableSessionFactory(async_sessionmaker[AsyncSession]):
    """Reads work; opening a write transaction fails, as it would with the database down."""

    def begin(self) -> NoReturn:
        raise ConnectionError


async def test_failed_write_still_logs_and_propagates(
    engine: AsyncEngine, caplog: pytest.LogCaptureFixture
) -> None:
    cache = make_cache(UnwritableSessionFactory(engine), CountingTransformer())

    with (
        caplog.at_level(logging.INFO, logger="caching_service.services.transformation_cache"),
        pytest.raises(ConnectionError),
    ):
        await cache.transform_many(["a"])

    (record,) = caplog.records
    assert record.getMessage() == "transform_many requested=1 unique=1 hits=0 misses=1 failed=0"
