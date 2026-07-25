# Testing

The Red Team Dashboard test platform has **five layers**, each catching a
different class of regression. Run them from cheapest to most expensive; a
green run of layers 1–3 is the minimum before pushing, layers 4–5 gate a
release.

| # | Layer | What it catches | Where | How to run |
|---|-------|-----------------|-------|------------|
| 1 | **Backend lint + types** | Style/type drift in the control plane + worker | `backend/` | `make lint` / `make typecheck` |
| 2 | **Backend unit/integration (pytest)** | API, services, worker, governance — against real Postgres + Redis | `backend/` | `make test` |
| 3 | **Frontend unit/component (vitest)** | Logic, API contract, React components in jsdom | `frontend/` | `npm run test:run` |
| 4 | **Frontend E2E (Playwright)** | Real user journeys against a running stack | `frontend/` | `npm run e2e` |
| 5 | **Compose smoke** | Container boot + wiring + migration head + MCP URL | repo root | `./scripts/smoke-compose.sh` |
| — | **CLI (pytest)** | CLI client + command parsing (httpx mocked) | `cli/` | `cd cli && pytest -q` |

---

## Backend (`backend/`)

Postgres + Redis must be reachable (the suite uses **real** services, not
mocks). The safe local entry point is:

```bash
docker compose -f infra/docker-compose.yml up -d postgres redis backend
# Recommended: `make test` performs the reset, migration, and pytest run.
# For a direct host run, recreate the disposable database first:
docker compose -f infra/docker-compose.yml exec -T postgres psql -U rtd -d postgres \
  -c 'DROP DATABASE IF EXISTS rtd_test WITH (FORCE);'
docker compose -f infra/docker-compose.yml exec -T postgres psql -U rtd -d postgres \
  -c 'CREATE DATABASE rtd_test OWNER rtd;'
cd backend
export DATABASE_URL="postgresql+psycopg://rtd:rtd@localhost:5432/rtd_test"
export REDIS_URL="redis://localhost:6379/15"
export RTD_MASTER_KEY="$(python -c 'import base64;print(base64.urlsafe_b64encode(b"0"*32).decode())')"
python -m alembic upgrade head     # once after migrations change
python -m pytest -p no:cacheprovider -q
```

`make test` recreates the dedicated `rtd_test` database and Redis DB 15, so
committed test fixtures never enter the operator-facing database. Direct local
pytest runs must likewise target a disposable database such as `rtd_test`;
`tests/conftest.py` refuses the live `rtd` target outside CI unless
`RTD_ALLOW_LIVE_TEST_DB=1` is deliberately set.

**Known host-only failures** (pass in CI/Ubuntu): 3 `test_events_api.py` SSE
tests (need a real uvicorn on :8000) and 1 WeasyPrint test (needs GTK).

## Frontend (`frontend/`)

The frontend shipped **zero** tests until this platform landed. Three layers:

### Vitest — unit + component (`npm run test:run`)

- Config: `frontend/vitest.config.ts`; setup: `frontend/vitest.setup.ts`.
- Env: jsdom. Globals (`describe`/`it`/`expect`) are enabled, but tests use
  explicit imports so `tsc --noEmit` keeps checking them.
- `@/*` resolves to the repo root (same alias as the app), so tests import
  exactly like production code.
- The setup file polyfills `matchMedia` / `IntersectionObserver` /
  `ResizeObserver` (Radix + recharts need them on mount) and stubs
  `next/navigation`.
- Coverage: `npm run test:coverage` (v8, reports to `coverage/`).

Current tests (seeds — grow this file set):
- `test/llm-providers.test.ts` — provider catalog contract.
- `test/api.test.ts` — `ApiError` parsing + the shared `request()` wrapper
  (2xx JSON, 204, non-2xx → `ApiError` with structured detail). This is the
  contract the "Provider key needed" banner and 409 handling depend on.
- `test/kick-run-modal.test.tsx` — v3 playbook kickoff scope parsing (the
  exact UX flagged in the audit) + component/RTL proof against Radix Dialog.

**Conventions:**
- Prefer testing exported pure logic (`lib/**`) and isolated components over
  full pages — pages pull dozens of hooks and become brittle.
- Mock hooks at the `@/lib/hooks` boundary; mock `fetch` via
  `vi.spyOn(globalThis, "fetch")`, not the whole `@/lib/api` module, unless
  you're testing one specific function's payload.
- Use explicit `import { describe, it, expect } from "vitest"`.

### Playwright — E2E (`npm run e2e`)

- Config: `frontend/playwright.config.ts`. Chromium only by default.
- Boots `npm run dev` automatically (unless `RTD_E2E_BASE_URL` is set to an
  already-running stack, e.g. `http://localhost:3000`).
- `npm run e2e:install` installs browser binaries once.
- Baseline smoke: `frontend/tests/e2e/smoke.spec.ts` (app shell renders).
- **Auth:** production uses Entra SSO which can't run headless. Journey specs
  that need a session should gate on `RTD_E2E_AUTHED=1` and a seeded backend;
  a future harness can inject a dev bearer. Keep the public-shell smoke test
  ungated.

### Typecheck + lint (part of every CI run)

```bash
npm run typecheck   # tsc --noEmit  (includes test files)
npm run lint        # next lint
npm run build       # production build — the strictest gate
```

## CLI (`cli/`)

Unit-only, httpx fully mocked (no services needed):

```bash
cd cli && pip install -e ".[dev]" && pytest -q
```

`test_save_writes_0600_perms` is POSIX-only and skips on Windows (chmod only
toggles the read-only bit there; the Windows ACL gap is tracked separately).

## Compose smoke (`scripts/smoke-compose.sh`)

Integration-level "the stack boots and is wired" check. Asserts:

1. backend `/health` responds (which means migrations ran),
2. DB is at the expected migration head (`alembic_version`),
3. the worker container is `running` (not crash-looping),
4. the frontend serves (`/` < 500),
5. the playbook MCP URL the worker resolves **is reachable inside the compose
   network** (catches the `backend:8001` vs container-`8000` class of bug).

```bash
./scripts/smoke-compose.sh            # up + smoke
./scripts/smoke-compose.sh --no-up    # smoke an already-running stack
```

## CI mapping (`.github/workflows/ci.yml`)

| CI job | Layers |
|--------|--------|
| `backend` | ruff + `from app.main import app` + alembic upgrade + pytest (+ uvicorn for SSE) |
| `frontend` | tsc + `next lint` |
| `cli` | ruff + pytest |
| `local-config` | compose `config --quiet` + shell syntax |

**Gaps to close** (tracked in the remediation strategy): CI runs neither the
new vitest layer, nor `npm run build`, nor `smoke-compose.sh`, nor a Bicep
build of the active kit. Adding `npm run test:run` + `npm run build` to the
`frontend` job is the single highest-leverage CI improvement.

## What is NOT covered today (honest gaps)

- **No authenticated E2E.** Every real user journey (create engagement →
  playbook → approve → report) is manual. The Playwright harness exists but
  has no seeded-session story yet.
- **No DB concurrency isolation tests** for the playbook runner's
  claim/cancel/crash-reclaim paths (need live Postgres + `SELECT … FOR
  UPDATE`; partially blocked until the orphan-reclaim fix lands).
- **Frontend coverage is seed-only.** The 19 tests prove the platform; broad
  coverage of findings/strategy/status views is the next milestone.
- **Worker durability is untested end-to-end** (crash mid-run, lease sweep).
