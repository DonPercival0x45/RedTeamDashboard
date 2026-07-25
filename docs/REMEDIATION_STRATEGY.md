# Red Team Dashboard — Remediation Strategy

> Living document. Synthesizes four independent audits (the parent session's
> frontend/backend/security/deploy pass + Ken#3 backend/data-plane +
> Ken#4 cross-layer/CLI) and the fixes already landed on
> `fix/v3-playbook-run-ui`. Severity is **user impact first**, blast-radius
> second. A parallel user-journey audit (6 slices) is in flight; its findings
> will fold into the P2/P3 backlog.

## 1. Current state

`fix/v3-playbook-run-ui` is **ahead of `origin/main` by 29 committed
rollback points**. The most recent completed slices are:

| commit | what | status |
|--------|------|--------|
| current slice | keyless scope hygiene, DNS boundary, dangling-DNS, web baseline, mail posture, and cloud-edge playbooks | ✅ fixed |
| `9d13c56` | scope actions avoid duplicate/opposite mutations; conservative vendor-contact chaff is collapsed but retained | ✅ fixed |
| `3091040` | Entity-to-playbook launch and safe per-step built-in/MCP transport routing | ✅ fixed |
| `dbc56d0` | Playbooks expand authorized discoveries into later steps, support stop-on-error, and preview execution plans | ✅ fixed |
| `8ee1b35` | durable Intelligence jobs, operational MCP playbooks, DeHashed evidence lookup, and durable active-tool approval | ✅ fixed |
| `6c9b2d2` | Runs keeps playbook decisions beside history; effective scope drives targets and destructive scope changes use in-app confirmation | ✅ fixed |
| `206744e` | exact email targets become first-class scope with an applicable governed playbook | ✅ fixed |
| `723fcb2` | analysts bulk-manage discovered Entity scope without duplicate entry | ✅ fixed |
| `398554a` | Compose refreshes the persistent frontend dependency volume when the lockfile changes | ✅ fixed |
| `ae07532` | playbook discoveries, executor affinity, and the Entity workbench converge on canonical contracts | ✅ fixed |
| `53ba6e2` | soft provider preferences route to a live MRU key while explicit identities remain strict | ✅ fixed |
| `737bbd5` | Analytics distinguishes load failure, stale data, and honest empty state | ✅ fixed |
| `3df18b4` | reconciles v3.0.3 with the canonical playbook finding bridge | ✅ fixed |
| `0f46281` | playbook runs are first-class across engagement navigation, status, and decisions | ✅ fixed |
| `3e6470a` | playbook results persist through the canonical Findings contract | ✅ fixed |

**Current validation evidence:**
- backend: Ruff clean; **204 focused playbook/entity/provider tests pass**;
  full suite **1042 passed / 4 documented Windows-host failures / 2 skipped**
- frontend: `tsc --noEmit` and `next build` clean; **72 Vitest tests pass**
- CLI: **35 passed / 1 skipped** (documented Windows permissions limitation)
- local backend tests reset an isolated `rtd_test` database and Redis DB 15;
  pytest refuses the operator-facing `rtd` database outside disposable CI
- Compose: Postgres, Redis, backend, worker, and frontend are running; backend
  is healthy and the live frontend is published on `http://localhost:3001`

Analyst-triggered Strategy, Analysis, Ideation, and Coverage Review now queue as
durable worker jobs and return HTTP 202 instead of holding a fragile browser
request open for the full LLM call. The catalog also includes real MCP-backed
domain/web discovery, IP intelligence, approved host validation, approved CIDR
survey, and mail/DNS collection. Email exposure triage uses imported DeHashed
evidence and persists canonical, deduplicated Findings. Domain/web discovery
feeds authorized Subfinder/CT discoveries into its later HTTP-probe step,
rechecking current exclusions before every expansion; kickoff previews ordered
steps, minimum calls, credential needs, discovery expansion, and whether steps
use built-in or connected-service transports. Mixed recipes route each tool
through its server-owned executor affinity and mint MCP leases only for MCP
steps. Analysts can launch from an exact, currently effective Entity scope row;
the matching target is preselected without trusting arbitrary UI state.

Entity scope controls now reflect actual applicability: already-included targets
show a disabled **In scope** state or a single **Remove from scope** action;
exact exclusions must be explicitly removed before adding the opposite rule;
bulk actions count and mutate only applicable selections. Derived Entities also
receive a conservative advisory relevance tag. Clear role mailboxes on domains
outside scope (for example `abuse@godaddy.com`) are collapsed as likely
third-party contacts by default, remain one click away, and are never deleted,
suppressed, or treated as authorization decisions.

The keyless catalog now also includes **Scope hygiene review**, **DNS ownership
boundary**, **Dangling DNS triage**, **Web security baseline**, **Mail
authentication posture**, and **Cloud/CDN edge boundary**. These use bounded
DNS or single-response HTTP collection, persist canonical posture evidence,
and require no new API credentials. Scope hygiene supports mixed exact scope
kinds, uses `defined`/`found` provenance, and produces recommendations without
changing authorization. Dangling-DNS results are explicitly candidates—not
confirmed takeovers—and edge signals never imply client ownership.

The previous Docker blocker is resolved. Four known Windows-host limitations
remain documented separately (three SSE tail tests and the report renderer's
missing native `libgobject-2.0-0`).

## 2. Severity rubric

- **P0 — app won't run / data loss / auth bypass.** Stop-the-line.
- **P1 — a core user journey is broken or a safety invariant is violated.**
  Fix before the next release.
- **P2 — a real surface is degraded/misleading, or audit/governance gap.**
  Fix in the next wave.
- **P3 — polish, hygiene, latent, or masked-by-polling.** Batch.

## 2A. Operator use-case blockers (reported by the user 2026-07-24)

The operator reported the product is "nigh unusable" for their use-case with 4
specific complaints. Each was verified against the current branch; status and
the fix are below. **These gate the operator's actual use of the product and
take precedence over the generic backlog order.**

### ✅ Complaint 3 — "API configurations were not being respected"
**Status: FIXED** in `6c5432b` (acting-user default honored across every
user-triggered LLM path — role/mode pref → user default → process fallback).
Both peers regression-validated it; **962 backend tests pass** including the
new resolver tests. No further action.

### ✅ Complaint 1 — "Playbooks are referenced in engagements but buried in an entirely different screen"
**Status: FIXED.** Playbooks now have a first-class engagement view. Their
running/terminal lifecycle is visible in Status and running jobs. Awaiting
playbook runs can be approved or rejected from a persistent side-by-side Manage
pane in Runs while the run list stays usable, and they also share the unified
decision inbox with tool approvals. The Scope
tab remains the authoritative place to define targets; it is no longer used as
a substitute Playbooks screen.

### 🔴 Complaint 2 — "A lot of the UI is basically pointless/deprecated because of recent changes"
**Status: NOT FIXED (broad).** v3 landed enforcement before the UI caught up,
leaving dead/misleading surfaces. Confirmed items:
- ~~**Entity quick-actions are dead on v3**~~ **Fixed:** legacy prompt actions
  are hidden on v3; entity preview remains useful for scope/provenance and
  links to the full workbench.
- **Uploaded-tool invocation UI never shipped** (`invokeTool`/`listToolInvocations`
  have zero callers; Tools page still copy-promises it "in v0.12.0" at v3.0.1).
- **3 of 5 Automation tabs are `ComingSoonTab` placeholders** (recon/scanning/
  exploitation) while the header says "pick a workflow to run."
- **Legacy banner / Status "legacy run history" copy** shows on brand-new v3
  engagements with no legacy history.
- Stale comments/copy (`vm-action-menu` "disabled/coming soon" that shipped,
  `costs-view` invocations count that can never be >0).

**Fix (Wave 4/5):** a dedicated **deprecated-UI sweep** — for each surface,
either wire it to the v3 path, hide it for v3, or correct the copy. Deliver as
one coherent pass (not scattered one-offs).

### ✅ Complaint 4 — "Playbook findings aren't ported to the engagement, and the kick modal asks me to re-type scope that's already in findings/scope"
**Status: FIXED, with legacy repair.**

- Playbook DNS, WHOIS, subfinder/CT, and MCP IP-enrichment output now uses the
  canonical finding-grouping bridge, records `FindingOrigin.thread_id`, stages
  requester-attributed feedback, and derives run counters from persisted
  canonical rows. Reruns deduplicate items.
- Validated scope is authoritative for both invocation and persistence. A
  hard-coded step target cannot redirect a built-in tool while retaining
  misleading scope attribution.
- A rerun retires legacy `whois:{{scope_item}}` / literal-target rows and writes
  a trustworthy resolved group.
- Kickoff selects only currently effective included scope items. Deleted rows
  and targets overridden by exclusions disappear from the picker, including
  while it is open; the API still revalidates scope membership and asset class
  before queueing.
- Scope targets and structured playbook discoveries appear in the derived
  Entities inventory without being duplicated into the separate imported
  Entity store.
- Executor affinity is catalog/server-owned. Only an allowlisted, passive tool
  set can be placed on MCP; unsupported or mixed-transport playbooks fail
  closed before queueing.

---

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

## 8. User-journey audit — refinements & new findings (6-slice sweep)

A focused read-only sweep of real operator journeys (onboarding/auth,
engagement setup, findings, strategy/intelligence, playbooks/approvals/status,
reporting/tools/settings/cross-cutting) ran across the branch. Findings below
**merge into** the waves in §5 — new items are tagged `[NEW]`; severity
**bumps** from the §3 tables are called out.

### Severity bumps (promote in §3)
- **Playbook cancel attribution (was P2-7)** → **P1**. Charter invariant
  ("audit_log captures every action"); cancel discards the actor and writes
  no `AuditLog`. Add `cancelled_by` + write `playbook_run.cancelled`.
- **v3 entity quick-actions (was P2-15)** → **P1**. Every recon button in the
  entity slide-over is dead UX on v3 (now the only architecture the wizard
  creates) — `pendingPrompt` is set then dropped because Scope mounts
  Playbooks, not RunPrompt.

### New P1 (stop-the-line for a journey)
- **[NEW] No React error boundary anywhere** — `app/error.tsx` +
  `app/global-error.tsx` are absent, so one unguarded field on any data-dense
  route (Analytics, Infrastructure, report readiness, findings) white-screens
  the **whole route** with no in-app recovery. *Wave 4.*
- **[NEW] No browser path to mint a CLI/API key** — `POST /api-keys` requires
  `X-API-Key` (`RequireScope`), unreachable from an Entra browser session; no
  Settings panel exists. Browser→CLI onboarding is a dead-end. *Wave 4*
  (add admin-only `/admin/api-keys` via `CurrentAdminUser` + a panel).
- **[NEW] Work-item results & comments are backend-only** — `strategy-api.ts`
  defines `listWorkItemResults`/`createWorkItemResult`/`decideWorkItemResult`
  but **zero components import them**; `WorkItemFlyout` renders no results/
  comments/accept-reject. Agent-proposed results are invisible. *Wave 4.*

### New P2 (degraded surfaces)
- **[NEW] Uploaded-tool invocation UI never shipped** — `invokeTool`/
  `listToolInvocations`/`getToolInvocation` have **no callers**; the Tools page
  still copy-promises invocation "in v0.12.0" (we're at v3.0.1). Either ship
  the invocation affordance or correct the copy + delete the dead fns. *Wave 5.*
- **[NEW] `pendingPrompt` leaks across engagements** — slug-change reset clears
  events but not `pendingPrompt`; a legacy quick-action can prefill another
  engagement's run box. *Wave 4* (one line: `setPendingPrompt(null)` in the
  slug effect).
- **[NEW] Methodologies outage dead-ends `/new`** — wizard hard-requires a
  methodology and offers no legacy fallback; a catalog outage blocks all new
  engagement creation. *Wave 4.*
- **[NEW] PDF export discards backend error detail** —
  `downloadEngagementReport` throws bare `${status} ${statusText}` (unlike the
  JSON export's parsed `ApiError`); PDF failures are undiagnosable from the UI.
  *Wave 5* (~6 lines).
- **[NEW] Engagement Memory has no API/UI** — `MemoryElement`/`memory.py` are
  internal-only; no `GET /engagements/{slug}/memory`, no restore path from
  the UI. *Wave 4* (read + restore endpoints + Strategy panel).
- **[NEW] Status tab is blind to playbook runs on v3** — `get_engagement_status`
  returns agents/tasks/approvals only; v3 analysts must context-switch to find/
  cancel their primary execution surface. *Wave 4* (add a `playbook_runs` slice
  or a deep-link card).
- **[NEW] No unified approval queue for awaiting playbook runs** — the bell
  lists LangGraph interrupts only; awaiting playbook runs live per-engagement.
  *Wave 4* (`GET /playbook-runs?status=awaiting_approval` + inbox badge).
- **[NEW] Settings "← engagements" back-link leaves the modal open** on every
  panel (navigation happens under a still-open overlay). *Wave 4* (one line:
  `useEffect(() => setSettingsOpen(false), [pathname])`).
- **[NEW] Admin-gated settings pages fire guaranteed 403s for user/guest**
  before the role guard renders (`integrations`/`management`/`tools` call their
  admin hooks unconditionally on direct-URL). *Wave 4* (gate hooks on
  `me?.is_admin` / `AdminOnlyGate`).
- **[NEW] `?setup=initial-guidance` deep-link is read but never produced** —
  two info banners are unreachable in normal flow (dead code). *Wave 4.*

### New P3 (polish/hygiene batch — Wave 6)
- Dead `IdentityMenu` component (superseded by `LeftSidebar` `UserChip`).
- `/settings/keys` copy omits that **sign-out clears all provider keys**
  (contradicts "held for the entire session").
- `/settings/keys` comment falsely claims a guest `enabled` guard exists.
- CLI README stale "web viewer is read-only" claim.
- Kick modal doesn't dedupe scope (duplicate rows → duplicate coverage records).
- Approve double-click → benign 409 flash (no optimistic disable/close).
- `LegacyEngagementBanner` dismiss copy says "next login" but it's
  tab-scoped `sessionStorage`.
- "Convert to v3" button is a tab-switch dressed as an action (no scroll/focus).
- v3 Status banner shows "legacy run history is read-only" on brand-new
  engagements with no legacy history.
- `/new` wizard has no guest gate (guests fill the form, 403 on submit).
- Engagement-card inline scope-add diverges from the full editor (no Found/
  note fields).
- `ReadOnlyNotice` conflates engagement status with work-state completion.
- Strategy workspace bypasses React Query (manual seq-counter fetch of 8–13
  slices every 30s + on focus).
- `KickRunModal` missing `DialogDescription` (a11y — surfaced by the new test).
- Pervasive native `confirm`/`alert`/`prompt` (37 sites) — not themed/a11y.
- App shell not responsive (no mobile/tablet layout).
- Automation defaults to Reporting, not Playbooks; 3 of 5 tabs are placeholders.
- `vm-actions-menu.tsx` header comment still says actions are "disabled /
  coming soon" (they shipped).
- `costs-view.tsx` reads an `invocations` count that can never be >0 from UI.

### Findings workspace (highest-traffic surface — 6th slice)
No P0/P1 on this surface, but 3 evidence-backed P2s + 8 P3s:

- **[NEW] `finding.updated` SSE events silently dropped** — the worker emits
  `finding.updated` when a live re-run folds new hits into a grouped parent,
  but the frontend handler covers only `finding.created`/`run.completed`/
  `run.errored`/`approval.pending` (and `finding.updated` isn't even in the
  `RunEvent` union). Grouped parent counts/items go stale **while work is in
  flight** until window refocus. *Wave 4* (add the event type + invalidate
  `qk.findings(slug)`; do NOT upsert — the payload is only a count).
- **[NEW] Failed findings fetch renders as "No findings yet."** —
  `findingsQuery.error` is never folded into the page error, so a 500/401
  shows the first-run empty state and an analyst may re-import (creating
  duplicates). *Wave 4* (render a distinct "could not load — retry" banner).
- **[NEW] Attachment evidence path fails silently** — upload/delete wrap the
  call in `try/finally` with no `catch` (silent 413/415/403/network), the
  file input never resets `value` (same file can't be re-selected after
  delete), and list-load failure renders "No attachments yet." *Wave 4*
  (error state + `event.target.value=""`).
- **P3 batch:** bulk-delete result note set into an already-unmounted bar;
  summary-history + comments fetch failures render empty copy; failed image
  preview stuck on "Loading preview…"; tag add/remove fails silently; **guest
  role sees a fully-armed write workspace** (every action 403s, some
  silently) — gate on `me?.role !== "guest"`; slug-less finding page fires a
  malformed `GET /engagements//findings` (add `enabled: Boolean(slug)`);
  AddFindingModal copy promises an observed-date fallback that doesn't exist;
  observation link/unlink failures silent.

### Reconciliation (orphan-reclaim)
The playbooks journey reviewer notes `playbook_worker._execute`'s outer
try/except marks botched runs `failed` (Python-level exceptions are handled).
This is **consistent** with Ken#3's P1-1: the orphan case is **not** a Python
exception — it's a hard `SIGKILL`/OOM or the daemon-thread `join(timeout=5)`
abandoning the in-flight transaction during graceful rolling deploy, which
never reaches the except handler. P1-1 stands for crash/deploys.

## 9. User-friendliness / institutional-knowledge audit (3-slice sweep)

A persona-driven sweep (brand-new analyst, zero tribal knowledge) across
navigation/IA, consistency/copy, and onboarding/learnability. Two systemic
root causes explain most of the "user unfriendly" pain; the fixes are
structural, not one-off.

### Root cause A — v3 objects aren't first-class on legacy surfaces
The v3 migration added PlaybookRun / awaiting_approval / Strategy-intelligence
**without retracting** tasks / Approval / RunPrompt / the Status feed. A new
analyst's three most common questions — *is anything running, does anything
need me, what just happened* — have no single honest answer:
- **Status is a museum for v3** (shows legacy history, never playbook runs;
  the global "Running jobs" banner polls legacy `/tasks/running`).
- **Two parallel approval systems never meet** — the bell reads only the legacy
  `Approval` table; gated playbook runs (`awaiting_approval`) produce no badge.
- **Playbook kickoff lives in Scope + Automation, never as a first-class Runs
  nav item**; the engagement nav has no Runs view at all.
**Structural fix (Wave 4, elevates complaint #1):** union `PlaybookRun` into
the Status feed + running-jobs + the approval inbox; add a dedicated **Runs**
nav item; give awaiting playbook runs a bell badge.

**Landed locally:** Status now includes playbook runs and durable lifecycle
steps; the nav is labeled **Runs**; a compatibility-safe `/jobs/running`
composes legacy tasks + live playbooks; `/decision-inbox` composes both
approval state machines with a discriminated contract; the Radix bell reuses
the correct existing decision modal for each kind. Awaiting playbooks cannot
bypass the reason/actor-preserving reject path via generic cancellation.

### Root cause B — React Query errors are swallowed into `?? []` / `!data`
**Six+ surfaces collapse "failed" into "empty" or "loading forever":**
- Findings fetch failure → "No findings yet." (coaches the user to re-run and
  create duplicates) — the worst instance.
- Run-detail modal → "Loading run…" spins forever on error.
- Playbooks catalog/runs failures → honest-looking empty states.
- Observations/Status render error line **and** "nothing here, go do work"
  coaching copy underneath.
- Engagements list shows "Loading…" forever alongside the error line.
**Systemic fix (Wave 4):** a shared `<QueryState>` helper (error / loading /
empty / children) adopted at every `useQuery` site. Review rule: *any
`useQuery` whose `error` isn't read is a bug.*

**Foundation landed locally:** shared accessible `<QueryState>` with retry and
stale-cache warnings, adopted in engagement Findings, Playbooks catalog/runs,
run detail, Status/Runs, Running Jobs, Infrastructure, Configurations, the
decision inbox, and every Analytics panel. Imported Entities no longer renders
a terminal error alongside an infinite loader. Analytics preserves cached data
with a stale warning, blocks incomplete exports, and has component regressions
for failed, legitimately empty, and stale-cache states. Remaining auxiliary
query consumers are the next adoption batch.

### Provider routing invariant landed locally
Saved role/mode preferences plus user/process defaults are **soft preferences**:
if that provider has no live ephemeral credential, user-triggered LLM paths use
the analyst's newest routable model-provider entry and attribute the actual
provider/model. One-shot provider choices, explicit `key_id` values, worker
envelopes, and tool-secret names remain strict. Direct and Tactical producers
pin the resolved key row into the cache and durable envelope so queue delay or
same-provider rotation cannot silently change accounts. Missing credentials
return an actionable 400; Redis credential/queue outages return 503. Coverage
now spans helper selection, direct responses/cache/envelopes, worker key use,
Tactical telemetry/cache/envelopes, v3 mode construction, finding chat,
strategist attribution, tool review/pricing, and failure responses.

### Notable consistency/copy offenders (each maps to a small fix)
- **Entity quick-actions dead on v3** (already P1) — every new engagement.
- **"Once v0.12 ships" copy on a v3.0.1 app** (Tools page, 3 instances).
- **"Legacy run history" banner on fresh v3 engagements** (no legacy history).
- **`canWrite` hardcoded `true`** — guests see an armed write workspace that
  403s on submit (Settings pages gate correctly; `/e` doesn't).
- **Dismiss copy says "next login" but is tab-scoped** `sessionStorage`.
- **3 of 5 Automation tabs are placeholders** (Recon/Scanning/Exploitation);
  "Exploitation" promises a charter-forbidden feature; default tab is the last.
- **Native `confirm`/`alert`/`prompt` in 16 files** vs Radix elsewhere —
  including `confirm()` fired from inside a Radix dialog (run-detail cancel).
- **Internal jargon as UI vocabulary**: "Gated" (= approval required),
  "Memory", "v3 rollout", truncated-UUID attribution, `stream open/closed`
  subtitle, fake percentage progress bars, two state machines both labeled
  "active".
- **Stale guidance pointing v3 users at legacy-only paths** (dossier,
  default-tools-banner, getting-started step 4).
- **Dead `IdentityMenu` component** still in the tree.

### IA fixes (target ~6 nav items, one guessable job each)
- Merge **Contributions** + **Diagnostics** into Status as sub-views (heatmap,
  "export debug bundle"). Diagnostics is a dev tool in prime analyst nav space.
- Rename **Dossier → IP Intel** (it's an enrichment slice, not a target report).
- **Observations vs Findings** is an unguessable distinction — merge as a note
  type or relabel.
- Pick one Settings shell (modal as primary); add the two orphaned panels
  (Agent Runs admin-only, What's New); align the Models/Keys label drift.

### Onboarding keepers (don't break these)
`/new` setup-aware redirect; Scope-tab empty state; Run Prompt
provider-readiness banner; Keys-page guest card (the role-gating pattern `/e`
is missing); engagements-list genuine-empty + pending-teaching banners;
type-the-slug delete confirmation; the run → findings feedback toast.

## 10. Durable playbook step receipts and evidence boundary

Coverage records answer whether a methodology node was satisfied; they do not
identify one concrete tool invocation. One invocation can produce zero, one,
or several coverage records, so the run aggregate previously could not answer
which target ran, how long it took, what transport was used, or which evidence
supported the resulting Finding.

**Landed locally on `feat/playbook-evidence-receipts`:**

- `PlaybookStepExecution` stores one immutable, per-target attempt receipt with
  the catalog-step snapshot, server-owned transport, target-bound redacted
  arguments, status, duration, and failure detail.
- `EvidenceArtifact` preserves bounded JSON tool output independently of the
  Findings workspace. Clean results remain evidence rather than becoming fake
  security issues; canonical Findings can be linked when the finding bridge
  creates or enriches one.
- Secret-like keys, bearer/basic credentials, URI passwords, nested string
  values, receipt errors, run errors, and coverage notes are sanitized before
  durable persistence. Oversized artifacts store a preview plus the complete
  redacted payload digest and byte count.
- Worker-owned runs now carry an independently refreshed heartbeat. Expired
  worker leases are marked failed together with any in-flight receipt, with an
  explicit “outcome unknown; not retried” message. Recovery never replays an
  external call whose side effects may already have happened.
- Run details show accessible per-target attempt receipts and lazy-load the
  redacted evidence payload. Evidence linked to a canonical Finding provides a
  direct navigation path.
- Kickoff now requests an authoritative server-generated execution plan with
  exact known targets, ordered steps, transports, risk labels, credential
  requirements, minimum calls, approval state, and explicit dynamic-expansion
  semantics. The client submits the plan digest; a changed plan is rejected for
  review instead of being silently queued. The immutable plan snapshot remains
  visible beside later approval decisions.
- Playbook execution now has a dedicated, supervised worker pool with two
  lanes in local Compose and a separate sibling worker process in the Azure
  Container App pod. PostgreSQL advisory scheduling enforces global and per-engagement
  concurrency caps without weakening per-run ownership fencing or replaying an
  uncertain external call. The general worker no longer duplicates playbook
  execution when the dedicated service is enabled.
- Worker processes, components, idle/busy lane heartbeats, current runs, and
  redacted operational incidents are durable. Docker health checks fail when a
  process heartbeat expires, critical component death exits for container
  restart, and the Runs view shows truthful animated worker activity, progress,
  queue depth/age, offline/degraded state, and recent incidents.

This is additive: `CoverageRecord` remains methodology coverage,
`Observation` remains the analyst notebook, and `Finding` remains an
actionable/reportable conclusion. Evidence does not authorize targets, validate
Findings, or change reportability.


