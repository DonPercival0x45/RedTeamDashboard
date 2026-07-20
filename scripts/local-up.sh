#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

ENV_FILE="infra/.env"
ENV_EXAMPLE="infra/.env.example"
COMPOSE=(
  docker compose
  --env-file "$ENV_FILE"
  -f infra/docker-compose.yml
  -f infra/docker-compose.override.yml
)

if [[ ! -f "$ENV_FILE" ]]; then
  cp "$ENV_EXAMPLE" "$ENV_FILE"
  echo "Created $ENV_FILE from $ENV_EXAMPLE."
fi
chmod 600 "$ENV_FILE" 2>/dev/null || true

worker_key=""
while IFS= read -r line || [[ -n "$line" ]]; do
  case "$line" in
    WORKER_MCP_API_KEY=*) worker_key=${line#WORKER_MCP_API_KEY=} ;;
  esac
done < "$ENV_FILE"
worker_key=${worker_key%$'\r'}

# The backend applies migrations on startup. The worker deliberately stays down
# until a cli-scoped MCP key exists; production retains the same fail-fast key
# requirement.
echo "Starting postgres, redis, and backend..."
"${COMPOSE[@]}" up -d --build postgres redis backend

echo "Waiting for backend health..."
backend_ready=false
for _ in {1..60}; do
  if "${COMPOSE[@]}" exec -T backend python -c \
    "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=2)" \
    >/dev/null 2>&1; then
    backend_ready=true
    break
  fi
  sleep 2
done
if [[ "$backend_ready" != true ]]; then
  echo "Backend did not become healthy; inspect logs with: make logs-backend" >&2
  exit 1
fi

if [[ -z "$worker_key" ]]; then
  key_name="local-worker-$(date -u +%Y%m%d%H%M%S)"
  worker_key=$("${COMPOSE[@]}" exec -T backend \
    python -m app.scripts.mint_api_key --name "$key_name" --scope cli)
  worker_key=${worker_key%$'\r'}

  if [[ ! "$worker_key" =~ ^rtd_[A-Za-z0-9_-]+$ ]]; then
    echo "The backend did not return a valid worker API key; leaving $ENV_FILE unchanged." >&2
    exit 1
  fi

  temp_env=$(mktemp "${ENV_FILE}.tmp.XXXXXX")
  cleanup() {
    rm -f "$temp_env"
  }
  trap cleanup EXIT
  chmod 600 "$temp_env" 2>/dev/null || true

  key_written=false
  while IFS= read -r line || [[ -n "$line" ]]; do
    case "$line" in
      WORKER_MCP_API_KEY=*)
        if [[ "$key_written" == false ]]; then
          printf 'WORKER_MCP_API_KEY=%s\n' "$worker_key" >> "$temp_env"
          key_written=true
        fi
        ;;
      *) printf '%s\n' "$line" >> "$temp_env" ;;
    esac
  done < "$ENV_FILE"
  if [[ "$key_written" == false ]]; then
    printf '\nWORKER_MCP_API_KEY=%s\n' "$worker_key" >> "$temp_env"
  fi

  mv "$temp_env" "$ENV_FILE"
  trap - EXIT
  unset worker_key
  echo "Minted a cli-scoped worker key and saved it to gitignored $ENV_FILE."
else
  unset worker_key
  echo "Reusing the worker key already stored in $ENV_FILE."
fi

echo "Starting worker and frontend..."
"${COMPOSE[@]}" up -d worker frontend
"${COMPOSE[@]}" ps
