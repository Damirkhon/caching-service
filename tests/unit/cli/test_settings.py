import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from cache_cli.settings import DEFAULT_HOST, MAX_REPEAT, describe_errors, parse_settings

SAMPLE_JSON = json.dumps({"list_1": ["a", "b"], "list_2": ["c", "d"]})


@pytest.fixture
def request_file(tmp_path: Path) -> Path:
    path = tmp_path / "request.json"
    path.write_text(SAMPLE_JSON)
    return path


def test_defaults() -> None:
    settings = parse_settings([])

    assert str(settings.host) == f"{DEFAULT_HOST}/"
    assert settings.repeat == 1
    assert settings.input_path is None
    assert settings.json_input is None
    assert settings.output_path == "-"


@pytest.mark.parametrize(
    ("short", "long", "value", "field"),
    [
        ("-h", "--host", "http://example.test:9000", "host"),
        ("-r", "--repeat", "3", "repeat"),
        ("-i", "--input", "-", "input_path"),
        ("-j", "--json", SAMPLE_JSON, "json_input"),
        ("-o", "--output", "-", "output_path"),
    ],
)
def test_short_and_long_flags_are_equivalent(short: str, long: str, value: str, field: str) -> None:
    assert getattr(parse_settings([short, value]), field) == getattr(
        parse_settings([long, value]), field
    )


def test_help_is_long_only_and_h_means_host(capsys: pytest.CaptureFixture[str]) -> None:
    assert str(parse_settings(["-h", "http://example.test"]).host) == "http://example.test/"

    with pytest.raises(SystemExit) as exc_info:
        parse_settings(["--help"])

    assert exc_info.value.code == 0
    help_text = capsys.readouterr().out
    assert "-h, --host URL" in help_text
    assert "--help" in help_text


def test_input_file_is_accepted(request_file: Path) -> None:
    assert parse_settings(["-i", str(request_file)]).input_path == str(request_file)


@pytest.mark.parametrize(
    "argv",
    [
        ["-i", "FILE", "-j", SAMPLE_JSON],
        ["--input", "FILE", "--json", SAMPLE_JSON],
        # The conflict is reported even when one of the values is itself invalid.
        ["-i", "/no/such/file", "-j", "{not json"],
    ],
    ids=["short", "long", "invalid-values"],
)
def test_input_and_json_are_mutually_exclusive(request_file: Path, argv: list[str]) -> None:
    with pytest.raises(ValidationError) as exc_info:
        parse_settings([str(request_file) if arg == "FILE" else arg for arg in argv])

    assert describe_errors(exc_info.value) == ["--input and --json are mutually exclusive"]


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["-r", "0"], "--repeat: Input should be greater than or equal to 1"),
        (
            ["-r", str(MAX_REPEAT + 1)],
            f"--repeat: Input should be less than or equal to {MAX_REPEAT}",
        ),
        (["-r", "many"], "--repeat: Input should be a valid integer"),
        (["-h", "ftp://example.test"], "--host: URL scheme should be 'http' or 'https'"),
        (["-i", "/no/such/request.json"], "--input: No such file: /no/such/request.json"),
        (["-j", "{not json"], "--json: Invalid JSON"),
        (
            ["-j", '{"list_1": ["a"], "list_2": []}'],
            "--json: list_1 and list_2 must have the same length",
        ),
        (["-j", '{"list_1": ["a"]}'], "--json: list_2: Field required"),
        (["-o", "/no/such/dir/out.jsonl"], "--output: No such directory: /no/such/dir"),
    ],
)
def test_invalid_values_rejected(argv: list[str], message: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        parse_settings(argv)

    (line,) = describe_errors(exc_info.value)
    assert line.startswith(message)


def test_unknown_flags_exit_with_usage_error() -> None:
    with pytest.raises(SystemExit) as exc_info:
        parse_settings(["--nope"])

    assert exc_info.value.code == 2


def test_env_vars_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOST", "http://from-environment.test")
    monkeypatch.setenv("REPEAT", "7")

    settings = parse_settings([])

    assert str(settings.host) == f"{DEFAULT_HOST}/"
    assert settings.repeat == 1
