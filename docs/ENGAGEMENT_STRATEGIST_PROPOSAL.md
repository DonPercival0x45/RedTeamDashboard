# Engagement Strategist and Persistent Work Ledger

**Status:** Proposed

**Prepared:** 2026-07-14

**Audience:** Product, engineering, security, and analyst stakeholders
**Scope:** Product and technical design; implementation is intentionally decomposed into reviewable PRs

## 1. Executive summary

RedTeamDashboard should provide a persistent engagement operating layer that lets an analyst open an engagement and understand, within approximately one minute:

- what the current strategy is;
- what changed since the analyst last worked;
- what is active, blocked, deferred, or awaiting a decision;
- which findings and entities matter to the current objectives;
- what work has already been attempted and what its outcome was;
- what should happen next; and
- what remains before the engagement can be intentionally completed.

The recommended feature is an **Engagement Strategist**, backed by durable, shared strategy, objective, work-item, coverage, decision, and checkpoint records. The agent is a reasoning interface over that state. It is not the sole memory of the engagement and is not a new execution path.

The implementation preserves the platform's existing governance model:

- agents propose; analysts decide;
- Tactical remains the only agent execution dispatcher;
- automated agents perform permitted enumeration and scanning only;
- active operations continue through deterministic scope checks and approvals;
- analyst-only validation work is never automatically dispatched;
- expanding Found Scope remains an explicit, audited analyst decision; and
- every consequential recommendation, decision, revision, and execution remains attributable.

The first valuable release should be manual and auditable: objectives, work items, strategy revisions, task counts, and a deterministic resume briefing. Agent-generated proposals should be added only after those workflows are coherent without an LLM.

### 1.1 Resolved design defaults

| Question | Decision |
|---|---|
| Strategy ownership | Shared, versioned engagement artifact |
| Raw strategist conversation | Personal per analyst initially; accepted effects are shared |
| General analyst work | New `WorkItem` model |
| Tool execution work | Existing `Task` and Tactical path |
| Agent-created work | Open `Suggestion` until analyst acceptance |
| Strategist identity | New `engagement_strategist`, distinct from finding-oriented `strategic` |
| Initial trigger mode | Manual only |
| Background scheduler | Deferred pending explicit credential sponsorship and operational controls |
| Coverage | Separate deterministic model added after manual strategy/work foundations |
| Completion | Deterministic preflight plus explicit analyst approval |
| Archived engagements | Read-only |
| Mutation authorization | Authenticated non-guest analyst, enforced server-side and audited |

---

## 2. North-star workflow

```text
Define formal scope, exclusions, objectives, constraints, and timeframe
        ↓
Generate and review an initial engagement strategy
        ↓
Accept an initial set of work items
        ↓
Dispatch approved enumeration/scanning through Tactical
        ↓
Review resulting findings, observations, entities, and coverage
        ↓
Create and complete finding or cross-finding work items
        ↓
Share material finding outcomes with the engagement strategy
        ↓
Revise objectives, priorities, hypotheses, and future work
        ↓
Repeat until remaining work is completed, deferred, or accepted as a gap
        ↓
Resolve report-readiness blockers and approve engagement completion
```

Findings remain evidence-backed engagement results. Planned exploration is represented by work items and coverage records, not placeholder findings.

---

## 3. Goals and non-goals

### 3.1 Goals

1. Reduce analyst spin-up time when resuming an engagement.
2. Maintain one shared, current, versioned engagement strategy.
3. Make remaining work visible at engagement and finding level.
4. Preserve completed, rejected, deferred, and superseded work as a decision ledger.
5. Coordinate engagement-level and finding-level agents through visible, structured handoffs.
6. Link planned work to executions, findings, entities, evidence, objectives, and outcomes.
7. Provide deterministic coverage and completion checks.
8. Keep every agent-originated mutation behind explicit analyst confirmation.
9. Support multiple analysts without silent last-write-wins strategy changes.
10. Reuse existing suggestions, agent executions, Tactical dispatch, scope gates, approvals, audit logs, Status, SSE, and TanStack Query patterns.

### 3.2 Non-goals for the initial program

1. An unattended autonomous engagement manager.
2. Silent strategy changes or automatic acceptance of agent proposals.
3. Direct execution by the Engagement Strategist.
4. Automated exploitation or automated analyst validation work.
5. A graph database or generalized project-management platform.
6. Drag-and-drop scheduling before ordering and concurrency semantics are established.
7. Claiming complete coverage based solely on an LLM response.
8. Sharing every analyst's raw exploratory chat with every coworker.
9. Replacing the existing execution-oriented Status workspace.
10. Periodic background LLM calls without an explicit credential, cost, locking, and sponsorship design.

---

## 4. Existing platform foundations

| Existing primitive | Reuse in this program |
|---|---|
| `Engagement`, `ScopeItem`, `Finding`, `Observation`, `Entity` | Canonical strategist dossier |
| `Suggestion` | Human-reviewed agent proposals |
| `Task` | Tactical execution job, not general analyst work |
| `AgentExecution` | Agent trace, status, model, token, and cost telemetry |
| `Conversation` / `ConversationMessage` | Base for user-scoped engagement strategist conversations |
| `AuditLog` | Immutable decision and action ledger |
| Strategic structured-output pattern | Proposal generation and provider/model resolution |
| Tactical agent | Controlled execution boundary |
| MCP leases and tool packs | Least-privilege execution context |
| Scope matcher and approval gate | Deterministic execution enforcement |
| Redis streams and SSE | Live execution and later work-item invalidation |
| Finding activity service | Pattern for an engagement-wide material activity projection |
| Status view | Execution telemetry, retry, cancellation, and approvals |
| Finding chat action cards | Explicit accept/deny interaction pattern |
| TanStack Query and URL-persisted engagement views | Strategy workspace navigation and cache integration |

### 4.1 Important current limitation

The existing `Task` model is an orchestrator execution record. Its kinds are `scan`, `enum`, and `exploit`; its payload carries Tactical tool arguments; and its statuses describe dispatch and run state. It must not be stretched into the sole representation of manual analyst work.

---

## 5. Product vocabulary and boundaries

### 5.1 Strategy

The shared, current engagement direction: mission, objectives, hypotheses, priorities, constraints, coverage expectations, and exit criteria.

### 5.2 Objective

A stable, addressable unit of intended engagement outcome. Work items reference objective IDs even when strategy narrative changes.

### 5.3 Suggestion

An agent proposal awaiting an analyst decision. Suggestions do not count as committed work.

### 5.4 Work item

A shared unit of analyst or agent-assisted coordination. A work item may be engagement-wide, linked to one finding, or linked to multiple findings. It may launch zero or more execution Tasks.

### 5.5 Execution Task

