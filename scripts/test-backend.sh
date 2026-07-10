#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
COMPOSE=(docker compose -f infra/docker-compose.yml -f infra/docker-compose.override.yml)

# Run tests inside the backend container so deps match CI/container, not host Python.
# Stop the worker while tests run so real queues don't race test workers.
"${COMPOSE[@]}" stop worker >/dev/null 2>&1 || true
status=0
"${COMPOSE[@]}" exec -T backend pytest "${@:-}" -q || status=$?
"${COMPOSE[@]}" start worker >/dev/null 2>&1 || true
exit "$status"
