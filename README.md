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

## The `cache-cli` tool

`cache-cli` sends a payload request to the service N times, reads each result back, and writes
one JSON line per iteration with timings. The first iteration pays for the transformer calls;
later ones are served from the cache and return the same id.

```bash
uv run cache-cli -r 3 -i request.json
uv run cache-cli -h http://localhost:8000 -j '{"list_1": ["a", "b"], "list_2": ["c", "d"]}'
cat request.json | uv run cache-cli -o results.jsonl
```

```text
cache-cli [-h|--host URL] [-r|--repeat N] [-i|--input FILE|-] [-j|--json JSON] [-o|--output FILE|-] [--help]
```

| Flag | Default | Meaning |
|---|---|---|
| `-h`, `--host` | `http://localhost:8000` | Base URL of the service (http or https) |
| `-r`, `--repeat` | `1` | Iterations, 1–10000 |
| `-i`, `--input` | | Read the request from a file, or from stdin with `-` |
| `-j`, `--json` | | The request inline as JSON |
| `-o`, `--output` | `-` (stdout) | Where to write the JSON Lines results |

- **`-h` means `--host`.** The task assigns `-h` to both `--host` and `--help`; `--host` wins as
  the functional flag, and help is available as `--help` only.
- Pass at most one of `--input` and `--json`. With neither, the request is read from stdin.
- Results go to stdout (or `--output`); a human summary goes to stderr, so stdout can be piped
  into tools like `jq`.
- Settings come from command-line arguments only, so a stray `HOST` variable in your shell can't
  change the target. The HTTP client still honours the standard proxy and TLS variables
  (`HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY`, `SSL_CERT_FILE`).
- Exit codes: `0` success; `1` the service is unreachable, timed out, failed, or returned
  different ids for the same request; `2` invalid arguments or input, including the service
  rejecting the request with `422`; `130` interrupted with Ctrl-C; `141` the output pipe was
  closed early (for example `cache-cli -r 100 | head -1`).

The CLI is a separate package that talks to the service over HTTP only and never imports the
server code, so it can be installed and used on its own.

Possible extensions: a `--concurrency K` flag to fire iterations in parallel (exercising the
service's coalescing of concurrent identical work), and an input file holding many requests.

## Configuration

Copy `.env.example` to `.env` and adjust as needed. The file is read from the current
working directory, so run commands from the repository root.

## Development

Install the git hooks once with `uv run pre-commit install`. They run ruff and
basic file hygiene checks on every commit; `make check` is the full gate.

## Assumptions

## Shortcuts
