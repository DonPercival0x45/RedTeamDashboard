#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
COMPOSE=(docker compose -f infra/docker-compose.yml -f infra/docker-compose.override.yml)

echo "branch: $(git branch --show-current)"
echo
"${COMPOSE[@]}" ps --format 'table {{.Service}}\t{{.State}}\t{{.Health}}'
echo
REV=$("${COMPOSE[@]}" exec -T postgres psql -U rtd -d rtd -tAc 'select version_num from alembic_version;' 2>/dev/null | tr -d '[:space:]' || true)
echo "db revision: ${REV:-unknown}"
