import asyncio
import io
import json
import socket
import threading
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi import FastAPI

from cache_cli.__main__ import run
from cache_cli.errors import EXIT_OK, EXIT_USAGE
from cache_cli.settings import parse_settings
from caching_service.api.schemas import MAX_STRING_LENGTH, SAMPLE_OUTPUT, SAMPLE_REQUEST
from caching_service.db.models import Base
from caching_service.db.session import create_engine
from caching_service.main import create_app
from tests.factories import make_settings
from tests.fakes import CountingTransformer

pytestmark = pytest.mark.e2e

SAMPLE_JSON = json.dumps(SAMPLE_REQUEST)
SERVER_START_TIMEOUT_SECONDS = 10.0
SERVER_STOP_TIMEOUT_SECONDS = 10.0


class _ObservableServer(uvicorn.Server):
    """Signals when it is listening, or has given up, so tests wait on an event, not a poll."""

    def __init__(self, config: uvicorn.Config) -> None:
        super().__init__(config)
        self.ready = threading.Event()

    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        await super().startup(sockets)
        self.ready.set()

    def run_in_thread(self) -> None:
        # uvicorn exits the process when startup fails; in a thread that would surface as a
        # second, confusing error, so the waiting test reports the failure instead.
        try:
            with suppress(SystemExit):
                self.run()
        finally:
            # Also released when startup fails, so the waiting test fails at once.
            self.ready.set()


@contextmanager
def serve(app: FastAPI) -> Iterator[str]:
    """Run the real app under uvicorn on a free local port; yields its base URL."""
    # Port 0 lets the OS pick a free port; log_config=None leaves logging to the test session.
    server = _ObservableServer(uvicorn.Config(app, host="127.0.0.1", port=0, log_config=None))
    thread = threading.Thread(target=server.run_in_thread, daemon=True)
    thread.start()
    try:
        if not server.ready.wait(SERVER_START_TIMEOUT_SECONDS) or not server.started:
            pytest.fail("the service did not start")
        port = server.servers[0].sockets[0].getsockname()[1]
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(SERVER_STOP_TIMEOUT_SECONDS)
        assert not thread.is_alive(), "the service did not stop"


async def _reset_schema(database_url: str, *, create: bool) -> None:
    engine = create_engine(make_settings(database_url=database_url))
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
            if create:
                await connection.run_sync(Base.metadata.create_all)
    finally:
        await engine.dispose()


@pytest.fixture
def schema_url(database_url: str) -> Iterator[str]:
    # Synchronous: the served app runs on its own event loop in a worker thread.
    asyncio.run(_reset_schema(database_url, create=True))
    yield database_url
    asyncio.run(_reset_schema(database_url, create=False))


def run_cli(argv: list[str], base_url: str) -> tuple[int, str, str]:
    """The CLI exactly as main() runs it, against the served app, with captured streams."""
    settings = parse_settings(["-h", base_url, *argv])
    stdout, stderr = io.StringIO(), io.StringIO()
    with httpx.Client(base_url=str(settings.host)) as http:
        code = run(settings, http=http, stdin=io.StringIO(), stdout=stdout, stderr=stderr)
    return code, stdout.getvalue(), stderr.getvalue()


def test_repeat_returns_same_id_and_transforms_each_string_once(schema_url: str) -> None:
    transformer = CountingTransformer()
    app = create_app(make_settings(database_url=schema_url), transformer=transformer)

    with serve(app) as base_url:
        code, out, _ = run_cli(["-r", "3", "-j", SAMPLE_JSON], base_url)

    records = [json.loads(line) for line in out.splitlines()]
    assert code == EXIT_OK
    assert [r["created"] for r in records] == [True, False, False]
    assert len({r["id"] for r in records}) == 1
    assert all(r["output"] == SAMPLE_OUTPUT for r in records)
    assert sorted(transformer.calls) == sorted(
        [*SAMPLE_REQUEST["list_1"], *SAMPLE_REQUEST["list_2"]]  # type: ignore[misc]  # sample is typed as generic JSON
    )


def test_writes_jsonl_to_file_and_summary_to_stderr(schema_url: str, tmp_path: Path) -> None:
    request_file = tmp_path / "request.json"
    request_file.write_text(SAMPLE_JSON)
    output_file = tmp_path / "out.jsonl"
    app = create_app(make_settings(database_url=schema_url), transformer=CountingTransformer())

    with serve(app) as base_url:
        code, out, err = run_cli(
            ["-r", "2", "-i", str(request_file), "-o", str(output_file)], base_url
        )

    lines = output_file.read_text(encoding="utf-8").splitlines()
    assert code == EXIT_OK
    assert out == ""
    assert len(lines) == 2
    assert err.startswith("2 iterations against")
    assert f"all returned payload {json.loads(lines[0])['id']}" in err


def test_server_validation_error_exits_2(schema_url: str) -> None:
    # Well-formed for the CLI, which only checks shape; the service enforces the limits.
    too_long = json.dumps({"list_1": ["x" * (MAX_STRING_LENGTH + 1)], "list_2": ["b"]})
    transformer = CountingTransformer()
    app = create_app(make_settings(database_url=schema_url), transformer=transformer)

    with serve(app) as base_url:
        code, out, err = run_cli(["-j", too_long], base_url)

    assert code == EXIT_USAGE
    assert out == ""
    assert err.startswith("cache-cli: service answered 422: list_1.0:")
    assert transformer.calls == []