The existing Tactical job representing a permitted enumeration or scan operation. Execution Tasks retain their existing dispatch, retry, cancellation, run, scope, and approval semantics.

### 5.6 Strategy signal

A structured, evidence-linked conclusion from a finding or completed work item that may affect engagement priorities, hypotheses, objectives, or future work.

### 5.7 Coverage item

A deterministic record that a target/activity category is planned, active, covered, blocked, deferred, accepted as a gap, or not applicable.

### 5.8 Checkpoint

A shared, durable summary of material engagement state at a point in time, used for resume briefings and change comparison.

---

## 6. Authority model

| Action | Analyst | Finding agent | Engagement Strategist | Tactical |
|---|---:|---:|---:|---:|
| Edit strategy directly | Yes | No | No | No |
| Propose strategy revision | Yes | Via signal | Yes | No |
| Accept strategy revision | Yes | No | No | No |
| Create manual work item | Yes | No | No | No |
| Propose work item | Yes | Yes | Yes | No |
| Accept/reject proposal | Yes | No | No | No |
| Produce draft reasoning outcome | Yes | Yes | Yes | No |
| Accept agent-authored outcome | Yes | No | No | No |
| Validate/reject/exclude finding | Yes | No | No | No |
| Expand Found Scope | Yes | No | No | No |
| Dispatch permitted execution Task | Via accepted action | No | No | Yes |
| Approve active operation | Yes | No | No | No |
| Declare engagement complete | Yes | No | May recommend | No |

All backend mutations use server-side authorization. Client-side visibility is not an authorization boundary.

---

## 7. Shared versus personal state

The following records are shared across analysts:

- current and historical strategy revisions;
- objectives;
- work items and outcomes;
- strategy signals;
- accepted/dismissed suggestions;
- coverage items and accepted gaps;
- checkpoints;
- execution links; and
- material activity and decisions.

Raw strategist conversation is user-scoped initially, consistent with existing finding conversations and per-user ephemeral provider keys. Accepted conversation actions create shared records with author attribution. Private drafts may be added later without changing the shared source of truth.

---

## 8. State machines

### 8.1 Suggestion lifecycle

```text
open → accepted
     → dismissed
```

Agent-proposed work remains a Suggestion until accepted. It does not appear in the committed remaining-work count.

### 8.2 Work-item lifecycle

```text
ready → in_progress → completed
   │          │
   │          ├→ blocked → in_progress
   │          ├→ deferred → ready
   │          └→ cancelled
   ├→ deferred
   └→ cancelled
```

Recommended states:

- `ready`
- `in_progress`
- `blocked`
- `completed`
- `deferred`
- `cancelled`

Recommended resolution outcomes:

- `completed`
- `disproved`
- `not_applicable`
- `duplicate`
- `superseded`
- `unable_to_complete`

Counts:

- **Remaining:** ready + in progress + blocked
- **Needs decision:** open work-item suggestions
- **Deferred:** visible separately and reviewed during closure
- **Terminal:** completed + cancelled
- **Overdue:** derived when a non-terminal item's `due_at` is earlier than the current UTC time; overdue is not a separate lifecycle state

### 8.3 Objective lifecycle

- `planned`
- `active`
- `blocked`
- `completed`
- `deferred`
- `cancelled`

### 8.4 Strategy revision lifecycle

- `draft`
- `proposed`
- `current`
- `rejected`
- `superseded`

A new current revision supersedes the former current revision. Agent-created revisions always begin as proposed. Acceptance uses an optimistic `based_on_revision_id` check.

### 8.5 Strategy signal lifecycle

- `open`
- `incorporated`
- `dismissed`
- `superseded`

### 8.6 Coverage lifecycle

- `not_started`
- `planned`
- `active`
- `covered`
- `blocked`
- `deferred`
- `accepted_gap`
- `not_applicable`

---

## 9. Proposed data model

Exact Alembic revision numbers must be assigned only after the current migration-bearing PR stack merges. Maintain one linear head.

### 9.1 `engagement_strategy_revisions`

```text
id UUID PK
engagement_id UUID FK engagements NOT NULL
version INTEGER NOT NULL
state strategy_revision_state NOT NULL
based_on_revision_id UUID nullable FK self
summary VARCHAR(300) nullable
body TEXT NOT NULL
structured JSONB NOT NULL default {}
created_by_user_id UUID nullable FK users
proposed_by_execution_id UUID nullable FK agent_executions
proposal_reason TEXT nullable
decided_by_user_id UUID nullable FK users
decided_at timestamptz nullable
created_at timestamptz NOT NULL
updated_at timestamptz NOT NULL
UNIQUE (engagement_id, version)
partial UNIQUE (engagement_id) WHERE state = 'current'
```

`structured` may contain hypotheses, constraints, priorities, and exit-criteria presentation data, but stable objectives are normalized separately.

### 9.2 `engagement_objectives`

```text
id UUID PK
engagement_id UUID FK engagements NOT NULL
title VARCHAR(300) NOT NULL
description TEXT nullable
success_criteria TEXT nullable
status objective_status NOT NULL
priority objective_priority NOT NULL
display_order INTEGER NOT NULL default 0
owner_user_id UUID nullable FK users
target_date date nullable
created_by_user_id UUID nullable FK users
completed_by_user_id UUID nullable FK users
completed_at timestamptz nullable
row_version INTEGER NOT NULL default 1
created_at timestamptz NOT NULL
updated_at timestamptz NOT NULL
```

Initial priorities: `critical`, `high`, `medium`, `low`.

### 9.3 `work_items`

```text
id UUID PK
engagement_id UUID FK engagements NOT NULL
objective_id UUID nullable FK engagement_objectives
parent_work_item_id UUID nullable FK self
title VARCHAR(300) NOT NULL
description TEXT nullable
rationale TEXT nullable
acceptance_criteria JSONB NOT NULL default []
status work_item_status NOT NULL
priority work_item_priority NOT NULL
executor_type work_item_executor NOT NULL
assigned_user_id UUID nullable FK users
created_by_user_id UUID nullable FK users
created_by_execution_id UUID nullable FK agent_executions
started_at timestamptz nullable
blocked_reason TEXT nullable
due_at timestamptz nullable
resolution_outcome work_item_resolution nullable
resolution_note TEXT nullable
completed_by_user_id UUID nullable FK users
completed_at timestamptz nullable
row_version INTEGER NOT NULL default 1
created_at timestamptz NOT NULL
updated_at timestamptz NOT NULL
```

Executor values:

- `analyst`
- `finding_agent`
- `engagement_strategist`
- `tactical`
- `unassigned`

Agent-authored work results are stored as immutable proposed result revisions described below. A mutable JSON field on WorkItem is intentionally not used as authoritative agent output.

### 9.4 `work_item_results`

