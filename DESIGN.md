# Design

Why the service has the shape it has: how it avoids transformer calls, what it assumes, the
decisions behind it, and the shortcuts it takes knowingly. For running and using it, see the
[README](README.md).

## How caching works

The service is judged on calling the slow transformer as rarely as possible. Each rule below
removes one specific way a redundant call could happen.

- **Persistent cache, one lookup per request.** Every transformed string is stored, keyed by a
  SHA-256 of the exact input. A request looks up all of its strings in a single batched query,
  and only misses go to the transformer. A string transformed once is never sent again.
- **Duplicates within a request cost one call.** Inputs are deduplicated (keeping first-seen
  order) before the lookup, so `["a", "a"]` + `["a", "b"]` makes at most two calls.
- **Concurrent requests share calls (single-flight).** If two requests miss the same string at
  the same moment, one transformer call runs and both await it. The call runs as its own
  shielded task, so a client disconnecting never cancels work another request is waiting on.
- **No database transaction spans a transformer call.** The cache is read in one short session
  and written in another; the slow calls happen in between, holding no connection. On SQLite
  that also keeps the write lock free; on PostgreSQL it keeps the pool free.
- **Failures still make progress.** Misses are transformed concurrently, at most
  `CACHE_SERVICE_TRANSFORMER_MAX_CONCURRENCY` at a time process-wide, each bounded by a timeout.
  If some fail, the successes are stored before the request fails with `502`, so a retry only
  pays for the strings that failed.

Payload ids are reused the same way: payloads are unique by a hash of their output, and creation
is an `INSERT ... ON CONFLICT DO NOTHING` followed by a lookup, so the database picks a single
winner even when identical requests race. Repeating an input costs no transformer calls, because
every string is already cached.

Every request logs one line with counts only (`requested`, `unique`, `hits`, `misses`,
`failed`), never the strings themselves, so the container logs show the cache at work.

## Data model

| Table | Key | Columns | Why |
|---|---|---|---|
| `transformations` | `input_hash` (SHA-256 hex) | `input`, `output`, `created_at` | A fixed-size key indexes the same on SQLite and PostgreSQL, whose B-tree entries on raw text are capped at about 2.7 kB. The raw input is kept so results can be mapped back and the key recomputed. |
| `payloads` | `id` (random UUID) | `output_hash` (unique), `output`, `created_at` | Unique by output, so the database enforces id reuse. The public id is random rather than derived from the hash, so it stays opaque and independent of the hashing scheme. |

There is no foreign key between the tables: a payload stores its rendered output, because
payloads never change and rebuilding them from cache rows on every read would only add cost.
Rows sharing keys are inserted in key order, so concurrent writers lock them in the same order
and can't deadlock on PostgreSQL.

## Assumptions

| Assumption | Consequence |
|---|---|
| The transformer is deterministic and context-free: the same string always gives the same result. | Caching per string is valid, and entries never expire. |
| The sample output implies the transformer uppercases; the real one is an external service. | `SimulatedTransformer` uppercases after a configurable delay, behind a `Transformer` protocol that a real HTTP client could implement. |
| Cache keys are the exact strings: no trimming, case folding or Unicode normalization. | We can't know what the external service treats as equal, so `"a"`, `"a "` and `"A"` are separate entries. |
| The output joins the interleaved transformed strings with `", "`. | One separator constant in the domain module. |
| "Payload files" means stored payloads, not files on disk. | Payloads live in the database next to the cache: one storage system, atomic writes. |
| Inputs must be bounded. | Each list has 1–1000 strings of 1–1000 characters; bodies larger than any valid request are refused with `413` before being read. |
| One service process is the deployment target. | In-process single-flight is enough; see the shortcuts below for several replicas. |

Questions the task leaves open, and the defaults chosen:

- **`-h` is given to both `--host` and `--help` in the CLI synopsis.** `-h` means `--host`, the
  functional flag; help is `--help` only. (Taken literally, the argument parser fails at startup.)
