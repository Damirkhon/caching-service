"""Transformer fakes that make call counts, failures and timing observable in tests."""

import asyncio
from collections.abc import Awaitable, Callable, Iterable

from caching_service.errors import TransformerError
from caching_service.services.single_flight import SingleFlight


class CountingTransformer:
    """Uppercases instantly and records every call, so tests can assert exact call counts."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def transform(self, value: str) -> str:
        self.calls.append(value)
        return value.upper()


class FailingTransformer:
    """Raises for the chosen inputs and uppercases the rest.

    ``error`` defaults to ``TransformerError``; pass another exception type to simulate a bug in
    the transformer, which callers must let propagate rather than wrap. The error carries no
    message, so the input never leaks into logs through it.
    """

    def __init__(self, fail_on: Iterable[str], error: type[Exception] = TransformerError) -> None:
        self.fail_on = frozenset(fail_on)
        self.error = error
        self.calls: list[str] = []

    async def transform(self, value: str) -> str:
        self.calls.append(value)
        if value in self.fail_on:
            raise self.error
        return value.upper()


class BlockingTransformer:
    """Holds every call until ``release`` is set, so overlapping requests are deterministic.

    ``started`` is set once the first call is in flight, and ``wait_for_calls(n)`` waits until
    ``n`` calls are, so a test can act at exactly the right moment without sleeping.
    ``in_flight`` and ``peak`` expose how many calls overlap. ``release`` is one-shot: once set,
    every later call passes straight through.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.in_flight = 0
        self.peak = 0
        self._entered = asyncio.Condition()

    async def transform(self, value: str) -> str:
        self.calls.append(value)
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        self.started.set()
        async with self._entered:
            self._entered.notify_all()
        try:
            await self.release.wait()
        finally:
            self.in_flight -= 1
        return value.upper()

    async def wait_for_calls(self, count: int) -> None:
        async with self._entered:
            await self._entered.wait_for(lambda: len(self.calls) >= count)


class GatedTransformer:
    """Answers at once, except for the ``gated`` inputs, which wait until ``release`` is set.

    ``answered`` records each input as its result is returned, so a test can act after one
    string of a request has finished while another is still held.
    """

    def __init__(self, gated: Iterable[str]) -> None:
        self.gated = frozenset(gated)
        self.calls: list[str] = []
        self.answered: list[str] = []
        self.release = asyncio.Event()
        self._answer = asyncio.Condition()

    async def transform(self, value: str) -> str:
        self.calls.append(value)
        if value in self.gated:
            await self.release.wait()
        async with self._answer:
            self.answered.append(value)
            self._answer.notify_all()
        return value.upper()

    async def wait_until_answered(self, value: str) -> None:
        async with self._answer:
            await self._answer.wait_for(lambda: value in self.answered)


class SlowTransformer:
    """Sleeps for ``delay`` seconds; configure it above the caller's timeout to force one."""

    def __init__(self, delay: float) -> None:
        self.delay = delay
        self.calls: list[str] = []

    async def transform(self, value: str) -> str:
        self.calls.append(value)
        await asyncio.sleep(self.delay)
        return value.upper()


class ObservedSingleFlight(SingleFlight[str, str]):
    """Signals once a given number of callers have joined, i.e. are awaiting a shared call.

    SingleFlight.run has no await before it starts waiting on the shared call, so when the
    counter is bumped the caller is, for scheduling purposes, already waiting.
    """

    def __init__(self) -> None:
        super().__init__()
        self.callers = 0
        self._target = 0
        self._reached = asyncio.Event()

    async def run(self, key: str, fn: Callable[[], Awaitable[str]]) -> str:
        self.callers += 1
        if self.callers >= self._target:
            self._reached.set()
        return await super().run(key, fn)

    async def wait_for_callers(self, count: int) -> None:
        self._target = count
        if self.callers >= count:
            return
        self._reached.clear()
        await self._reached.wait()