```text
id UUID PK
work_item_id UUID FK work_items ON DELETE CASCADE
revision INTEGER NOT NULL
state work_item_result_state NOT NULL
summary TEXT NOT NULL
structured JSONB NOT NULL default {}
evidence_refs JSONB NOT NULL default []
proposed_by_user_id UUID nullable FK users
proposed_by_execution_id UUID nullable FK agent_executions
decided_by_user_id UUID nullable FK users
decided_at timestamptz nullable
created_at timestamptz NOT NULL
UNIQUE (work_item_id, revision)
partial UNIQUE (work_item_id) WHERE state = 'accepted'
```

States:

- `proposed`
- `accepted`
- `rejected`
- `superseded`

Finding agents and the Engagement Strategist can create proposed results. Only an analyst can accept or reject them. Accepting a newer result transactionally supersedes the former accepted result while retaining history. Result acceptance does not silently complete the work item or change strategy.

The accept request explicitly controls optional follow-up effects:

- `resolve_work_item`: apply a typed resolution outcome and note in the same transaction; and
- `share_with_strategy`: create a new open StrategySignal referencing the accepted result.

Both default to false. Every effect is included in the response and audit payload.

### 9.5 `work_item_findings`

```text
work_item_id UUID FK work_items ON DELETE CASCADE
finding_id UUID FK findings ON DELETE RESTRICT
relationship work_item_finding_relationship NOT NULL
created_at timestamptz NOT NULL
PRIMARY KEY (work_item_id, finding_id, relationship)
```

Relationships:

- `primary`
- `related`
- `produced_by`
- `blocks`

Finding merge behavior transfers links to the surviving finding and preserves an audit record. Soft deletion never silently destroys work history.

### 9.6 Existing `tasks` extension

```text
work_item_id UUID nullable FK work_items ON DELETE SET NULL, indexed
```

One work item may therefore link to multiple execution Tasks. Existing `finding_id`, task kind, payload, and Tactical behavior remain authoritative for execution.

### 9.7 Existing `suggestions` extension

Extend `SuggestionKind` with:

- `work_item`
- `strategy_revision`

Add:

```text
proposal_key VARCHAR(200) nullable
context_hash VARCHAR(64) nullable
objective_id UUID nullable FK engagement_objectives
work_item_id UUID nullable FK work_items, indexed
```

Recommended unique suppression is implemented in service logic and supported by an index over engagement, status, kind, and proposal key.

Work-item suggestions use a versioned, typed payload:

```json
{
  "schema_version": 1,
  "work_item": {
    "title": "Compare authentication behavior",
    "description": "...",
    "rationale": "...",
    "objective_id": "uuid-or-null",
    "priority": "high",
    "executor_type": "analyst",
    "acceptance_criteria": ["..."],
    "finding_links": [
      {"finding_id": "uuid", "relationship": "primary"},
      {"finding_id": "uuid", "relationship": "related"}
    ]
  }
}
```

A proposal may link at most 50 unique findings. Acceptance validates every objective and finding against the Suggestion's engagement before writing anything, rejects duplicate/invalid relationships atomically, creates the WorkItem and all `work_item_findings` rows in one transaction, and returns the complete created projection. `Suggestion.finding_id` may mirror the primary link for indexing/backward-compatible presentation, but the typed payload is authoritative for multi-finding proposals.

Acceptance routing:

- `task` → existing execution Task and Tactical path;
- `work_item` → create WorkItem and typed finding links, never auto-dispatch;
- `strategy_revision` → validate and activate a proposed revision;
- `note` → acknowledge informational guidance.

### 9.8 `strategy_signals`

```text
id UUID PK
engagement_id UUID FK engagements NOT NULL
source_finding_id UUID nullable FK findings
source_work_item_id UUID nullable FK work_items
source_work_item_result_id UUID nullable FK work_item_results
source_execution_id UUID nullable FK agent_executions
signal_type VARCHAR(80) NOT NULL
summary TEXT NOT NULL
confidence VARCHAR(20) NOT NULL
evidence_refs JSONB NOT NULL default []
suggested_effect JSONB NOT NULL default {}
dedup_key VARCHAR(200) NOT NULL
status strategy_signal_status NOT NULL
decided_by_user_id UUID nullable FK users
decided_at timestamptz nullable
created_at timestamptz NOT NULL
updated_at timestamptz NOT NULL
```

When `source_work_item_result_id` is present, the referenced result and optional source WorkItem must belong to the same engagement. A uniqueness constraint/service invariant permits at most one active signal of a given type from the same immutable result, preventing acceptance retries from sharing it twice.

### 9.9 Conversation generalization

Add a context enum:

- `finding`
- `engagement`

Make `Conversation.finding_id` nullable and add `context_type`. Backfill existing rows as `finding`. Enforce context consistency with database checks/service validation. `engagement_id` and `created_by_user_id` remain the engagement-thread identity for personal strategist conversations. Conversation messages and inert action payloads are reused.

### 9.10 `coverage_items` (later program slice)

```text
id UUID PK
engagement_id UUID FK engagements NOT NULL
objective_id UUID nullable FK engagement_objectives
scope_item_id UUID nullable FK scope_items
target_kind VARCHAR(40) NOT NULL
target_key VARCHAR(500) NOT NULL
activity_category coverage_category NOT NULL
status coverage_status NOT NULL
supporting_refs JSONB NOT NULL default []
reason TEXT nullable
accepted_by_user_id UUID nullable FK users
accepted_at timestamptz nullable
row_version INTEGER NOT NULL default 1
created_at timestamptz NOT NULL
updated_at timestamptz NOT NULL
UNIQUE (engagement_id, target_kind, target_key, activity_category)
```

Initial categories:

- `scope_review`
- `asset_discovery`
- `service_identification`
- `scanner_coverage`
- `finding_review`
- `evidence_collection`
- `reporting`

### 9.11 `engagement_checkpoints`

```text
id UUID PK
engagement_id UUID FK engagements NOT NULL
strategy_revision_id UUID nullable FK engagement_strategy_revisions
created_by_user_id UUID nullable FK users
created_by_execution_id UUID nullable FK agent_executions
material_event_cursor timestamptz NOT NULL
facts JSONB NOT NULL
narrative TEXT nullable
created_at timestamptz NOT NULL
```

Facts are deterministic. Narrative is optional LLM presentation.

### 9.12 Engagement work state and `engagement_completion_decisions`

Add an engagement work-state column independent of archive/flush status:

```text
engagements.work_state engagement_work_state NOT NULL default 'active'
engagements.work_state_version INTEGER NOT NULL default 1
```

States:

- `active`
- `completion_review`
- `completed`

