# Caching Service

[![CI](https://github.com/Damirkhon/caching-service/actions/workflows/ci.yml/badge.svg)](https://github.com/Damirkhon/caching-service/actions/workflows/ci.yml)

A FastAPI microservice that takes two equal-length lists of strings, transforms every string with
a slow external transformer (simulated here), interleaves the results, and stores the payload
under an id the client can read it back by. It is built to call the transformer as rarely as
possible: **once a string's result is stored, it is never sent to the transformer again, and a
payload generated before keeps its original id**. `cache-cli` is a companion command-line tool
that exercises the service over HTTP.

How that guarantee is achieved, the assumptions and decisions behind it, and the shortcuts and
known limitations (including the narrow cases where a string can be transformed twice) are in
[DESIGN.md](DESIGN.md#shortcuts-and-future-work).

## Quick start (Docker)

Only Docker (with Compose v2.17 or later) is needed on the host.

```bash
make up        # build and start the API on PostgreSQL; returns once both are healthy
```

```bash
curl -i -X POST http://localhost:8000/payload \
  -H 'content-type: application/json' \
  -d '{"list_1": ["first string", "second string", "third string"],
       "list_2": ["other string", "another string", "last string"]}'
# 201 Created, Location: /payload/<id>, {"id": "<id>", "message": "Payload created."}

curl http://localhost:8000/payload/<id>
# {"output": "FIRST STRING, OTHER STRING, SECOND STRING, ANOTHER STRING, THIRD STRING, LAST STRING"}
```

Repeat the POST and it answers `200` with the same id, without calling the transformer.
`docker compose logs api` shows each request's cache hits and misses.

```bash
make smoke     # build, start, exercise the stack with cache-cli, verify, tear down
make down      # stop the stack (the database volume is kept)
```

Interactive API docs are at <http://localhost:8000/docs>. Set `API_PORT` to publish the API on
another host port.

## API

| Method and path | Purpose | Responses |
|---|---|---|
| `POST /payload` | Create a payload from two equal-length lists of strings | `201` new payload, `200` an identical payload already existed and its id is reused; both return `{"id", "message"}` and a `Location` header |
| `GET /payload/{id}` | Read a payload's output | `200 {"output"}`, `404` unknown id |
| `GET /health` | Readiness, including a database check | `200 {"status": "ok"}`, `503 {"status": "unavailable"}` |

Each list holds 1–1000 strings of 1–1000 characters. Other error responses:

| Status | When |
|---|---|
| `400` | The body nests deeper than the JSON parser can handle |
| `413` | The body is larger than any valid request could be (about 25 MB) |
| `422` | The body is malformed JSON or fails validation, or the id is not a UUID; the response lists each problem without echoing the input |
| `502` | The transformer failed or timed out; results that succeeded are cached, so a retry is cheaper |
| `500` | An unexpected error; details are logged, never returned |

## The `cache-cli` tool

`cache-cli` sends a payload request to the service N times, reads each result back, and writes
one JSON line per iteration with timings. The first iteration pays for the transformer calls;
later ones are served from the cache and return the same id.

```text
cache-cli [-h|--host URL] [-r|--repeat N] [-i|--input FILE|-] [-j|--json JSON] [-o|--output FILE|-] [--help]
```

```bash
echo '{"list_1": ["a", "b"], "list_2": ["c", "d"]}' > request.json

uv run cache-cli -h http://localhost:8000 -j '{"list_1": ["a", "b"], "list_2": ["c", "d"]}'
uv run cache-cli -r 3 -i request.json -o results.jsonl
cat request.json | uv run cache-cli
```

It also ships in the Docker image: `docker compose exec -T api cache-cli -r 3 -i - < request.json`.

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
  different ids for the same request; `2` invalid arguments, input or output path, including the service
  rejecting the request with `422`; `130` interrupted with Ctrl-C; `141` the output pipe was
  closed early (for example `cache-cli -r 100 | head -1`).

The CLI is a separate package that talks to the service over HTTP only and never imports the
server code, so it can be installed and used on its own. Possible extensions: a `--concurrency K`
flag to fire iterations in parallel, and an input file holding many requests.

## Local development

Requires [uv](https://docs.astral.sh/uv/); it installs Python 3.13 if needed.

```bash
uv sync        # create .venv and install the locked dependencies
make migrate   # create or upgrade the schema (SQLite in ./data by default)
make run       # serve on http://127.0.0.1:8000 with auto-reload
make check     # lint, format check, mypy --strict, tests with coverage (the gate for every commit)
make help      # list every target
```

Install the git hooks once with `uv run pre-commit install`; they run ruff and basic file
hygiene checks on every commit.

Tests live in `tests/unit`, `tests/integration` (marked `integration`, real database) and
`tests/e2e` (marked `e2e`, the CLI against a served app). By default they use a temporary SQLite
file per test. To run the same suite on PostgreSQL, point `TEST_DATABASE_URL` at a database whose
role may create databases (the migration tests create a throwaway one):

```bash
docker run -d --name test-pg -p 5432:5432 -e POSTGRES_PASSWORD=test postgres:17.11-alpine
until docker exec test-pg pg_isready -h 127.0.0.1 -U postgres; do sleep 1; done
TEST_DATABASE_URL=postgresql+asyncpg://postgres:test@localhost:5432/postgres make test
docker rm -f test-pg
```

To run a subset without the coverage gate, call pytest directly, for example only the unit
tests: `uv run pytest -m 'not integration and not e2e'`. `make test` and `make check` always run
the whole suite with the 90% coverage gate.

CI runs lint and type checks, the test suite on SQLite and on PostgreSQL, and the Docker smoke
test on every push.

## Configuration

The service reads environment variables prefixed with `CACHE_SERVICE_`. A `.env` file is read
from the current working directory too; copy `.env.example` to start one.

| Variable | Default | Purpose |
|---|---|---|
| `CACHE_SERVICE_DATABASE_URL` | `sqlite+aiosqlite:///./data/cache.db` | SQLAlchemy async URL; Compose sets a `postgresql+asyncpg://` URL |
| `CACHE_SERVICE_TRANSFORMER_LATENCY_SECONDS` | `0.5` | Simulated transformer latency, which makes cache hits visible |
| `CACHE_SERVICE_TRANSFORMER_TIMEOUT_SECONDS` | `5.0` | Per-call timeout; exceeding it counts as a transformer failure |
| `CACHE_SERVICE_TRANSFORMER_MAX_CONCURRENCY` | `10` | Process-wide cap on parallel transformer calls (1–1000) |
| `CACHE_SERVICE_LOG_LEVEL` | `INFO` | Level for the service's own loggers: `DEBUG`, `INFO`, `WARNING` or `ERROR` |

Invalid values stop the service at startup with a clear error.

## Docker details

- The image also runs on its own, serving SQLite from a volume:
  `docker build -t caching-service . && docker run -p 8000:8000 -v caching-data:/app/data caching-service`.
- The container applies migrations on start, then `exec`s a single uvicorn worker, so it
  receives the stop signal directly and `docker stop` shuts it down gracefully. One worker on
  purpose: concurrent identical work is coalesced within a process, so scale with replicas, not
  workers.
- It runs as an unprivileged user that can write only to `/app/data`, and reports its health
  through `GET /health`.
- The Compose credentials are throwaway local values (see [DESIGN.md](DESIGN.md#shortcuts-and-future-work)).

## Project layout

```text
src/caching_service/
├── main.py           # create_app() factory and lifespan wiring
├── config.py         # validated settings (CACHE_SERVICE_* variables)
├── errors.py         # domain errors
├── domain/           # pure logic: interleaving, fingerprints, the Transformer protocol
├── services/         # use cases: transformation cache, single-flight, payloads, health
├── db/               # ORM models, engine and sessions, repositories
└── api/              # routes, schemas, error mapping, body size limit
src/cache_cli/        # the CLI: settings, I/O, HTTP client, runner (talks HTTP only)
migrations/           # Alembic environment and versions
tests/                # unit, integration and e2e suites, plus transformer fakes
scripts/smoke.sh      # Docker smoke test
Dockerfile, docker-compose.yml, docker-entrypoint.sh
```

Dependencies point inward: `api` → `services` → `domain` and `db`. The domain layer imports no
framework code, which a test enforces.
