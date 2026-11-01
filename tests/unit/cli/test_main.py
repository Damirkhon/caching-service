import io
import json
import uuid
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from cache_cli import __main__ as cli_main
from cache_cli.__main__ import EXIT_BROKEN_PIPE, EXIT_INTERRUPTED, main, run
from cache_cli.errors import EXIT_FAILURE, EXIT_OK, EXIT_USAGE
from cache_cli.settings import parse_settings

REQUEST = json.dumps({"list_1": ["a"], "list_2": ["b"]})
# Nothing listens on port 1, so connecting fails at once.
UNREACHABLE_HOST = "http://127.0.0.1:1"

Handler = Callable[[httpx.Request], httpx.Response]


def fake_service(*, ids: list[uuid.UUID] | None = None) -> Handler:
    """201 on the first create, 200 afterwards; ``ids`` overrides the id returned per create."""
    first_id = uuid.uuid4()
    creates = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal creates
        if request.method == "POST":
            payload_id = ids[creates] if ids else first_id
            creates += 1
            return httpx.Response(
                201 if creates == 1 else 200, json={"id": str(payload_id), "message": ""}
            )
        return httpx.Response(200, json={"output": "A, B"})

    return handler


class ClosedPipe(io.StringIO):
    """A stdout whose reader has gone away, as with `cache-cli ... | head -1`."""

    def write(self, text: str) -> int:
        raise BrokenPipeError


def invoke(
    argv: list[str], handler: Handler, stdin: str = "", stdout: io.StringIO | None = None
) -> tuple[int, str, str]:
    stdout = io.StringIO() if stdout is None else stdout
    stderr = io.StringIO()
    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://service.test")
    code = run(
        parse_settings(argv), http=http, stdin=io.StringIO(stdin), stdout=stdout, stderr=stderr
    )
    return code, stdout.getvalue(), stderr.getvalue()


def test_writes_one_json_line_per_iteration_and_a_summary() -> None:
    code, out, err = invoke(["-r", "3", "-j", REQUEST], fake_service())

    records = [json.loads(line) for line in out.splitlines()]
    assert code == EXIT_OK
    assert [r["iteration"] for r in records] == [1, 2, 3]
    assert [r["created"] for r in records] == [True, False, False]
    assert len({r["id"] for r in records}) == 1
    assert all(r["output"] == "A, B" for r in records)
    assert err.startswith(
        f"3 iterations against http://localhost:8000/ — all returned payload {records[0]['id']}"
    )
    assert "create: first" in err


def test_single_iteration_summary() -> None:
    code, _, err = invoke(["-j", REQUEST], fake_service())

    assert code == EXIT_OK
    assert err.startswith("1 iteration against")
    assert "create: first" not in err


def test_reads_the_request_from_stdin() -> None:
    code, out, _ = invoke([], fake_service(), stdin=REQUEST)

    assert code == EXIT_OK
    assert len(out.splitlines()) == 1


def test_writes_results_to_a_file(tmp_path: Path) -> None:
    path = tmp_path / "out.jsonl"

    code, out, _ = invoke(["-r", "2", "-j", REQUEST, "-o", str(path)], fake_service())

    assert code == EXIT_OK
    assert out == ""
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2


def test_different_ids_for_the_same_request_break_the_contract() -> None:
    first, second = uuid.uuid4(), uuid.uuid4()

    code, out, err = invoke(["-r", "2", "-j", REQUEST], fake_service(ids=[first, second]))

    assert code == EXIT_FAILURE
    assert len(out.splitlines()) == 2
    assert f"iteration 2 returned payload {second}" in err


def test_server_validation_error_exits_2() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            422, json={"detail": [{"loc": ["body", "list_1"], "msg": "too long"}]}
        )

    code, _, err = invoke(["-j", REQUEST], handler)

    assert code == EXIT_USAGE
    assert err == "cache-cli: service answered 422: list_1: too long\n"


def test_interrupt_exits_quietly() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise KeyboardInterrupt

    code, _, err = invoke(["-j", REQUEST], handler)

    assert (code, err) == (EXIT_INTERRUPTED, "")


def test_main_reports_invalid_arguments_without_a_traceback(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["-r", "0", "-j", REQUEST]) == EXIT_USAGE

    err = capsys.readouterr().err
    assert err == "cache-cli: --repeat: Input should be greater than or equal to 1\n"


def test_main_reports_an_unreachable_service(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["-h", UNREACHABLE_HOST, "-j", REQUEST]) == EXIT_FAILURE

    assert capsys.readouterr().err == f"cache-cli: cannot reach service at {UNREACHABLE_HOST}/\n"


def test_main_without_a_source_on_a_terminal_hints_at_the_flags(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class Terminal(io.StringIO):
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr("sys.stdin", Terminal())

    assert main([]) == EXIT_USAGE
    assert "--input FILE or --json JSON" in capsys.readouterr().err


def test_main_reads_sys_argv_by_default(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.argv", ["cache-cli", "-r", "0"])

    assert main() == EXIT_USAGE
    assert "--repeat" in capsys.readouterr().err


def test_closed_output_pipe_exits_quietly() -> None:
    code, _, err = invoke(["-j", REQUEST], fake_service(), stdout=ClosedPipe())

    assert (code, err) == (EXIT_BROKEN_PIPE, "")


def test_main_silences_stdout_after_a_broken_pipe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Python flushes stdout again at exit; main redirects the descriptor so that flush is quiet.
    def broken_pipe_run(*_: object, **__: object) -> int:
        return EXIT_BROKEN_PIPE

    monkeypatch.setattr(cli_main, "run", broken_pipe_run)
    with (tmp_path / "stdout").open("w") as stdout:
        monkeypatch.setattr("sys.stdout", stdout)

        assert main(["-j", REQUEST]) == EXIT_BROKEN_PIPE
        stdout.write("discarded\n")

    assert (tmp_path / "stdout").read_text() == ""
