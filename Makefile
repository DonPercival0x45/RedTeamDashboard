# Developer ergonomics for the local compose stack.
#
# All targets are .PHONY — there are no file products. Run `make help` for
# the menu.

COMPOSE := docker compose -f infra/docker-compose.yml -f infra/docker-compose.override.yml
BACKEND := $(COMPOSE) exec -T backend

.DEFAULT_GOAL := help
.PHONY: help up down rebuild doctor logs logs-backend logs-worker logs-frontend \
        test test-fast lint typecheck check shell-backend shell-redis psql \
        redis-flush worker-stop worker-start

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------------------
# Stack lifecycle
# ---------------------------------------------------------------------------

up: ## Bring the full stack up (postgres, redis, backend, worker, frontend)
	$(COMPOSE) up -d

down: ## Stop the stack (preserves volumes)
	$(COMPOSE) down

rebuild: ## Rebuild backend + worker images and recreate containers
	$(COMPOSE) up -d --build backend worker

doctor: ## Show branch, container health, and DB migration revision
	@git branch --show-current | sed 's/^/branch: /'
	@$(COMPOSE) ps --format 'table {{.Service}}\t{{.State}}\t{{.Health}}'
	@$(COMPOSE) exec -T postgres psql -U rtd -d rtd -tAc 'select version_num from alembic_version;' | sed 's/^/db revision: /'

worker-stop: ## Stop only the worker (used by `make test`)
	$(COMPOSE) stop worker

worker-start: ## Start the worker
	$(COMPOSE) start worker

# ---------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------

logs: ## Tail logs from all services
	$(COMPOSE) logs -f

logs-backend: ## Tail backend logs
	$(COMPOSE) logs -f backend

logs-worker: ## Tail worker logs
	$(COMPOSE) logs -f worker

logs-frontend: ## Tail frontend dev-server logs
	$(COMPOSE) logs -f frontend

# ---------------------------------------------------------------------------
# Tests + lint
# ---------------------------------------------------------------------------

# `make test` is the safe-to-run target: stops the compose worker so its
# real-LLM consumer doesn't race with the test-thread workers, runs the
# suite, then restarts it. Exit code propagates from pytest so CI catches
# real failures and not the trailing `worker-start`.
test: ## Stop worker, run full pytest suite, restart worker
	@$(COMPOSE) stop worker >/dev/null 2>&1 || true
	@status=0; $(BACKEND) pytest -q || status=$$?; \
		$(COMPOSE) start worker >/dev/null 2>&1 || true; \
		exit $$status

test-fast: ## Run pytest assuming the worker is already stopped
	$(BACKEND) pytest -q

lint: ## Ruff lint over backend
	$(BACKEND) ruff check app tests

typecheck: ## mypy over backend
	$(BACKEND) mypy app

check: lint test ## Lint + tests (the gate before pushing)

# ---------------------------------------------------------------------------
# Direct access
# ---------------------------------------------------------------------------

shell-backend: ## Drop into a bash shell in the backend container
	$(COMPOSE) exec backend bash

shell-redis: ## redis-cli
	$(COMPOSE) exec redis redis-cli

psql: ## Postgres shell (rtd/rtd@rtd)
	$(COMPOSE) exec postgres psql -U rtd -d rtd

redis-flush: ## Delete all runs:* streams (orphans from killed tests)
	$(COMPOSE) exec -T redis redis-cli --no-raw eval \
		"local keys = redis.call('KEYS', 'runs:*'); for i,k in ipairs(keys) do redis.call('DEL', k) end; return #keys" 0
