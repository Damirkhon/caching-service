import asyncio
import logging

import pytest

from caching_service.domain.transformer import SimulatedTransformer, Transformer


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("abc", "ABC"),
        ("Mixed Case 123", "MIXED CASE 123"),
        ("straße", "STRASSE"),
        ("", ""),
    ],
)
async def test_simulated_transformer_uppercases(value: str, expected: str) -> None:
    assert await SimulatedTransformer(latency_seconds=0).transform(value) == expected


async def test_simulated_transformer_applies_latency(monkeypatch: pytest.MonkeyPatch) -> None:
    delays: list[float] = []

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(asyncio, "sleep", record_sleep)

    await SimulatedTransformer(latency_seconds=0.75).transform("abc")

    assert delays == [0.75]


async def test_simulated_transformer_logs_length_not_value(
    caplog: pytest.LogCaptureFixture,
) -> None:
    user_value = "user data that must not be logged"

    with caplog.at_level(logging.INFO, logger="caching_service.domain.transformer"):
        await SimulatedTransformer(latency_seconds=0).transform(user_value)

    (record,) = caplog.records
    assert record.getMessage() == f"transformer call chars={len(user_value)}"
    assert user_value not in caplog.text
    assert user_value not in str(record.__dict__)


async def test_simulated_transformer_satisfies_protocol() -> None:
    # The annotation is the static half: mypy --strict rejects it if the protocol isn't met.
    transformer: Transformer = SimulatedTransformer(latency_seconds=0)

    assert await transformer.transform("a") == "A"