Existing and new engagements use `active`; initial strategy generation is an active-engagement workflow rather than a separate lifecycle state. `completed` locks new strategy/work/execution mutations until an analyst reopens the engagement; read, report, export, and archive remain available. Archive controls visibility/retention and is not synonymous with work completion.

Completion and reopening decisions are immutable:

```text
id UUID PK
engagement_id UUID FK engagements NOT NULL
action engagement_completion_action NOT NULL
from_work_state engagement_work_state NOT NULL
to_work_state engagement_work_state NOT NULL
readiness_hash VARCHAR(64) nullable
readiness_snapshot JSONB nullable
accepted_exceptions JSONB NOT NULL default []
strategy_revision_id UUID nullable FK engagement_strategy_revisions
prior_completion_decision_id UUID nullable FK self
reason TEXT nullable
idempotency_key VARCHAR(100) NOT NULL
decided_by_user_id UUID FK users NOT NULL
created_at timestamptz NOT NULL
UNIQUE (engagement_id, idempotency_key)
```

Actions are `review_started`, `approved`, and `reopened`. Review and approval decisions require a readiness hash and snapshot. Reopen decisions require `prior_completion_decision_id` referencing the latest approval plus a non-empty reason; their readiness fields are null. Accepted exceptions contain typed references to coverage gaps, deferred work, or other explicitly waivable readiness checks plus analyst rationale. Hard governance blockers such as active runs, pending approvals, or a stale readiness hash cannot be waived.

---

## 10. API proposal

All reads require authenticated access. Mutations use `CurrentNonGuestUser`, reject flushed engagements, and treat archived engagements as read-only.

### 10.1 Strategy revisions

```text
GET  /engagements/{slug}/strategy
GET  /engagements/{slug}/strategy/revisions
POST /engagements/{slug}/strategy/revisions
POST /engagements/{slug}/strategy/revisions/{id}/accept
POST /engagements/{slug}/strategy/revisions/{id}/reject
POST /engagements/{slug}/strategy/revisions/{id}/restore
```

Mutation bodies include `based_on_revision_id`. Stale acceptance returns `409` with the current revision ID.

### 10.2 Objectives

```text
GET    /engagements/{slug}/objectives
POST   /engagements/{slug}/objectives
PATCH  /engagements/{slug}/objectives/{id}
POST   /engagements/{slug}/objectives/{id}/complete
POST   /engagements/{slug}/objectives/{id}/reopen
DELETE /engagements/{slug}/objectives/{id}   # terminal cancel/archive semantics
```

### 10.3 Work items

```text
GET   /engagements/{slug}/work-items
POST  /engagements/{slug}/work-items
GET   /work-items/{id}
PATCH /work-items/{id}
POST  /work-items/{id}/start
POST  /work-items/{id}/block
POST  /work-items/{id}/defer
POST  /work-items/{id}/resolve
POST  /work-items/{id}/reopen
POST  /work-items/{id}/cancel
POST  /work-items/{id}/findings
DELETE /work-items/{id}/findings/{finding_id}
GET   /work-items/{id}/results
POST  /work-items/{id}/results
POST  /work-item-results/{result_id}/accept
POST  /work-item-results/{result_id}/reject
POST  /work-items/{id}/execution-suggestions
```

List filters:

```text
status, priority, executor_type, assigned_user_id, objective_id,
finding_id, needs_decision, q, limit, cursor
```

Resolution uses an explicit typed body rather than a generic patch:

```json
{
  "outcome": "completed",
  "note": "Confirmed the secondary hostname is unaffected.",
  "evidence_refs": [
    {"type": "observation", "id": "..."}
  ]
}
```

Agent result creation always produces a `proposed` WorkItemResult. Result acceptance requires an expected work-item row version and accepts explicit effects:

```json
{
  "expected_work_item_version": 4,
  "resolve_work_item": false,
  "resolution_outcome": null,
  "resolution_note": null,
  "share_with_strategy": true
}
```

Acceptance/rejection is semantically idempotent by result ID and terminal state: retrying acceptance returns the existing accepted result and previously created effects without resolving or sharing twice. It preserves rejected/superseded revisions, attributes the deciding analyst, and returns the WorkItem, accepted result, optional StrategySignal, and updated rollup.

`POST /work-items/{id}/execution-suggestions` creates an inert, open `SuggestionKind.task`; it never calls Tactical or creates an execution Task. Request:

```json
{
  "tool": "service_detect",
  "target": "api.example.com",
  "task_kind": "scan",
  "title": "Identify exposed services",
  "expected_work_item_version": 4,
  "idempotency_key": "client-generated-key"
}
```

The service verifies engagement ownership, tool/kind compatibility, expected WorkItem version, and current scope before creating the Suggestion; it stores the route's WorkItem in normalized `Suggestion.work_item_id` and the expected version in the versioned Suggestion payload. Accepting that Suggestion through the existing audited suggestion-accept endpoint atomically revalidates that the linked WorkItem belongs to the engagement, is not terminal, and still has the expected row version; it then rechecks current scope, creates the execution Task linked by `work_item_id`, invokes Tactical when eligible, and retains the separate active-tool Approval layer. Foreign, completed, cancelled, or stale WorkItem links are rejected without partial writes. Repeated idempotency keys return the existing Suggestion.

### 10.4 Finding task rollup

Either extend the findings list projection or expose a batched endpoint:

```text
GET /engagements/{slug}/work-item-rollup
```

```json
{
  "by_finding": {
    "finding-uuid": {
      "remaining": 3,
      "blocked": 1,
      "proposals": 2,
      "deferred": 1
    }
  },
  "engagement": {
    "remaining": 17,
    "blocked": 4,
    "proposals": 6,
    "deferred": 3
  }
}
```

Counts are computed server-side from explicit status rules.

### 10.5 Strategy signals

```text
GET  /engagements/{slug}/strategy/signals
POST /findings/{id}/strategy-signals
POST /work-items/{id}/strategy-signals
POST /strategy-signals/{id}/incorporate
POST /strategy-signals/{id}/dismiss
```

### 10.6 Resume and checkpoints

```text
GET  /engagements/{slug}/resume
POST /engagements/{slug}/checkpoints
GET  /engagements/{slug}/checkpoints
```

`GET /resume` is deterministic and should not require a provider key.

### 10.7 Engagement strategist conversation

```text
GET    /engagements/{slug}/strategy/chat
POST   /engagements/{slug}/strategy/chat
POST   /engagements/{slug}/strategy/chat/messages/{message_id}/actions/accept
POST   /engagements/{slug}/strategy/chat/messages/{message_id}/actions/deny
POST   /engagements/{slug}/strategy/chat/summarize
DELETE /engagements/{slug}/strategy/chat
```

Closing or clearing presentation never accepts, rejects, or dispatches an action.

### 10.8 Strategy generation

