# Developer ergonomics for the local compose stack.
#
# All targets are .PHONY — there are no file products. Run `make help` for
# the menu.

COMPOSE := docker compose -f infra/docker-compose.yml -f infra/docker-compose.override.yml
BACKEND := $(COMPOSE) exec -T backend
TEST_DATABASE := rtd_test
TEST_DATABASE_URL := postgresql+psycopg://rtd:rtd@postgres:5432/$(TEST_DATABASE)
TEST_REDIS_URL := redis://redis:6379/15
TEST_BACKEND := $(COMPOSE) exec -T \
	-e ENV=test \
	-e DATABASE_URL=$(TEST_DATABASE_URL) \
	-e REDIS_URL=$(TEST_REDIS_URL) \
	backend

.DEFAULT_GOAL := help
.PHONY: help up down rebuild doctor logs logs-backend logs-worker logs-frontend \
        test test-fast test-db-reset lint typecheck check shell-backend shell-redis \
        psql redis-flush worker-stop worker-start

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------------------
# Stack lifecycle
# ---------------------------------------------------------------------------

up: ## Bootstrap and bring up the full local stack
	./scripts/local-up.sh

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

# Tests must never inherit the backend container's live `rtd` database or
# Redis DB 0. Reset a dedicated database and Redis namespace on every run so
# committed API fixtures cannot appear in the operator's engagement list.
test-db-reset: ## Recreate the isolated local test database and Redis namespace
	$(COMPOSE) exec -T postgres psql -v ON_ERROR_STOP=1 -U rtd -d postgres \
		-c 'DROP DATABASE IF EXISTS $(TEST_DATABASE) WITH (FORCE);'
	$(COMPOSE) exec -T postgres psql -v ON_ERROR_STOP=1 -U rtd -d postgres \
		-c 'CREATE DATABASE $(TEST_DATABASE) OWNER rtd;'
	$(COMPOSE) exec -T redis redis-cli -n 15 FLUSHDB >/dev/null
	$(TEST_BACKEND) alembic upgrade head

test: test-db-reset ## Reset isolated services and run the full backend suite
	$(TEST_BACKEND) pytest -q

test-fast: test-db-reset ## Reset isolated services and run pytest without Make orchestration
	$(TEST_BACKEND) pytest -q

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
