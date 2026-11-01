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

## Running the service

```bash
make migrate   # create or upgrade the database schema (SQLite in ./data by default)
make run       # serve on http://127.0.0.1:8000 with auto-reload
```

Interactive API docs are at <http://127.0.0.1:8000/docs>.

### API

| Method and path | Purpose | Responses |
|---|---|---|
| `POST /payload` | Create a payload from two equal-length lists of strings | `201` new payload, `200` an identical payload already existed and its id is reused; both return `{"id", "message"}` and a `Location` header |
| `GET /payload/{id}` | Read a payload's output | `200 {"output"}`, `404` unknown id |
| `GET /health` | Readiness, including a database check | `200 {"status": "ok"}`, `503 {"status": "unavailable"}` |

Example:

```bash
curl -i -X POST http://127.0.0.1:8000/payload \
  -H 'content-type: application/json' \
  -d '{"list_1": ["first string", "second string", "third string"],
       "list_2": ["other string", "another string", "last string"]}'
```

Each list holds 1–1000 strings of 1–1000 characters. Other error responses:

| Status | When |
|---|---|
| `400` | The body is not parseable JSON (for example, nested too deeply) |
| `413` | The body is larger than any valid request could be |
| `422` | The body or id is invalid; the response lists each problem without echoing the input |
| `502` | The transformer failed or timed out; results that succeeded are cached, so a retry is cheaper |
| `500` | An unexpected error; details are logged, never returned |

## Configuration

Copy `.env.example` to `.env` and adjust as needed. The file is read from the current
working directory, so run commands from the repository root.

## Development

Install the git hooks once with `uv run pre-commit install`. They run ruff and
basic file hygiene checks on every commit; `make check` is the full gate.

## Assumptions

## Shortcuts