```text
POST /engagements/{slug}/strategy/generate-initial
POST /engagements/{slug}/strategy/recommend
POST /engagements/{slug}/strategy/reassess
POST /engagements/{slug}/strategy/review-completion
```

These endpoints create `AgentExecution` records using the acting analyst's provider configuration and return proposals. They do not directly mutate current strategy or execute tools.

### 10.9 Engagement completion

```text
GET  /engagements/{slug}/completion/readiness
POST /engagements/{slug}/completion/review
POST /engagements/{slug}/completion/approve
POST /engagements/{slug}/completion/reopen
GET  /engagements/{slug}/completion/decisions
```

Readiness is deterministic and returns typed checks:

```json
{
  "work_state": "active",
  "ready": false,
  "readiness_hash": "sha256",
  "checks": [
    {
      "key": "remaining_work",
      "severity": "blocker",
      "count": 3,
      "waivable": false,
      "refs": [{"type": "work_item", "id": "..."}],
      "message": "3 committed work items remain"
    }
  ],
  "accepted_gap_candidates": []
}
```

`completion/review` changes `active` to `completion_review` using the expected work-state version and records a `review_started` decision with the readiness snapshot. `completion/approve` requires the current readiness hash, expected work-state version, idempotency key, and typed exception rationales. It rejects stale hashes, unwaivable blockers, foreign references, or exceptions that are not present in the current preflight. Approval sets work state to `completed` and records the immutable snapshot and exceptions.

Completion does not archive the engagement. A completed engagement remains readable and reportable but rejects new work, strategy, and execution mutations. `completion/reopen` requires a reason, expected version, idempotency key, and the latest approved completion-decision ID:

```json
{
  "prior_completion_decision_id": "uuid",
  "expected_work_state_version": 8,
  "reason": "New formal-scope target added by client request",
  "idempotency_key": "client-generated-key"
}
```

It records a `reopened` decision with null readiness fields and returns the engagement to `active`. Reopen does not delete the prior completion record or accepted gaps.

Report readiness remains a component of completion readiness rather than being replaced by it.

---

## 11. Engagement dossier and context compiler

The strategist must rebuild context from canonical records rather than rely on conversation memory.

### 11.1 Deterministic dossier sections

1. Engagement metadata, timeframe, description, and status.
2. Current strategy revision and active objectives.
3. Formal, Found, and excluded scope summaries.
4. Remaining, blocked, deferred, and recently completed work items.
5. Open, accepted, and recently dismissed suggestions.
6. Finding counts by status, severity, phase, and exclusion.
7. Selected high-priority or recently changed findings with record IDs.
8. Entity summary and relevant provenance.
9. Recent observations.
10. Active and recent execution Tasks/runs.
11. Pending approvals and current grants where safe to disclose.
12. Deterministic report-readiness checks.
13. Open strategy signals.
14. Material audit events since the last checkpoint.
15. Previously rejected/deferred proposals relevant to deduplication.
16. Coverage summary after coverage is introduced.

### 11.2 Bounded context rules

- Never include secrets or provider keys.
- Never include raw attachment bytes.
- Avoid entire scanner exports and unbounded raw tool output.
- Bound each record category and preserve IDs for follow-up reads.
- Prefer deterministic summaries over arbitrary truncation.
- Include the strategy revision ID and dossier generation timestamp.
- Record a context hash on `AgentExecution.input` and generated suggestions.
- Treat every finding, observation, import field, entity property, and tool result as untrusted data rather than prompt instructions.
- Delimit untrusted records explicitly in prompts.
- Use strict structured output and server-side validation for all proposed identifiers and action types.

### 11.3 Resume response

A deterministic response should include:

```json
{
  "current_focus": {},
  "since_checkpoint": {},
  "active_work": [],
  "blocked_work": [],
  "decisions_required": [],
  "recommended_starting_records": [],
  "coverage_summary": {},
  "report_readiness": {},
  "generated_at": "UTC timestamp"
}
```

An optional narrative may be generated separately so the resume workspace still functions without a provider credential.

---

## 12. Engagement Strategist contract

Add a distinct `AgentName.engagement_strategist`. Do not silently broaden or rename the existing finding-oriented `strategic` role.

### 12.1 Structured output

```json
{
  "situation_summary": "...",
  "facts": [
    {"statement": "...", "refs": [{"type": "finding", "id": "..."}]}
  ],
  "inferences": [
    {"statement": "...", "confidence": "medium", "refs": []}
  ],
  "hypotheses": [
    {"statement": "...", "confidence": "low", "validation_needed": "..."}
  ],
  "work_item_proposals": [],
  "strategy_revision_proposal": null,
  "coverage_gaps": [],
  "warnings": []
}
```

Facts, inferences, hypotheses, and recommendations are separate. Factual claims reference canonical records wherever possible.

### 12.2 Proposal controls

- Maximum five new proposals per run by default.
- Stable proposal keys.
- Do not recreate equivalent open proposals.
- Do not reopen completed work.
- Do not resuggest dismissed work unless the relevant context hash materially changes.
- Respect a `do_not_suggest_again` decision.
- Apply per-engagement cooldown and cost limits.
- Never accept the agent's own proposals.
- Never call Tactical directly.

### 12.3 Trigger policy

Initial release:

- analyst requests initial strategy;
- analyst requests a briefing or recommendation;
- analyst asks for reassessment;
- analyst shares a finding/work outcome; or
- analyst asks for completion review.

Later event-assisted release:

- scanner import committed;
- material scope change;
- high/critical finding created or validated;
- objective completed;
- work item resolved with strategy impact; or
- run completed with material findings.

Event-assisted runs require idempotency keys, per-engagement locking, cooldowns, and loop suppression.

Periodic unattended ticks are explicitly deferred until credential sponsorship, quiet hours, cost budgets, locking, retry/backoff, and notification policies are approved.

---

## 13. Finding-agent interaction

Agents do not exchange hidden free-form messages. They coordinate through visible work items, requests, results, and strategy signals.

### 13.1 Engagement Strategist to finding

A strategist creates a work-item suggestion linked to one or more findings:

```text
Determine whether the authentication behavior is gateway-wide.

Rationale:
A gateway-wide issue would change Objective 2's affected-target assessment.

Acceptance criteria:
- Compare primary and secondary hosts.
- Reference supporting evidence.
- State whether affected targets should change.
```

### 13.2 Finding agent to strategist

A finding-agent result may contain:

```json
{
  "conclusion": "Likely shared gateway policy",
  "confidence": "medium",
  "evidence_refs": [],
  "related_finding_ids": [],
  "related_entity_ids": [],
  "suggested_strategy_effect": {},
  "suggested_follow_up": {}
}
```

The analyst may accept the outcome, share it with Strategy, create cross-finding work, propose a strategy revision, or record no strategy impact.

