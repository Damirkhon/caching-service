import io
import json
from pathlib import Path

import pytest

from cache_cli.errors import (
    InvalidRequestError,
    NoInputError,
    UnreadableInputError,
    UnwritableOutputError,
)
from cache_cli.io import open_output, read_request
from cache_cli.models import PayloadRequest
from cache_cli.settings import parse_settings

REQUEST = {"list_1": ["a"], "list_2": ["b"]}


class FakeTerminal(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_read_request_from_json_flag() -> None:
    settings = parse_settings(["-j", json.dumps(REQUEST)])

    assert read_request(settings, io.StringIO()) == PayloadRequest(**REQUEST)


def test_read_request_from_file(tmp_path: Path) -> None:
    path = tmp_path / "request.json"
    path.write_text(json.dumps(REQUEST))

    assert read_request(parse_settings(["-i", str(path)]), io.StringIO()) == PayloadRequest(
        **REQUEST
    )


@pytest.mark.parametrize("argv", [[], ["-i", "-"]], ids=["no-source", "dash"])
def test_read_request_from_stdin(argv: list[str]) -> None:
    assert read_request(parse_settings(argv), io.StringIO(json.dumps(REQUEST))) == PayloadRequest(
        **REQUEST
    )


def test_read_request_tty_without_source_errors() -> None:
    with pytest.raises(NoInputError, match="--input FILE or --json JSON"):
        read_request(parse_settings([]), FakeTerminal())


def test_explicit_dash_reads_even_an_interactive_stdin() -> None:
    terminal = FakeTerminal(json.dumps(REQUEST))

    assert read_request(parse_settings(["-i", "-"]), terminal) == PayloadRequest(**REQUEST)


@pytest.mark.parametrize(
    "content",
    ["{not json", json.dumps({"list_1": ["a"]}), json.dumps({"list_1": ["a"], "list_2": []})],
    ids=["malformed", "missing-list", "unequal-lengths"],
)
def test_invalid_request_from_file_is_a_usage_error(tmp_path: Path, content: str) -> None:
    path = tmp_path / "request.json"
    path.write_text(content)

    with pytest.raises(InvalidRequestError, match="invalid request"):
        read_request(parse_settings(["-i", str(path)]), io.StringIO())


def test_undecodable_input_file_is_a_usage_error(tmp_path: Path) -> None:
    path = tmp_path / "request.json"
    path.write_bytes(b"\xff\xfe\x00")

    with pytest.raises(UnreadableInputError, match="not UTF-8"):
        read_request(parse_settings(["-i", str(path)]), io.StringIO())


def test_input_file_removed_after_parsing_is_a_usage_error(tmp_path: Path) -> None:
    path = tmp_path / "request.json"
    path.write_text(json.dumps(REQUEST))
    settings = parse_settings(["-i", str(path)])
    path.unlink()

    with pytest.raises(UnreadableInputError, match="cannot read"):
        read_request(settings, io.StringIO())


def test_open_output_dash_does_not_close_stdout() -> None:
    stdout = io.StringIO()

    with open_output("-", stdout) as stream:
        stream.write("line\n")

    assert not stdout.closed
    assert stdout.getvalue() == "line\n"


def test_open_output_writes_and_closes_a_file(tmp_path: Path) -> None:
    path = tmp_path / "out.jsonl"

    with open_output(str(path), io.StringIO()) as stream:
        stream.write("ünïcode\n")

    assert stream.closed
    assert path.read_text(encoding="utf-8") == "ünïcode\n"


def test_open_output_reports_an_unwritable_path(tmp_path: Path) -> None:
    # A directory can't be opened for writing.
    with pytest.raises(UnwritableOutputError, match="cannot write"):  # noqa: SIM117
        with open_output(str(tmp_path), io.StringIO()):
            pass
