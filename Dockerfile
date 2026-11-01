# syntax=docker/dockerfile:1

# --- build stage: install the locked dependencies and the project into a venv ----------------
# Same Python minor version and distro as the runtime stage: the venv's interpreter symlinks
# point at /usr/local/bin/python3.13.
FROM ghcr.io/astral-sh/uv:0.12.21-python3.13-trixie-slim AS builder
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app

# Dependencies first, so editing the source doesn't invalidate this cached layer.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-dev --no-install-project

COPY pyproject.toml uv.lock README.md alembic.ini ./
COPY --chmod=0755 docker-entrypoint.sh ./
COPY migrations ./migrations
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

# --- runtime stage: the venv and the files needed at runtime, nothing else ------------------
FROM python:3.13.18-slim-trixie

RUN groupadd --system app \
    && useradd --system --gid app --uid 10001 --no-create-home app \
    && mkdir -p /app/data \
    && chown app:app /app/data

WORKDIR /app
# Only what runs: the venv (the project is installed into it, not run from source), the
# migrations and the entrypoint. Owned by root, so the service can't rewrite its own code; the
# data directory is the one place it can write.
COPY --from=builder /app/.venv ./.venv
COPY --from=builder /app/migrations ./migrations
COPY --from=builder /app/alembic.ini /app/docker-entrypoint.sh ./

# The default is SQLite on a volume, so the image runs on its own; Compose points it at Postgres.
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CACHE_SERVICE_DATABASE_URL="sqlite+aiosqlite:////app/data/cache.db"

USER app
EXPOSE 8000
VOLUME ["/app/data"]

# Python's urllib, because the slim image has no curl and it isn't worth adding for this.
# Port 8000 must match the entrypoint's --port.
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"]

ENTRYPOINT ["/app/docker-entrypoint.sh"]
