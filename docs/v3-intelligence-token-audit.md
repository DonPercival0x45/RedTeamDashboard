# v3 Intelligence and Token-Cost Audit

Status: final Track B audit after per-engagement activation (#215) and UI (#216).

## Runtime call inventory

| Path | Architecture | Trigger | LLM calls | Guard |
|---|---|---|---:|---|
| `finding.created` / `finding.updated` | legacy | automatic | 1 per event | engagement auto-assess + receipt |
| `strategy.reassess.requested` | legacy | automatic | 1 per event | engagement auto-assess + cooldown + receipt |
| collection/run completion | v3 | automatic | at most 1 analysis call per changed significant-finding batch | global automation kill switch + per-engagement v3 + actor identity + receipt + batch fingerprint |
| coverage gap | v3 | automatic | 1 strategy call per durable event | same gates; bounded milestone payload |
| baseline completion | v3 | automatic | 1 ideation call per durable event | same gates; bounded milestone payload |
| milestone maintenance | v3 | automatic | 0 or 1 coverage-review call | deterministic compaction first; only if still over budget; unchanged context fingerprint skips |
| `/intelligence/runs` | v3 | analyst | exactly 1 selected-mode call | non-guest identity + acting user's BYO key + row/Memory lock + read-only check |
| legacy strategist/per-finding Strategic APIs | v3 | stale/direct client | 0 | rejected with 409 |

Explicit finding chat, triage, and rewrite helpers are not automatic intelligence-plane paths. They remain analyst-triggered editing utilities with separate cost attribution.

## Findings and remediation

### 1. Significant findings lacked usable evidence

The B3 context carried finding counts and UUIDs but no bounded title/summary/status/target evidence. An analysis model could not reliably interpret a new batch from IDs alone.

Remediation: `significant_finding_batch()` now projects prioritized compact evidence, excludes raw `details`, truncates text/tags, and enforces both item and estimated-token ceilings. Critical/high and unvalidated findings are selected first. All four system prompts explicitly classify finding, Memory, and milestone text as untrusted evidence rather than instructions; the agent cannot call tools or bypass analyst approval.

The hard safety boundary is not prompt wording: the model has structured output with no bound tools, persistence is engagement-scoped and reviewable/reversible, and execution still requires analyst authorization. The trust-boundary prompt is defense in depth.

Defaults:

- `INTELLIGENCE_FINDING_TOKEN_BUDGET=4000`
- `INTELLIGENCE_MAX_SIGNIFICANT_FINDINGS=50`
- existing HOT Memory ceiling remains `HOT_MEMORY_TOKEN_BUDGET=10000`

Like the Memory projection, the evidence projection guarantees one highest-priority item. Text truncation keeps that item below the production 4k ceiling; an operator-configured tiny budget can report a one-item estimate above its configured budget rather than silently hiding all evidence.

### 2. Unchanged pending/high findings could be analyzed repeatedly

The significance predicate intentionally includes unvalidated and high-severity findings, so every later run milestone could find the same set significant.

Remediation: automatic analysis stores a stable significant-batch fingerprint in `AgentExecution.input`. A completed automatic analysis with the same fingerprint suppresses model/key resolution and invocation. Finding changes update the fingerprint and make the batch eligible again. Manual analysis is never deduplicated. The read/check/invoke sequence is serialized by a transaction-scoped per-engagement advisory lock, including direct `handle_milestone` callers. The fingerprint reflects the thread/since-scoped significance predicate: different genuinely new sets may each run, while identical included evidence collapses across milestone types.

### 3. Coverage maintenance could repeat without state change

If deterministic compaction left Memory above budget and a coverage review made no folds, each later milestone could invoke the same maintenance prompt again.

Remediation: automatic coverage review stores the full bounded context fingerprint. An unchanged completed review is skipped before resolving the model/key. Failed setup/model attempts remain retryable.

### 4. Milestone and methodology context was incomplete

Coverage-gap reason/node and baseline methodology identity were discarded before prompt assembly.

Remediation: the consumer now passes a bounded allowlist of milestone fields. Context also includes the frozen methodology slug/version. Collection scope lists and raw finding details are deliberately excluded.

### 5. Legacy direct-call bypasses remained for v3

The UI hid legacy controls, but stale clients could still call the legacy Engagement Strategist or per-finding Strategic analyze APIs for a v3 engagement.

Remediation: those call-producing endpoints now reject v3 with HTTP 409. Historical strategist chat remains readable and old suggestions remain reviewable so conversion does not destroy legacy work.

## Accounting and observability

Every v3 `AgentExecution` now records:

- exact provider/model and acting analyst;
- prompt mode and trigger;
- estimated prompt tokens;
- estimated response tokens;
- significant total/included counts;
- significant-batch and full-context fingerprints.

`tokens_in`, `tokens_out`, and `cost_usd` remain reserved for provider-reported usage rather than mixing estimates with actual billing. Estimates live in execution JSON metadata and are suitable for regression/capacity analysis.

## Safety invariants verified

- v3 never falls back to legacy automatic intelligence when the global switch is off.
- archived/completed engagements cannot run manual or queued v3 intelligence.
- no model/key resolution occurs for empty or unchanged automatic analysis batches.
- compaction remains deterministic-first, engagement-locked, and reversible.
- persistence failures roll back and retry; model failures do not leave partial Memory/work.
- legacy engagements retain their existing paths until explicitly converted.

## Residual retry behavior

A failing durable event is bounded by the receipt retry/DLQ limit. A later distinct milestone carrying the same fingerprint may retry it because only completed automatic executions suppress future calls. This is intentional availability behavior: transient provider/schema failures must recover without requiring a finding edit. Fingerprint and prompt-estimate metadata make persistent cross-event failures observable; operators should alert on repeated failures for the same fingerprint.

Strategy and ideation use event-level receipt deduplication rather than context fingerprints. Their production emitters must keep stable event IDs and emit genuine state transitions (especially coverage-gap reopen and baseline completion), not polling noise.

## Resolved blocker: production actor identity

**Closed by #227.** `collection.job.completed` milestones now carry the
authenticated analyst (`approved_by` for gated playbooks, `requested_by` for
direct runs; legacy rows omit the key and the consumer refuses rather than
borrowing). Combined with `run.completed` (already actor-bearing) and
`baseline.completed`/`coverage.gap.opened` (actor-bearing when their emitters
pass a user actor), the consumer can now resolve the exact BYO model key that
authorized collection on every production milestone that actually fires today.

Automatic v3 remains default-off by choice. Enabling it is a controlled
canary — flip `V3_INTELLIGENCE_ENABLED=true` on one v3 engagement, verify
receipts, attribution, retries, dedup, and costs against a real provider, then
broaden. Manual v3 intelligence is unaffected and fully operational.

**Wiring prerequisite (Track A, not yet blocking):** `mark_baseline_completed`
and `open_coverage_gap` have no production caller yet. When they are wired into
a real flow, their call sites must pass a provable user actor (the same pattern
#227 established); otherwise the consumer DLQs the event because the milestone
lacks `acting_user_id`. Until those emitters ship, they cannot block the
collection-run and run-completion milestones that actually fire in production.
