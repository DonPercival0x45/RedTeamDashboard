#!/usr/bin/env bash
# Compose stack smoke test — the integration layer of the test platform.
#
# Brings the local stack up (or assumes it's up), then asserts the
# end-to-end startup contract: migrations reach head, the backend is
# healthy, the worker booted, the frontend serves, and the MCP/playbook
# URL the worker uses resolves inside the compose network.
#
# This is NOT a full functional suite — it catches "the container boots
# and the wiring is alive" regressions that unit/component tests cannot.
# Pair with `make test` (backend pytest) for behavioral coverage.
#
# Usage:
#   ./scripts/smoke-compose.sh            # bring stack up + smoke
#   ./scripts/smoke-compose.sh --no-up    # smoke an already-running stack
#
# Exits non-zero on any failed assertion. Safe to re-run.
set -euo pipefail

COMPOSE=(docker compose -f infra/docker-compose.yml -f infra/docker-compose.override.yml)
NO_UP=0
[[ "${1:-}" == "--no-up" ]] && NO_UP=1

c_ok()   { printf "  \033[32m✓\033[0m %s\n" "$*"; }
c_fail() { printf "  \033[31m✗\033[0m %s\n" "$*"; }
c_info() { printf "  \033[36m•\033[0m %s\n" "$*"; }

fail() { c_fail "$*"; exit 1; }

if [[ "$NO_UP" -eq 0 ]]; then
  c_info "bringing stack up (this also applies migrations on backend boot)…"
  "${COMPOSE[@]}" up -d --quiet-pull >/dev/null
fi

# Wait for backend health (it runs `alembic upgrade head` before uvicorn).
c_info "waiting for backend /health…"
for _ in $(seq 1 60); do
  if curl -sf http://localhost:8001/health >/dev/null 2>&1; then break; fi
  sleep 1
done

# 1. Backend health endpoint.
if curl -sf http://localhost:8001/health >/dev/null 2>&1; then
  c_ok "backend /health responds"
else
  fail "backend /health did not respond (check: ${COMPOSE[*]} logs backend)"
fi

# 2. Migration head matches the codebase chain.
EXPECTED_HEAD="0072"
ACTUAL_HEAD="$("${COMPOSE[@]}" exec -T postgres psql -U rtd -d rtd -tAc 'select version_num from alembic_version;' 2>/dev/null | tr -d '[:space:]')"
if [[ "$ACTUAL_HEAD" == "$EXPECTED_HEAD" ]]; then
  c_ok "db at migration head $ACTUAL_HEAD"
else
  fail "db migration head mismatch: expected $EXPECTED_HEAD, got '$ACTUAL_HEAD'"
fi

# 3. Core and dedicated playbook workers are running and healthy.
for service in worker playbook-worker; do
  WORKER_STATE="$("${COMPOSE[@]}" ps "$service" --format '{{.State}}' 2>/dev/null | tr -d '[:space:]')"
  WORKER_HEALTH="$("${COMPOSE[@]}" ps "$service" --format '{{.Health}}' 2>/dev/null | tr -d '[:space:]')"
  if [[ "$WORKER_STATE" == "running" && "$WORKER_HEALTH" == "healthy" ]]; then
    c_ok "$service is running and healthy"
  else
    fail "$service unhealthy (state='$WORKER_STATE', health='$WORKER_HEALTH'; check logs)"
  fi
done

# 4. Frontend serves (the SPA shell, before auth redirect).
FE_STATUS="$(curl -s -o /dev/null -w '%{http_code}' http://localhost:3001/ 2>/dev/null || true)"
if [[ "$FE_STATUS" -lt 500 ]]; then
  c_ok "frontend responds (HTTP $FE_STATUS)"
else
  fail "frontend returned HTTP $FE_STATUS (check: $COMPOSE logs frontend)"
fi

# 5. The MCP URL the playbook worker resolves to is reachable inside the net.
#    Catches the dead-port regression (backend:8001 vs container 8000).
MCP_PROBE="$("${COMPOSE[@]}" exec -T playbook-worker python -c '
from app.core.config import settings
import urllib.request, sys
url = settings.playbook_mcp_url.rstrip("/")
request = urllib.request.Request(
    url,
    headers={"X-API-Key": settings.worker_mcp_api_key or ""},
)
try:
    urllib.request.urlopen(request, timeout=3)
    print("ok")
except Exception as e:
    print("fail:" + str(e)[:120])
' 2>/dev/null | tr -d '[:space:]')"
if [[ "$MCP_PROBE" == "ok" ]] || [[ "$MCP_PROBE" == fail*405* ]] || [[ "$MCP_PROBE" == fail*404* ]]; then
  c_ok "playbook MCP URL reachable and worker key accepted"
else
  fail "playbook MCP URL unreachable: $MCP_PROBE"
fi

echo
c_info "smoke OK — stack is alive and wired correctly."
c_info "behavioral coverage: make test   (backend pytest)   |   cd frontend && npm run test:run   |   npx playwright test"
