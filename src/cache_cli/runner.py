"""Sends the request repeatedly and times each create and read."""

import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Final

from cache_cli.client import PayloadClient
from cache_cli.models import PayloadRequest

MS_PER_SECOND: Final = 1000


@dataclass(frozen=True, slots=True)
class IterationResult:
    iteration: int
    id: uuid.UUID
    created: bool
    output: str
    create_ms: float
    read_ms: float

    def to_record(self) -> dict[str, object]:
        return {
            "iteration": self.iteration,
            "id": str(self.id),
            "created": self.created,
            "output": self.output,
            "create_ms": round(self.create_ms, 1),
            "read_ms": round(self.read_ms, 1),
        }


def run_iterations(
    client: PayloadClient, request: PayloadRequest, repeat: int
) -> Iterator[IterationResult]:
    """Yields each result as soon as it is known, so output streams while later ones run."""
    for iteration in range(1, repeat + 1):
        started = time.perf_counter()
        payload, created = client.create(request)
        created_at = time.perf_counter()
        output = client.get_output(payload.id)
        read_at = time.perf_counter()
        yield IterationResult(
            iteration=iteration,
            id=payload.id,
            created=created,
            output=output,
            create_ms=(created_at - started) * MS_PER_SECOND,
            read_ms=(read_at - created_at) * MS_PER_SECOND,
        )
