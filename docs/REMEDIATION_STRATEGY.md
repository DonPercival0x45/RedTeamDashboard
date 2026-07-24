# Red Team Dashboard — Remediation Strategy

> Living document. Synthesizes four independent audits (the parent session's
> frontend/backend/security/deploy pass + Ken#3 backend/data-plane +
> Ken#4 cross-layer/CLI) and the fixes already landed on
> `fix/v3-playbook-run-ui`. Severity is **user impact first**, blast-radius
> second. A parallel user-journey audit (6 slices) is in flight; its findings
> will fold into the P2/P3 backlog.

## 1. Current state

`fix/v3-playbook-run-ui` is **ahead of `origin/main` by 4 commits**:

| commit | what | status |
|--------|------|--------|
| `382a97a` | v3 engagements route runs through Playbooks; v3 Status hides legacy retry | ✅ fixed |
| `6c5432b` | **P0** dead 204 routes (app wouldn't import) + Anthropic-key nonsense across all user LLM paths + dead MCP port + RunPrompt UI race + CLI provider set + CLI 0600 Windows skip | ✅ fixed |
| `a030e30` | `SANDBOX_RUNNER` env-name mismatch (prod sandbox broken) + cancel cache key | ✅ fixed |
| `166fb19` | **Test platform**: vitest + playwright + compose smoke + docs | ✅ landed |

**Validation gates now green locally:**
- backend: ruff clean, `from app.main import app` ok, **965 tests collect**
- frontend: `tsc --noEmit` clean, `next build` clean, **19 vitest tests pass**
- CLI: ruff clean, **35 passed / 1 skipped**

**Blocking the full backend pytest run:** Docker Desktop is **not running** on
this host. The 965-test suite needs Postgres + Redis (real services, not
mocks). Starting Docker unblocks layers 2 + 5 of the test platform.

## 2. Severity rubric

- **P0 — app won't run / data loss / auth bypass.** Stop-the-line.
- **P1 — a core user journey is broken or a safety invariant is violated.**
  Fix before the next release.
- **P2 — a real surface is degraded/misleading, or audit/governance gap.**
  Fix in the next wave.
- **P3 — polish, hygiene, latent, or masked-by-polling.** Batch.

## 3. Master backlog (deduplicated across all audits)

### 🔴 P1 — broken journeys / safety

| # | Finding | Files | Fix sketch | Effort | Validation |
|---|---------|-------|------------|--------|------------|
| P1-1 | **Playbook runs orphan in `running` forever** on worker crash / rolling deploy (no reclaim; daemon-thread shutdown abandons the in-flight txn) | `services/playbook/runner.py`, `worker/playbook_worker.py`, `worker/main.py` | heartbeat/lease on claim; sweeper reclaims stale `running` → `pending`/`failed`; `_execute` honors `stop_event`; per-step commit | M | new tests: orphan-reclaim-after-lease, shutdown-doesn't-strand (SlowExecutor + stop_event) — **needs live PG** |
| P1-2 | **Custom tool invocations bypass scope + risk gate** (`invoke_tool` never calls `evaluate_scope`; `target=169.254.169.254` accepted) | `services/tool_invocation.py`, `api/tool_invocations.py` | identify manifest args with `scope_kind`, gate them like built-ins, fail-closed on missing scope metadata | M | API tests: exact/subdomain/CIDR/exclusion/link-local/internal + active/destructive approval interrupt |
| P1-3 | **MCP active tools self-approve** (synthesizes an approved `Approval` and executes immediately on `Action.interrupt`) | `mcp/server.py` | return a pending approval; require a bound decision before execution | M | MCP tests: active call creates pending row + does no network work; denial prevents; changed args need new approval |
| P1-4 | **CLI has no v3 path** — `rtd run start` 409s on every v3 engagement and there's no playbook alternative | `cli/src/rtd/commands/`, backend `api/engagements.py` | add `rtd playbook {list,show,run,runs,status,approve,reject,cancel,queue}` (Ken#4 spec ready); detect v3-409 in `run start` and hint to `playbook run` | M | CLI pytest per command (respx-mocked) + smoke against local stack |

### 🟠 P2 — degraded surfaces / governance

**Auth & data integrity**
- **P2-1** Deactivating a user does **not** revoke their API/MCP keys (`api/deps.py` API-key branch skips `_require_active`; MCP `mcp/auth.py` skips `is_active`). *3-line fix.* Regression test: deactivate → old key 403s on HTTP + MCP.
- **P2-2** **Default config accepts caller-chosen identity** — no `ENV` → `env=local` → `allow_x_user_id=True`; any caller sends `X-User-Id: <admin>` and becomes admin (`core/config.py`, `api/deps.py`). *Fix:* default to prod-safe; `ALLOW_X_USER_ID` explicit opt-in; refuse to resolve existing privileged users from an unsigned header.
- **P2-3** Provider-key **probe is an authenticated SSRF primitive** (`POST /me/provider-keys/probe`, caller-controlled `endpoint`, no scheme/IP validation; `http://127.0.0.1:8000/health?x=` → `…/models`). *Fix:* HTTPS-only allowlist; reject loopback/private/link-local per redirect.
- **P2-4** Integration secrets (Discord tokens, PATs, custom headers) stored as **plaintext JSONB** (`models/integration.py`, `services/integrations.py`). *Fix:* envelope-encrypt secret fields / Key Vault.
- **P2-5** "Ephemeral" provider keys default to **indefinite** plaintext retention + local Redis snapshots (`provider_key_ttl_seconds=0`, compose `/data` volume). *Fix:* bounded server-side TTL; never `PERSIST`; disable snapshots or encrypt.
- **P2-6** Uploaded **tool source readable by guests/viewers** via `validation.source_b64` (`api/tools.py` list/detail are `CurrentUser`). Contradicts the schema's masking boundary.

**Playbook governance**
- **P2-7** Playbook catalog mutations + cancel are **unattributed** (`api/playbook.py` + `services/playbook/catalog.py` write no `AuditLog`; `cancel_run` takes no `cancelled_by`). *Fix:* audit every create/update/delete + run transition; mirror `approve_run`/`reject_run` attribution.
- **P2-8** Playbook approve/reject/cancel **race** (unlocked `session.get` then update, unlike the main approval endpoint's `SELECT … FOR UPDATE`). Concurrent approve+reject can corrupt attribution. *Fix:* conditional `UPDATE … WHERE status='awaiting_approval'` requiring exactly one affected row.
- **P2-9** Playbook **recipes mutate after enqueue/approve** — worker reloads current steps, so an edit changes what runs vs. what was approved; `steps_total`/slug/version are mutable too. *Fix:* snapshot ordered steps at enqueue/approve; edits create a new version.
- **P2-10** Playbook **finding counts diverge from persisted findings** (internal DNS/WHOIS count answers as findings but persist nothing; `engagement_rollup` gathers by `thread_id==run_id` that's never set). *Fix:* one finding contract — executors return candidates, runner persists + stamps `FindingOrigin.thread_id`.
- **P2-11** Playbook `scope_subset` accepts **arbitrary strings** passed straight to tools (no engagement-scope membership check) — defeats the in-scope-only invariant. *(Known; pairs with P2-9.)*
- **P2-12** Playbook **MCP runs not run-bound** (no lease; seeded `osint-enrichment` MCP templates lack `engagement_slug` → every step errors; attribution falls to worker API-key user). *Fix:* mint a run-bound lease (engagement, tools, scope, requester/approver, expiry).
- **P2-13** Mid-run **cancel blocks** behind the worker's uncommitted step txn (cancel can't observe uncommitted status; can even overwrite completed→cancelled). *Fix:* per-step commit / control row.

**Frontend UX**
- **P2-14** **`canWrite` hardcoded `true`** before `/me` resolves; guests see mutation controls that 403 on submit (`app/e/page.tsx`). *Fix:* derive from `/me` + lifecycle; pass to every mutation view.
- **P2-15** **v3 entity quick-actions silently discard** their prompt (they route to Scope, which now mounts Playbooks, not RunPrompt). *Fix:* architecture-aware callback (map to playbook kickoff w/ scope preselected) or hide on v3.
- **P2-16** **Regroup leaves findings cache incomplete** (absorbed rows removed, parent not upserted/invalidated until window focus). *Fix:* invalidate `qk.findings(slug)` on apply.
- **P2-17** **Playbook query failures render as empty/loading** (coerce-to-`[]` hides 401/500; `RunDetailModal` spins "Loading" forever on error). *Fix:* render `query.error` + retry.
- **P2-18** **v3 legacy escape hatch unreachable from UI** — backend honors `enforce_v3_playbook_only=false` but the frontend unconditionally hides retry/prompt for v3. *Fix:* expose an effective-gate capability and drive UI from it (or remove the documented hatch).

**Infra / deploy**
- **P2-19** Legacy `infra/azure` Bicep **doesn't compile** + worker missing `WORKER_MCP_API_KEY` + KEDA watches a nonexistent stream. *Decision:* archive in favor of `azure-kit` (recommend) or repair.
- **P2-20** `deploy.yml` builds/updates **backend only** — no frontend, no tests, no post-rollout health. *Fix:* build+update frontend; run the release gates; poll `/health`.
- **P2-21** **3 HIGH npm advisories** (postcss/sharp via next). *Fix:* bump next/sharp/postcss; regenerate lock; document residual risk.
- **P2-22** **Azure ARM routes can act outside** `infra_subscriptions` (direct get/start/restart/run-command trust caller-supplied `arm_id`). *Fix:* centralize `VmRef` auth; reject unlisted subscriptions.
- **P2-23** `/releases.json` is **unauthenticated** (low-sensitivity; likely intentional for login page — document or gate).

### 🟡 P3 — polish / hygiene / latent

- **P3-1** `useFlushEngagementMutation` invalidates only `qk.engagements()` → slug-scoped caches go stale.
- **P3-2** Dead-lettered messages leave `ProcessingReceipt` stuck in `processing` (hygiene).
- **P3-3** Lifecycle events carry random `event_id` (bypass outbox) → crash-reprocess can double-fire milestones / dup `Approval` rows. Derive deterministic `event_id`.
- **P3-4** `provider_key_master` default is a **valid** hardcoded Fernet key + stale "dead" comment (`secret_box` still imports it).
- **P3-5** `PlaybookRunRead.requested_by` dropped from the frontend type (UI shows approver/rejecter, never requester).
- **P3-6** `GET /playbook-runs/{id}` is `CurrentUser`-only (guests/viewers can read any run by UUID).
- **P3-7** ORM missing `server_default` on 5 columns (autogenerate noise).
- **P3-8** `GET /engagements/{id}/approvals` + `/authorizations` typed as UUID while everything else uses `{slug}` (`listApprovals` would 422 if called — currently dead).
- **P3-9** Flushed-engagement status divergence: `tool_invocations` → 404, others → 409.
- **P3-10** Guest can mutate `/me/preferences` despite "read-only" (`CurrentUser` vs `CurrentNonGuestUser`).
- **P3-11** Health endpoint conflates liveness + readiness → dependency outage restarts a healthy process; split `/live` + `/ready`; add worker/MCP/frontend probes.
- **P3-12** Backend prod image is nondeterministic (no lock) + carries dev/build/test content.
- **P3-13** `app.main` hardcodes FastAPI version `0.0.1`; release checks only validate CLI version.
- **P3-14** Stale docs: `frontend/.env.example` (build-time `NEXT_PUBLIC_*`), `infra/.env.example` (Entra "future"), README alembic head (`0053` vs `0064`).
- **P3-15** CLI portability: `os.execvp` on Windows, redundant SIGINT handler, `open()` without encoding, network errors dump tracebacks.
- **P3-16** Self-approval: any non-guest can approve their own playbook run (documented design; revisit if governance tightens).
- **P3-17** `KickRunModal` missing `DialogDescription` (a11y warning surfaced by the new test) — add `aria-describedby` or a `DialogDescription`.

## 4. Test platform — status & roadmap

**Landed (`166fb19`):** vitest + playwright + compose smoke + `docs/TESTING.md`.
19 frontend tests green; the 5-layer model is documented.

**Immediate gaps to close (high-leverage, low-risk):**
1. **Wire vitest + `npm run build` into CI's `frontend` job.** Today CI runs
   only `tsc` + `next lint`. One job addition catches every SSR/build regression.
2. **Add `./scripts/smoke-compose.sh` + `az bicep build` to CI.** Catches the
   `backend:8001` and Bicep-compile classes statically.
3. **Grow frontend coverage** from 3 seed files → per-view suites
   (findings, strategy, status, playbooks). Each P2 frontend fix should land
   with the test that would have caught it.
4. **Authenticated E2E harness:** seed a dev bearer + a scripted backend state
   so Playwright can drive real journeys (create → playbook → approve → report).
   This is the single biggest coverage unlock.
5. **Backend:** once Docker is up, add the **playbook-runner concurrency suite**
   (claim/cancel/crash-reclaim) — these are the P1-1/P2-13 paths and they have
   no live-PG coverage today.

**Honest current gaps (also in `docs/TESTING.md`):** no authenticated E2E; no
DB-concurrency isolation tests for the playbook runner; frontend coverage is
seed-only; worker durability untested end-to-end.

## 5. Recommended execution order

**Wave 0 — unblock validation (now):** start Docker; run the full backend
pytest once on a clean DB to get a green baseline (the P0 import fix means it
can finally collect *and* run).

**Wave 1 — safety + broken journeys (P1):**
P1-1 orphan reclaim → P1-2 tool-invocation scope gate → P1-3 MCP self-approve →
P1-4 CLI v3. Each ships with the test that reproduces it.

**Wave 2 — governance + auth (P2 auth cluster):**
P2-1 is_active gate (3 lines) → P2-2 X-User-Id default → P2-3 probe SSRF →
P2-4/P2-5 secret retention → P2-6 tool-source disclosure. These are mostly
small, high-value, and independent.

**Wave 3 — playbook correctness (P2 playbook cluster):**
P2-7 audit attribution → P2-8 race (FOR UPDATE) → P2-9 immutable recipes →
P2-10 finding contract → P2-11 scope membership → P2-12 run-bound lease →
P2-13 cancel. These are interdependent; do them as one coherent PR series.

**Wave 4 — frontend UX (P2 UX cluster):** P2-14 canWrite → P2-15 v3 quick-actions
→ P2-16 regroup cache → P2-17 error states → P2-18 escape-hatch capability.

**Wave 5 — infra/deploy (P2 infra cluster):** P2-19 legacy bicep decision →
P2-20 deploy.yml → P2-21 npm advisories → P2-22 ARM scope.

**Wave 6 — P3 batch + CI hardening** (add vitest/build/smoke/bicep to CI).

## 6. Decisions needed before code

| Item | Question | Default recommendation |
|------|----------|------------------------|
| `infra/azure` legacy stack | repair or archive? | **Archive** — `azure-kit` supersedes it. |
| Export auth asymmetry (`POST` admin-only, `GET` any-user, same payload) | is export governance/audit or read? | Treat as read; drop the `POST` gate; keep blob-upload audit logging. |
| `enforce_v3_playbook_only` escape hatch | keep the operator hatch? | Expose an effective-gate capability to the UI; or remove the hatch from docs+code. |
| Provider-key TTL default (`0` = never) | keep session-length default? | Flip to a bounded default (e.g. 8h) with an operator override. |
| Playbook self-approval | require four-eyes? | Keep (documented) until governance asks otherwise. |

## 7. Risk register

- **Concurrency fixes (P1-1, P2-8, P2-13)** need live Postgres to validate
  `FOR UPDATE` semantics — blocked until Docker is available or run in CI.
- **MCP run-bound lease (P2-12)** is a meaningful protocol change; spec before
  coding.
- **Auth cluster (P2-1/2/3)** touches every request path — ship behind the new
  test platform and a focused regression run.
- The 6-way user-journey audit in flight may surface additional P2/P3 UX items;
  this doc's P2/P3 tables will grow when it lands.
