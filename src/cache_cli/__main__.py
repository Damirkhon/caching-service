"""Entry point for the ``cache-cli`` console script."""

import json
import os
import statistics
import sys
from collections.abc import Sequence
from typing import Final, TextIO

import httpx
from pydantic import ValidationError

from cache_cli.client import PayloadClient
from cache_cli.errors import EXIT_OK, EXIT_USAGE, CliError, ContractError
from cache_cli.io import open_output, read_request
from cache_cli.models import PayloadRequest
from cache_cli.runner import IterationResult, run_iterations
from cache_cli.settings import PROG, CliSettings, describe_errors, parse_settings

# A cold request may transform up to 2000 strings, a few at a time, before answering.
HTTP_TIMEOUT_SECONDS: Final = 300.0
# Connecting should be quick, so an unreachable host fails fast instead of after five minutes.
CONNECT_TIMEOUT_SECONDS: Final = 5.0
# Shell conventions: 128 plus the signal number, for Ctrl-C (SIGINT) and a closed pipe (SIGPIPE).
EXIT_INTERRUPTED: Final = 130
EXIT_BROKEN_PIPE: Final = 141


def main(argv: Sequence[str] | None = None) -> int:
    """Run the tool; --help and unknown flags exit through argparse's SystemExit instead."""
    arguments = sys.argv[1:] if argv is None else argv
    try:
        settings = parse_settings(arguments)
    except ValidationError as exc:
        for line in describe_errors(exc):
            print(f"{PROG}: {line}", file=sys.stderr)
        return EXIT_USAGE
    timeout = httpx.Timeout(HTTP_TIMEOUT_SECONDS, connect=CONNECT_TIMEOUT_SECONDS)
    with httpx.Client(base_url=str(settings.host), timeout=timeout) as http:
        code = run(settings, http=http, stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr)
    if code == EXIT_BROKEN_PIPE:
        # The reader went away (e.g. `| head`). Python flushes stdout again at exit, which would
        # fail once more with a noisy message; pointing it at /dev/null keeps the exit quiet.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
    return code


def run(
    settings: CliSettings, *, http: httpx.Client, stdin: TextIO, stdout: TextIO, stderr: TextIO
) -> int:
    """The whole tool with its I/O injected, so tests can drive it against an in-process app."""
    try:
        request = read_request(settings, stdin)
        results = _write_results(settings, PayloadClient(http), request, stdout)
    except CliError as exc:
        print(f"{PROG}: {exc}", file=stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        return EXIT_INTERRUPTED
    except BrokenPipeError:
        return EXIT_BROKEN_PIPE
    print(summarize(results, str(settings.host)), file=stderr)
    return EXIT_OK


def _write_results(
    settings: CliSettings, client: PayloadClient, request: PayloadRequest, stdout: TextIO
) -> list[IterationResult]:
    results: list[IterationResult] = []
    with open_output(settings.output_path, stdout) as out:
        for result in run_iterations(client, request, settings.repeat):
            out.write(json.dumps(result.to_record(), ensure_ascii=False) + "\n")
            out.flush()
            results.append(result)
            # The same request must always map to the same payload id.
            if result.id != results[0].id:
                raise ContractError(result.iteration, result.id, results[0].id)
    return results


def summarize(results: Sequence[IterationResult], host: str) -> str:
    count = len(results)
    noun = "iteration" if count == 1 else "iterations"
    header = f"{count} {noun} against {host} — all returned payload {results[0].id}"
    first, rest = results[0], results[1:]
    if not rest:
        timings = f"create: {first.create_ms:.1f} ms · read: {first.read_ms:.1f} ms"
    else:
        later_create = statistics.fmean(r.create_ms for r in rest)
        read = statistics.fmean(r.read_ms for r in results)
        timings = (
            f"create: first {first.create_ms:.1f} ms, then avg {later_create:.1f} ms"
            f" · read: avg {read:.1f} ms"
        )
    return f"{header}\n{timings}"


if __name__ == "__main__":
    raise SystemExit(main())