### 13.3 Strategy version visibility

Finding-agent responses influenced by strategy record the revision ID. When the current strategy changes, the finding workbench displays that the prior assessment used an older version and offers reassessment without invalidating historical reasoning.

---

## 14. User experience

### 14.1 Engagement navigation

Add `Strategy` immediately after Findings:

```text
Findings
Strategy
Entities
Observations
Status
Report
Costs
Scope
...
```

Status remains execution telemetry. Strategy is the planning, decision, and resume workspace.

### 14.2 Strategy workspace

Recommended sections:

1. **Resume engagement** — current focus, changes, active work, blockers, decisions, and recommended starting records.
2. **Current strategy** — current version, edit, propose revision, history, diff, and restore.
3. **Objectives** — status, priority, success criteria, and linked work.
4. **Work queue** — remaining, blocked, deferred, completed, and assignment filters.
5. **Needs decision** — work proposals, strategy revisions, signals, scope decisions, and approvals.
6. **Recent activity** — material ledger projection.
7. **Coverage** — added in the coverage slice.
8. **Strategist** — personal conversation and proposal actions.

Start with deterministic sorting and responsive lists/tables. Defer drag-and-drop.

URL state follows the existing statically exportable engagement route:

```text
/e?slug={slug}&view=strategy
/e?slug={slug}&view=strategy&objective={objective_id}
/e?slug={slug}&view=strategy&workItem={work_item_id}
/e/findings/{finding_id}?tab=tasks&returnTo={safe_relative_engagement_url}
```

`returnTo` accepts only safe same-origin relative engagement paths. Strategy navigation participates in the existing hover-prefetch switch. Empty, loading, error, and archived/read-only states are designed explicitly; the workspace does not render enabled mutation controls solely because the frontend currently assumes `canWrite=true`.

### 14.3 Finding list

Each finding row/card shows:

```text
3 tasks remaining · 1 blocked · 2 proposals
```

Filters:

- has remaining work;
- has blocked work;
- has proposals;
- no remaining work;
- assigned to me;
- executor; and
- sort by remaining count.

Clicking the badge opens a compact, keyboard-operable popover. The main Findings list may expose only constrained lifecycle shortcuts: start a ready item, mark an active item blocked, or resolve an item through an explicit outcome/confirmation form. Creating, editing, linking, agent delegation, and evidence management remain in the full finding workbench. The compact side preview blade remains read-only and links to the full Tasks tab.

Quick mutations require server authorization and an expected row version. Resolution preserves the selected outcome and note on failure, keeps the popover open with an inline error, and returns focus to the invoking task row after success. Cache updates refresh the finding rollup, Strategy queue, and individual item together. Status is always communicated with text as well as color.

### 14.4 Finding workbench

Add a Tasks tab with a count badge. Keep Tools for executable AI actions and execution history. Tasks contains:

- strategy relevance and objective;
- strategist requests;
- analyst work;
- agent-assisted work;
- acceptance criteria;
- parent/child decomposition;
- proposed, accepted, rejected, and superseded result revisions;
- evidence references;
- strategy signals; and
- links to execution Status.

Persist the workbench tab in the URL so Strategy can deep-link to a finding's Tasks context. The compact side preview blade shows counts and a launcher only. The main Findings list is limited to the three governed lifecycle shortcuts defined above; all other editing remains in the workbench. The existing Tools tab remains dedicated to executable AI actions and run history.

### 14.5 Manual resolution

Quick resolution presents an explicit outcome and optional/required note depending on task type. Completing all finding work does not validate the finding automatically.

### 14.6 Checkpoints

An analyst can create a checkpoint at the end of a session. The system stores deterministic facts and optionally drafts a narrative. The next resume briefing compares current material state to the checkpoint cursor.

---

## 15. Cache, query, and realtime design

### 15.1 Query keys

```text
strategy(slug)
strategyRevisions(slug)
objectives(slug, filters)
workItems(slug, filters)
findingWorkItems(findingId)
workItem(id)
workItemResults(id)
workItemRollup(slug)
strategySignals(slug, filters)
resume(slug)
checkpoints(slug)
strategyConversation(slug, user)
coverage(slug, filters)
```

Before this program, centralize the existing execution-task fetch into `qk.tasks(slug, filters)` and `useTasks`; the current finding workbench should not maintain independent polling state.

### 15.2 Mutation invalidation

- Work-item mutation: work items, individual work-item cache, finding work items, rollup, objectives where relevant, resume, and report/coverage only when affected.
- Strategy revision: strategy, revisions, objectives if linked, resume, and strategy-aware finding panels.
- Signal decision: signals, strategy, relevant finding activity, and resume.
- Execution Task link: work item, Status, execution tasks, and relevant finding.
- Finding merge/delete: work-item links, rollup, strategy references, and activity.

### 15.3 Realtime

Initial release may use mutation updates, refocus invalidation, and modest polling. Poll execution Tasks only while execution-linked rows are active. Extend the typed Status projection with `finding_id`, `work_item_id`, and `task_id` (or a typed links object) so Strategy never parses generic log JSON for navigation. Later add:

```text
work_item.created
work_item.updated
work_item.resolved
work_item.cancelled
strategy.revision_current
strategy_signal.created
objective.updated
checkpoint.created
```

SSE events update/invalidate scoped TanStack Query keys. Closing UI never mutates server state.

---

## 16. Audit and material activity vocabulary

```text
strategy.created
strategy.revision_proposed
strategy.revision_accepted
strategy.revision_rejected
strategy.revision_restored
objective.created
objective.updated
objective.completed
objective.reopened
work_item.created
work_item.started
work_item.blocked
work_item.deferred
work_item.resolved
work_item.reopened
work_item.cancelled
work_item.finding_linked
work_item.execution_linked
work_item.agent_result_proposed
work_item.agent_result_accepted
work_item.agent_result_rejected
work_item.agent_result_superseded
work_item.execution_suggestion_created
finding.strategy_signal_shared
strategy_signal.incorporated
strategy_signal.dismissed
engagement.checkpoint_created
engagement.completion_review_started
engagement.completion_approved
engagement.completion_reopened
```

Audit payloads include IDs, counts, prior/new state, actor, revision/context identifiers, and bounded evidence references. They do not include secrets, raw uploads, or unbounded chat content.

The engagement activity projection shows material events rather than every pure scope evaluation or read.

---

## 17. Credential, cost, and scheduler policy

Current agent credentials are per-user and ephemeral. Therefore:

1. Manual strategist runs use the acting analyst's current credential.
2. Deterministic resume and work dashboards require no LLM credential.
3. Event-assisted runs initially require a valid sponsoring analyst context and fail safely when unavailable.
4. The engagement creator's credential is never used implicitly.
5. Unattended scheduling requires a separate approved design:
   - explicit platform/service credential or sponsor grant;
   - visible sponsor and revocation;
   - daily engagement/user cost budget;
   - concurrency lock;
   - cooldown and exponential backoff;
   - quiet hours and notification batching;
   - maximum proposals per run; and
   - clear audit attribution.

