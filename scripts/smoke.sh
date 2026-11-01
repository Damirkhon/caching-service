#!/usr/bin/env bash
# Smoke test of the Docker deployment: build and start the Compose stack, drive it with the CLI
# inside the container, check the results, and always tear everything down again.
# Needs nothing on the host but Docker.
set -euo pipefail

cd "$(dirname "$0")/.."

# An isolated project (own containers, volumes and image) on any free host port. Set here, never
# taken from the environment, so the teardown below can't touch a developer's own stack.
export COMPOSE_PROJECT_NAME=caching-service-smoke
export API_PORT=0
WAIT_TIMEOUT_SECONDS=180

SAMPLE='{"list_1": ["first string", "second string", "third string"], "list_2": ["other string", "another string", "last string"]}'
EXPECTED='FIRST STRING, OTHER STRING, SECOND STRING, ANOTHER STRING, THIRD STRING, LAST STRING'

cleanup() {
    status=$?
    if [ "$status" -ne 0 ]; then
        echo "smoke: FAILED; service status and recent logs:" >&2
        docker compose ps --all >&2 || true
        docker compose logs --no-color --tail 50 >&2 || true
    fi
    docker compose down --volumes --remove-orphans --rmi local >/dev/null 2>&1 || true
    exit "$status"
}
trap cleanup EXIT

echo "smoke: building and starting the stack"
# --wait returns once every service reports healthy: Postgres first, then the API after its
# migrations ran.
docker compose up --build --detach --wait --wait-timeout "$WAIT_TIMEOUT_SECONDS"

echo "smoke: creating the sample payload twice with cache-cli"
# The CLI itself fails if the two iterations return different payload ids.
results=$(docker compose exec -T api cache-cli -h http://localhost:8000 -r 2 -j "$SAMPLE")

echo "smoke: checking the results"
printf '%s\n' "$results" | docker compose exec -T -e EXPECTED="$EXPECTED" api python -c '
import json, os, sys

records = [json.loads(line) for line in sys.stdin if line.strip()]
assert len(records) == 2, records
assert [r["created"] for r in records] == [True, False], records
assert all(r["output"] == os.environ["EXPECTED"] for r in records), records
'

echo "smoke: passed"
