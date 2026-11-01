import asyncio
import gc
import logging

import pytest

from caching_service.services.single_flight import SingleFlight


class Gate:
    """A call that blocks until released and counts how often it actually ran."""

    def __init__(self, result: str = "value") -> None:
        self.result = result
        self.runs = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(self) -> str:
        self.runs += 1
        self.started.set()
        await self.release.wait()
        return self.result


async def joined() -> None:
    # One loop iteration lets a freshly created task run up to its first suspension point,
    # which in SingleFlight.run is the await on the shared call: run() has no earlier await.
    await asyncio.sleep(0)


async def test_concurrent_same_key_runs_once() -> None:
    flights: SingleFlight[str, str] = SingleFlight()
    gate = Gate()

    first = asyncio.create_task(flights.run("k", gate))
    await gate.started.wait()
    second = asyncio.create_task(flights.run("k", gate))
    await joined()
    gate.release.set()

    assert list(await asyncio.gather(first, second)) == ["value", "value"]
    assert gate.runs == 1


async def test_different_keys_run_independently() -> None:
    flights: SingleFlight[str, str] = SingleFlight()
    gate_a, gate_b = Gate("a"), Gate("b")

    tasks = [
        asyncio.create_task(flights.run("a", gate_a)),
        asyncio.create_task(flights.run("b", gate_b)),
    ]
    await gate_a.started.wait()
    await gate_b.started.wait()
    gate_a.release.set()
    gate_b.release.set()

    assert await asyncio.gather(*tasks) == ["a", "b"]
    assert (gate_a.runs, gate_b.runs) == (1, 1)


async def test_exception_propagates_to_all_waiters_and_is_not_cached() -> None:
    flights: SingleFlight[str, str] = SingleFlight()
    release = asyncio.Event()
    runs = 0

    async def failing() -> str:
        nonlocal runs
        runs += 1
        await release.wait()
        raise RuntimeError

    first = asyncio.create_task(flights.run("k", failing))
    await joined()
    second = asyncio.create_task(flights.run("k", failing))
    await joined()
    release.set()

    results = await asyncio.gather(first, second, return_exceptions=True)
    assert [type(result) for result in results] == [RuntimeError, RuntimeError]
    assert runs == 1

    # The failure was not kept: the next caller makes a fresh attempt.
    with pytest.raises(RuntimeError):
        await flights.run("k", failing)
    assert runs == 2


async def test_key_released_after_completion() -> None:
    flights: SingleFlight[str, str] = SingleFlight()
    gate = Gate()
    gate.release.set()

    await flights.run("k", gate)
    await flights.run("k", gate)

    assert gate.runs == 2


async def test_cancelled_waiter_does_not_cancel_shared_task() -> None:
    flights: SingleFlight[str, str] = SingleFlight()
    gate = Gate()

    leader = asyncio.create_task(flights.run("k", gate))
    await gate.started.wait()
    waiter = asyncio.create_task(flights.run("k", gate))
    await joined()

    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    gate.release.set()
    assert await leader == "value"
    assert gate.runs == 1


async def test_failure_after_every_caller_left_is_not_reported_as_unretrieved(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # asyncio logs "exception was never retrieved" when a failed future is garbage-collected
    # unobserved, which is exactly what happens if the only caller was cancelled.
    flights: SingleFlight[str, str] = SingleFlight()
    release = asyncio.Event()
    finished = asyncio.Event()

    async def failing() -> str:
        try:
            await release.wait()
            raise RuntimeError
        finally:
            finished.set()

    caller = asyncio.create_task(flights.run("k", failing))
    await joined()
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller

    with caplog.at_level(logging.ERROR, logger="asyncio"):
        release.set()
        await finished.wait()
        await joined()
        gc.collect()

    assert "never retrieved" not in caplog.text


async def test_cancelled_call_reaches_callers_and_releases_key() -> None:
    # The shared call itself being cancelled (e.g. at shutdown) must not wedge its key.
    flights: SingleFlight[str, str] = SingleFlight()

    async def cancelled() -> str:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await flights.run("k", cancelled)

    gate = Gate()
    gate.release.set()
    assert await flights.run("k", gate) == "value"


async def test_caller_arriving_just_after_a_failure_retries() -> None:
    # The late caller's first step is queued before the failure's release callback, so it
    # runs in the window where the failed flight is still registered under the key.
    flights: SingleFlight[str, str] = SingleFlight()
    retry = Gate("retried")
    retry.release.set()
    late: asyncio.Future[str] | None = None

    async def failing() -> str:
        nonlocal late
        late = asyncio.ensure_future(flights.run("k", retry))
        raise RuntimeError

    with pytest.raises(RuntimeError):
        await flights.run("k", failing)

    assert late is not None
    assert await late == "retried"
    assert retry.runs == 1

    # The retry's own entry was not removed by the failed flight's late release.
    second = Gate("again")
    second.release.set()
    assert await flights.run("k", second) == "again"