Every strategist call writes `AgentExecution` with context hash, strategy revision, trigger, model, tokens, cost, status, and error. Add an explicit Engagement Strategist role to the existing per-engagement agent-model configuration and preserve the current provider/model fallback chain; do not silently reuse the Finding Strategist model selection.

---

## 18. Security and governance requirements

1. Agent output is never an authorization decision.
2. Every proposed executable target is rechecked by the canonical scope matcher at acceptance and execution.
3. Tactical remains the execution boundary and refuses analyst-only work.
4. Active tools retain the existing approval gate.
5. Accepting a work proposal is distinct from approving an active tool operation.
6. Scope grants are visible when proposed execution may use them.
7. Imported and tool-produced content is treated as untrusted prompt data.
8. Structured output is strictly validated and allow-listed.
9. Cross-engagement objective, finding, work-item, evidence, and execution IDs are rejected atomically.
10. Archived engagements are read-only; flushed engagements are inaccessible.
11. Strategy edits use optimistic concurrency.
12. Multi-event triggers use idempotency and per-engagement locking.
13. The strategist cannot accept its own suggestions or mark its own result authoritative.
14. Finding validation, exclusions, and Found Scope changes remain explicit analyst actions.
15. Completion requires deterministic checks and analyst approval.

---

## 19. Engagement lifecycle integration

Every new engagement-scoped table and relationship must be included in:

- `flush_engagement` database function;
- JSON/internal archive export;
- engagement archive/read-only handling;
- backup/import behavior where supported;
- audit and contribution projections;
- finding merge and soft-delete semantics;
- entity/scope provenance where linked;
- report-readiness invalidation where relevant; and
- model/schema registration.

Foreign-key deletion must preserve audit and decision history. Work items should normally reach a terminal state rather than be hard-deleted.

---

## 20. Performance and indexing

Required indexes should include:

- work items by engagement/status/priority/updated time;
- work-item links by finding and work item;
- work-item results by work item/state/revision;
- objectives by engagement/status/order;
- strategy revisions by engagement/version/state;
- signals by engagement/status/dedup key;
- coverage by engagement/status/category/target key;
- Suggestions by engagement/status/kind/proposal key;
- execution Tasks by work item;
- checkpoints by engagement/created time; and
- completion decisions by engagement/created time and idempotency key.

Use cursor pagination for large work, activity, revision, signal, and coverage lists. Rollups are computed in grouped SQL queries, not one query per finding. Dossier assembly must avoid N+1 reads and unbounded JSONB hydration.

---

## 21. Concurrency and idempotency

- Mutable work items and objectives carry `row_version`; clients submit the expected version.
- Stale updates return `409` with the current representation.
- Strategy revision acceptance validates `based_on_revision_id` under a row/advisory lock.
- Proposal keys suppress equivalent open proposals.
- Event-assisted strategy runs acquire a per-engagement lock and record an idempotency key.
- Work-item resolution is idempotent for the same terminal outcome/request key.
- Work-item result acceptance locks the parent item, enforces one accepted result, and is semantically idempotent by result ID/state; retries return the existing effects.
- Completion approval locks the engagement work state and validates the current readiness hash/version.
- Execution-task creation uses a unique action/proposal key so retries do not dispatch twice.
- Finding merge relinking is transactional.

---

## 22. Testing strategy

### 22.1 Backend model/API tests

- Objective and work-item lifecycle transitions.
- Invalid transitions and stale row versions.
- Manual creation and explicit resolution outcomes.
- Proposed/accepted/rejected/superseded WorkItemResult history and optional accepted effects.
- Retried result acceptance cannot resolve twice or create a duplicate StrategySignal.
- Server-computed finding/engagement rollups.
- Cross-engagement references rejected atomically.
- Suggestion acceptance routes to WorkItem versus execution Task correctly.
- Multi-finding work suggestions validate and create all links atomically.
- Work-item execution-suggestion endpoint remains inert; only suggestion acceptance may reach Tactical.
- Strategy revision acceptance, rejection, restore, and stale conflict.
- Work-item/finding links survive merge and soft deletion according to policy.
- Archived/flushed engagement behavior.
- Every mutation writes expected audit attribution.
- Completion preflight, waivable versus hard blockers, stale hash/version, approve, idempotent retry, and reopen history.
- Completion remains distinct from archive and completed-state mutations are rejected until reopen.
- Flush/export includes every new table.

### 22.2 Agent and context tests

- Dossier bounds and deterministic ordering.
- No secrets or raw attachment bytes in context.
- Prompt-injected finding/entity/import fields are treated as data.
- Structured output rejects unknown action types and foreign IDs.
- Stable proposal deduplication.
- Rejected/completed work is not repeatedly suggested.
- Maximum proposal and cost limits.
- Strategy version/context hash recorded on AgentExecution.
- Missing/expired provider credential fails without mutation.
- Agent cannot accept its own proposal or dispatch Tactical directly.

### 22.3 Execution governance tests

- Accepted WorkItem suggestion does not dispatch.
- Accepted execution suggestion uses existing Tactical path.
- Analyst-only work never dispatches.
- Scope is rechecked after proposal and before execution.
- Active execution still requires Approval unless a valid explicit grant applies.
- Duplicate action acceptance cannot execute twice.

### 22.4 Frontend tests

- Resume facts render without an LLM credential.
- Work counts distinguish remaining, blocked, deferred, and proposals.
- Quick resolution requires the appropriate outcome/note.
- Completing work does not validate a finding.
- Strategy proposal accept/reject remains reachable after navigation.
- Finding task tab and Strategy deep links preserve engagement context.
- Shared cache updates after work mutation.
- Archived engagement controls are read-only.
- Keyboard/screen-reader operation for task menus, filters, dialogs, and action cards, including labeled controls, focus restoration after dialogs/mutations, non-color status text, and live mutation feedback.
- Stale edit conflicts preserve unsaved analyst text.

### 22.5 End-to-end scenarios

1. Define scope, generate an initial strategy, edit it, and accept it.
2. Accept work-item proposals without executing anything.
3. Convert approved execution work through Tactical and Approval.
4. Review resulting findings and create finding tasks.
5. Ask a finding agent to draft a result, then accept and share it with Strategy.
6. Create a cross-finding task and link its execution results.
7. Close and resume later using a deterministic checkpoint comparison.
8. Defer a coverage item with a reason and accept it as a gap.
9. Complete all active work while a report blocker remains; completion stays blocked.
10. Resolve blockers and approve engagement completion with accepted gaps recorded.

