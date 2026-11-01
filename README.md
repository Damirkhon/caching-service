# Caching Service

A FastAPI microservice that caches the results of a slow external string
transformer in a database and builds interleaved payloads from them, plus
`cache-cli`, a command-line tool that exercises the service over HTTP.

## Quickstart

Requires [uv](https://docs.astral.sh/uv/) and Python 3.13.

```bash
uv sync        # create .venv and install the locked dependencies
make check     # lint, type-check and run the tests with coverage
make help      # list every target
```

## Configuration

Copy `.env.example` to `.env` and adjust as needed. The file is read from the current
working directory, so run commands from the repository root.

## Development

Install the git hooks once with `uv run pre-commit install`. They run ruff and
basic file hygiene checks on every commit; `make check` is the full gate.

## Assumptions

## Shortcuts
