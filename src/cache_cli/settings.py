"""Command-line arguments, parsed and sanitized by pydantic-settings.

The task gives ``-h`` to both ``--host`` and ``--help``. ``-h`` means ``--host`` here, because
it is the functional flag; help is available as ``--help`` only.
"""

import argparse
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from pydantic import AliasChoices, Field, HttpUrl, ValidationError, field_validator, model_validator
from pydantic_core import PydanticCustomError
from pydantic_settings import (
    BaseSettings,
    CliApp,
    CliSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from cache_cli.models import PayloadRequest

PROG: Final = "cache-cli"
STDIO: Final = "-"
MAX_REPEAT: Final = 10_000
DEFAULT_HOST: Final = "http://localhost:8000"

# The raw keys an input source can arrive under, before validation maps them to fields.
INPUT_ALIASES: Final = frozenset({"i", "input"})
JSON_ALIASES: Final = frozenset({"j", "json"})

# Named aliases, because pydantic-settings shows the annotation's name as the placeholder in
# --help: "-h URL" reads better than "-h HttpUrl".
type URL = HttpUrl
type N = int
type FILE = str
type JSON = str


class CliSettings(BaseSettings):
    """Exercise the caching service: create a payload N times, read it back, report timings.

    -h is --host; use --help for this message.
    """

    model_config = SettingsConfigDict(cli_prog_name=PROG, cli_hide_none_type=True, frozen=True)

    host: URL = Field(
        default=HttpUrl(DEFAULT_HOST),
        validation_alias=AliasChoices("h", "host"),
        description="Base URL of the caching service.",
    )
    repeat: N = Field(
        default=1,
        ge=1,
        le=MAX_REPEAT,
        validation_alias=AliasChoices("r", "repeat"),
        description="How many times to send the request.",
    )
    # Field names avoid the builtins `input` and `json`; the aliases keep the public flag names.
    input_path: FILE | None = Field(
        default=None,
        validation_alias=AliasChoices("i", "input"),
        description="Read the request from FILE, or from stdin with '-'.",
    )
    json_input: JSON | None = Field(
        default=None,
        validation_alias=AliasChoices("j", "json"),
        description='The request as JSON: {"list_1": [...], "list_2": [...]}.',
    )
    output_path: FILE = Field(
        default=STDIO,
        validation_alias=AliasChoices("o", "output"),
        description="Write JSON Lines results to FILE, or to stdout with '-'.",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Command-line arguments only. Short aliases bypass any env prefix, so with environment
        # sources enabled an unrelated HOST variable in the user's shell would become --host.
        return (init_settings,)

    @field_validator("input_path")
    @classmethod
    def _input_is_readable(cls, value: str | None) -> str | None:
        if value is not None and value != STDIO and not Path(value).is_file():
            raise PydanticCustomError("input_not_found", "No such file: {path}", {"path": value})
        return value

    @field_validator("json_input")
    @classmethod
    def _json_is_a_request(cls, value: str | None) -> str | None:
        # Checked while parsing arguments, so a bad request fails before any network call.
        if value is not None:
            try:
                PayloadRequest.model_validate_json(value)
            except ValidationError as exc:
                raise PydanticCustomError(
                    "invalid_request", "{reason}", {"reason": describe_first_error(exc)}
                ) from None
        return value

    @field_validator("output_path")
    @classmethod
    def _output_directory_exists(cls, value: str) -> str:
        if value != STDIO and not Path(value).parent.is_dir():
            raise PydanticCustomError(
                "output_dir_not_found",
                "No such directory: {path}",
                {"path": str(Path(value).parent)},
            )
        return value

    @model_validator(mode="before")
    @classmethod
    def _one_input_source(cls, data: dict[str, object]) -> dict[str, object]:
        # Checked before the fields, so passing both flags is reported as the conflict it is,
        # not as whatever is wrong with one of their values. A validator rather than an argparse
        # group: pydantic-settings' group helper would rename the flags (e.g. --source.input).
        # Settings sources always arrive merged into a dict.
        given = {key for key, value in data.items() if value is not None}
        if given & INPUT_ALIASES and given & JSON_ALIASES:
            raise PydanticCustomError(
                "conflicting_inputs", "--input and --json are mutually exclusive"
            )
        return data


def _long_flags() -> dict[str, str]:
    flags: dict[str, str] = {}
    for field in CliSettings.model_fields.values():
        alias = field.validation_alias
        choices = alias.choices if isinstance(alias, AliasChoices) else []
        names = [name for name in choices if isinstance(name, str)]
        flags.update(dict.fromkeys(names, f"--{max(names, key=len, default='')}"))
    return flags


LONG_FLAGS: Final = _long_flags()


def parse_settings(argv: Sequence[str]) -> CliSettings:
    """Parse arguments; raises ValidationError, or SystemExit for --help and unknown flags."""
    # No automatic -h/--help: it would clash with -h/--host at startup.
    parser = argparse.ArgumentParser(prog=PROG, description=CliSettings.__doc__, add_help=False)
    parser.add_argument("--help", action="help", help="Show this help message and exit.")
    source: CliSettingsSource[argparse.ArgumentParser] = CliSettingsSource(
        CliSettings, root_parser=parser
    )
    return CliApp.run(CliSettings, cli_args=list(argv), cli_settings_source=source)


def describe_errors(exc: ValidationError) -> list[str]:
    """One line per error, naming the long flag: ``--repeat: Input should be ...``."""
    lines = []
    for error in exc.errors():
        location = error["loc"]
        flag = LONG_FLAGS.get(str(location[0])) if location else None
        lines.append(f"{flag}: {error['msg']}" if flag else error["msg"])
    return lines


def describe_first_error(exc: ValidationError) -> str:
    error = exc.errors()[0]
    where = ".".join(str(part) for part in error["loc"])
    return f"{where}: {error['msg']}" if where else error["msg"]