---

## 23. Rollout and observability

### 23.1 Feature flags

Recommended flags:

```text
ENGAGEMENT_WORK_ITEMS_ENABLED
ENGAGEMENT_STRATEGY_ENABLED
ENGAGEMENT_STRATEGIST_ENABLED
ENGAGEMENT_COVERAGE_ENABLED
ENGAGEMENT_STRATEGIST_EVENT_TRIGGERS_ENABLED
ENGAGEMENT_STRATEGIST_SCHEDULER_ENABLED
```

Enable manual state/workflows before agent proposals, and agent proposals before event assistance.

### 23.2 Metrics

- Time from opening an engagement to first useful action.
- Resume briefing clicks/deep links.
- Remaining/blocked/deferred work over time.
- Percentage of work items with accepted outcomes and evidence.
- Proposal acceptance, edit, defer, and dismissal rates.
- Duplicate proposal suppression count.
- Strategy revisions per engagement and stale-conflict rate.
- Agent cost/tokens per engagement and per accepted proposal.
- Provider-key-unavailable failures.
- Event trigger suppression/cooldown counts.
- Coverage gaps discovered before reporting.
- Analyst corrections to generated narratives.
- Completion attempts blocked by deterministic checks.

### 23.3 Notifications

Initial release uses in-app decision counts. Later notifications require severity thresholds, batching, quiet hours, and per-user preferences. Do not notify for every strategist thought or pure read.

---

## 24. Recommended PR decomposition

Each migration-bearing branch must rebase onto the current Alembic head before merge. Every PR owns backend/frontend behavior tests as applicable, authorization assertions, audit verification, and accessibility checks. Manual workflows must remain useful even when agent configuration is absent.

The first implementation PR after the task-query cleanup is deliberately limited to manual WorkItem storage and APIs. It does not add agent generation, autonomous triggers, persistent unattended credentials, direct execution, or coverage claims.

### Foundation

1. **Task query/cache cleanup**
   - Add shared task query keys/hooks.
   - Fix status filtering in the API client.
   - Replace finding-local execution-task polling duplication.

2. **Work-item backend foundation**
   - WorkItem, multi-finding links, and immutable result-revision models/migration.
   - Task `work_item_id` link.
   - CRUD/lifecycle/result propose-review APIs, validation, audit, and tests.

3. **Work-item rollups and finding UI**
   - Batched rollup API.
   - Finding counts, filters, quick resolution, and full Tasks tab.

4. **Suggestion routing extensions**
   - `work_item` suggestion kind, typed multi-finding payload, and atomic acceptance behavior.
   - Inert work-item execution-suggestion endpoint followed by existing accepted execution path.
   - Proposal keys, deduplication, and audit.

### Strategy

5. **Objectives and strategy revision backend**
   - Models/migration, concurrency, APIs, history, restore, flush/export.

6. **Read-first Strategy workspace**
   - Navigation, resume facts, current strategy, objectives, work queue, decisions.

7. **Strategy editing and checkpoint UI**
   - Manual revision editor/diff/history.
   - Objective management and deterministic checkpoints.

### Agent collaboration

8. **Engagement context compiler and strategist execution**
   - Bounded dossier, distinct agent identity/configuration, AgentExecution, manual endpoints.

9. **Engagement strategist conversation**
   - Conversation generalization, personal chat, proposal action cards, summarize/clear semantics.

10. **Strategy revision and work proposal actions**
    - Typed agent outputs, explicit accept/deny, stable deduplication.

11. **Finding strategy integration**
    - Strategy relevance, strategist requests, agent outcome review, strategy signals, cross-finding work.

### Coverage and maturity

12. **Coverage backend and closure service**
    - Coverage model, engagement work state, immutable completion decisions, deterministic preflight, accepted gaps, approve/reopen APIs, and audit.

13. **Coverage and completion UI**
    - Coverage matrix/list, gap decisions, closure checklist, completion approval.

14. **Realtime and typed lineage**
    - Work/strategy SSE events, cache invalidation, typed Status links, run/finding/work lineage.

15. **Event-assisted strategist**
    - Material triggers, locks, idempotency, cooldowns, budgets, loop suppression.

16. **Optional scheduler**
    - Only after explicit credential sponsorship, quiet hours, cost controls, and operational approval.

---

## 25. Dependencies and sequencing with current work

The following existing/open work improves the strategist but should not be duplicated:

- slim full finding workbench and finding context promotion;
- persistent approval inbox;
- canonical scope matcher;
- Status filters and stable execution presentation;
- bulk finding triage;
- report readiness;
- scanner preview/confirmation;
- canonical entity view; and
- run-to-finding lineage.

Start new migrations only after the current migration-bearing finding-context branch is merged and Alembic has one confirmed head. The manual work-item backend can begin after that stabilization. The finding Tasks-tab branch must follow or rebase after the active full-finding-workbench stack. Coverage and rich execution linkage should follow canonical entities and run-to-finding lineage, but they do not block the initial strategy/work-item MVP.

---

## 26. Definition of done

The complete program is done when:

1. An analyst can resume an engagement and see deterministic current focus, recent changes, active work, blockers, and decisions in one workspace.
2. Strategy is shared, versioned, attributable, diffable, and restorable.
3. Manual engagement, finding, and cross-finding work can be created, assigned, started, blocked, deferred, resolved, reopened, and audited.
4. Findings show accurate remaining, blocked, deferred, and proposal counts.
5. Work outcomes link to evidence, findings, entities, and executions.
6. The Engagement Strategist produces fact-referenced, structured, bounded proposals using an acting analyst's credential.
7. Agent proposals never mutate current strategy or dispatch execution without explicit acceptance.
8. Finding agents and the strategist coordinate through visible work items, outcomes, and strategy signals.
9. Tactical, scope, approval, and analyst-only boundaries remain unchanged.
10. Coverage states are deterministic or explicitly analyst-decided and support accepted gaps.
11. Completion is blocked by deterministic remaining-work, execution, approval, finding-review, coverage, and report-readiness checks until an analyst approves exceptions.
12. New records participate in flush, archive, export, audit, activity, cache invalidation, and finding merge semantics.
13. Concurrent edits and repeated events do not silently overwrite state or duplicate actions.
14. The system remains useful without an LLM credential; AI improves synthesis and proposals rather than owning the engagement memory.

---

## 27. Final recommendation

Build the engagement state first and the engagement agent second.

The platform already has mature execution governance, agent telemetry, suggestions, approvals, events, and finding-level assistance. The missing foundation is a durable shared strategy plus an analyst work and coverage ledger. Once that state exists, the Engagement Strategist can safely help analysts resume, prioritize, coordinate findings, and decide what to do next without becoming an autonomous or opaque source of truth.