- **Does "the same payload" mean the same input or the same output?** The same output, which
  covers both readings, since identical inputs always produce identical outputs.
- **What should the CLI print?** JSON Lines, one record per iteration with timings, and a short
  human summary on stderr.
- **Should cached results ever expire?** No, given a deterministic transformer.

## Key decisions

| Decision | Instead of | Why |
|---|---|---|
| Async throughout (FastAPI, SQLAlchemy asyncio, aiosqlite/asyncpg) | Sync routes in a thread pool | The workload waits on a slow external service; asyncio makes concurrent misses and call coalescing simple. |
| SQLAlchemy 2 typed ORM | SQLModel | Dialect-specific `ON CONFLICT` is first-class, and keeping API schemas separate from tables stops storage details leaking into the HTTP contract. |
| PostgreSQL in Compose, SQLite for local development and the default test run, the same suite on both in CI | One database | Postgres shows a production-like deployment; SQLite keeps tests fast with nothing to install; running both catches dialect drift, which is isolated in one helper. |
| Deduplicate payloads by output hash | By input fingerprint | Covers both readings of "same payload" with one mechanism, at no extra transformer cost. |
| In-process single-flight | A distributed lock | Matches the single-process target at almost no cost. |
| Alembic migrations, run by the container entrypoint | `create_all()` at startup | Versioned, reviewable schema changes. Tests use `create_all` for speed, and a test checks that the migrations match the models. |
| Services own short sessions through an injected session factory | One session per request | A per-request session would hold a transaction open across slow transformer calls. |
| An app factory, `create_app(settings, transformer=...)` | A module-level app | Configuration is read when the app is built, not at import, and every test gets an isolated app with a fake transformer, no patched globals. |
| The CLI is a separate package that talks HTTP only | Importing the server's models | It installs and runs on its own; a test enforces that it never imports the server. |

## Error model

| Situation | Status |
|---|---|
| Invalid body or id | `422`, in FastAPI's format, without echoing the rejected input |
| Body not parseable as JSON (for example, nested too deeply) | `400` |
| Body larger than any valid request | `413` |
| Unknown payload id | `404` |
| Transformer failure or timeout, after storing the successes | `502` |
| Database unreachable (health check) | `503` |
| Anything unexpected | `500`; the traceback is logged, never returned |

Services raise domain errors, and only the API's exception handlers map them to HTTP.

## Shortcuts and future work

These are deliberate, and each has a known next step.

- **Several replicas.** Single-flight is per process. With several instances, concurrent misses
  on different instances can each call the transformer once. Results stay correct, because
  writes are idempotent. The fix is a PostgreSQL advisory lock or a Redis lock keyed by the input
  hash. For the same reason the container runs one uvicorn worker; scale with replicas.
- **Two narrow windows for a duplicate call.** A single-flight entry is released when the
  transformer returns, slightly before its result is committed, so a request reading the cache
  in that moment calls again. And if every request waiting on a call is cancelled, the call
  still finishes but nobody stores the result. Both cost one extra call, never a wrong result;
  closing them would need per-key write transactions or a cross-request result map.
- **No invalidation or versioning.** If the transformer's behaviour could change, a
  `transformer_version` column would join the cache key.
- **No authentication, rate limiting or proxy-level request limits.** Assumed to live at an API
  gateway in front of the service.
- **Logs, not metrics.** Each request logs its hits, misses and failures; Prometheus counters
  would be the next step.
- **No HTTP caching of `GET /payload/{id}`.** Payloads are immutable, so `Cache-Control:
  immutable` and an `ETag` would let proxies serve them.
- **Payloads are kept forever.** Retention isn't specified.
- **Migrations run on container start.** Right for one replica; with several, they would move to
  a one-off job.
- **Image size.** About 240 MB uncompressed (66 MB compressed), mostly real runtime
  dependencies: SQLAlchemy, asyncpg and uvloop. Dropping uvicorn's `[standard]` extras would save
  about 20 MB at some cost in server performance.
- **The Compose credentials are throwaway local values.** A real deployment would read them from
  a secret store.
