"""Where the request comes from and where results go."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TextIO

from pydantic import ValidationError

from cache_cli.errors import (
    InvalidRequestError,
    NoInputError,
    UnreadableInputError,
    UnwritableOutputError,
)
from cache_cli.models import PayloadRequest
from cache_cli.settings import STDIO, CliSettings, describe_first_error


def read_request(settings: CliSettings, stdin: TextIO) -> PayloadRequest:
    """The request from --json, --input FILE, or stdin (with --input - or no source at all)."""
    if settings.json_input is not None:
        raw = settings.json_input
    elif settings.input_path is not None and settings.input_path != STDIO:
        raw = _read_file(settings.input_path)
    else:
        # With no source given, stdin is read as a Unix filter would; but on an interactive
        # terminal that would just hang waiting, so explain what to pass instead.
        if settings.input_path is None and stdin.isatty():
            raise NoInputError
        raw = stdin.read()
    try:
        return PayloadRequest.model_validate_json(raw)
    except ValidationError as exc:
        raise InvalidRequestError(describe_first_error(exc)) from None


@contextmanager
def open_output(path: str, stdout: TextIO) -> Iterator[TextIO]:
    """The output stream; stdout for '-', which is left open for the caller."""
    if path == STDIO:
        yield stdout
        return
    # Covers failing to open the file and failing to write to it later (e.g. a full disk).
    try:
        with Path(path).open("w", encoding="utf-8") as stream:
            yield stream
    except OSError as exc:
        raise UnwritableOutputError(path, exc.strerror or str(exc)) from None


def _read_file(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise UnreadableInputError(path, exc.strerror or str(exc)) from None
    except UnicodeDecodeError:
        raise UnreadableInputError(path, "not UTF-8 text") from None
