import asyncio

import pytest

from caching_service.domain.transformer import Transformer
from caching_service.errors import TransformerError
from tests.fakes import (
    BlockingTransformer,
    CountingTransformer,
    FailingTransformer,
    SlowTransformer,
)

# Far above the timeout used against it, so the timeout always fires first.
SLOW_DELAY_SECONDS = 60.0
SHORT_TIMEOUT_SECONDS = 0.01


async def test_fakes_satisfy_protocol() -> None:
    blocking = BlockingTransformer()
    blocking.release.set()
    # The annotation is the static half: mypy --strict rejects it if any fake drifts.
    transformers: list[Transformer] = [
        CountingTransformer(),
        FailingTransformer(fail_on=set()),
        blocking,
        SlowTransformer(delay=0),
    ]

    assert [await t.transform("a") for t in transformers] == ["A"] * len(transformers)


async def test_counting_transformer_records_every_call() -> None:
    fake = CountingTransformer()

    results = [await fake.transform(v) for v in ("a", "b", "a")]

    assert results == ["A", "B", "A"]
    assert fake.calls == ["a", "b", "a"]


async def test_failing_transformer_fails_only_chosen_inputs() -> None:
    fake = FailingTransformer(fail_on={"bad"})

    assert await fake.transform("good") == "GOOD"
    with pytest.raises(TransformerError):
        await fake.transform("bad")
    assert fake.calls == ["good", "bad"]


async def test_failing_transformer_can_raise_a_non_domain_error() -> None:
    fake = FailingTransformer(fail_on={"bug"}, error=RuntimeError)

    with pytest.raises(RuntimeError):
        await fake.transform("bug")
    assert fake.calls == ["bug"]


async def test_blocking_transformer_holds_calls_until_released() -> None:
    fake = BlockingTransformer()

    task = asyncio.create_task(fake.transform("a"))
    await fake.started.wait()

    assert not task.done()
    assert fake.calls == ["a"]

    fake.release.set()
    assert await task == "A"


async def test_blocking_transformer_tracks_overlapping_calls() -> None:
    fake = BlockingTransformer()

    tasks = [asyncio.create_task(fake.transform(v)) for v in ("a", "b", "c")]
    await fake.wait_for_calls(3)

    assert fake.in_flight == 3

    fake.release.set()
    assert await asyncio.gather(*tasks) == ["A", "B", "C"]
    assert fake.in_flight == 0
    assert fake.peak == 3


async def test_slow_transformer_outlasts_a_short_timeout() -> None:
    fake = SlowTransformer(delay=SLOW_DELAY_SECONDS)

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(fake.transform("a"), timeout=SHORT_TIMEOUT_SECONDS)
    assert fake.calls == ["a"]
