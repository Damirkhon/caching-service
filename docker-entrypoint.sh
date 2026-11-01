#!/bin/sh
# Container start: bring the schema up to date, then serve.
set -eu

# Fine for a single replica; with several, migrations would move to a one-off job.
alembic upgrade head

# exec makes uvicorn PID 1, so it receives SIGTERM directly and shuts down gracefully.
# One worker on purpose: concurrent identical work is coalesced within a process, so scale
# with replicas rather than workers. The port must match the image's HEALTHCHECK.
exec uvicorn --factory caching_service.main:create_app \
    --host 0.0.0.0 --port 8000 --no-server-header "$@"
