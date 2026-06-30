# Red Team Dashboard — Approved Roadmap

Approved suggestions from the in-portal Suggestion Box (`/settings/suggestions`). Generated for Claude Code to pick up as future PR work.

## 1. Adding an "Out of Scope / Outside RoE" finding state with report-omission is a low-cost, high-value extension of the existing validation workflow that fills a real engagement-hygiene gap.

**Original suggestion:**

> I'd like the ability to mark findings as OUT OF SCOPE, or OUTSIDE ROE. Some findings are valid, but we cannot target them for clients reasons. So if we surface something that shouldnt be targeted, just mark it that way accordingly. Then allow the option to Omit those marked items in the final report.

**Pros:**
- Directly supports the 'analyst in control' guiding principle — analysts can curate what hits the client deliverable without deleting evidence.
- Cleanly extends the existing Phase 8a pending → validated state machine; likely just a new status value plus a PDF filter, not new infrastructure.
- Preserves an audit trail of findings agents/imports surface but that RoE forbids touching — useful for cross-engagement pattern detection (UX backlog #14) and for defending scope decisions later.
- Reinforces the 'agents scan, analysts exploit' invariant by giving analysts a first-class way to halt agent follow-up on findings that must not be pursued.
- Pairs naturally with the in-progress Phase 10 hybrid ingest, where imported nmap/Nessus data will frequently surface things outside RoE that need triage.

**Cons:**
- Status semantics need a clear decision: is OUT_OF_SCOPE a third terminal state alongside validated/pending, or an orthogonal flag? — leaving this ambiguous will cause UI and PDF-template churn.
- Needs to interact correctly with the Strategic agent (Phase 9) — out-of-scope findings should suppress new task suggestions, otherwise the agent will keep proposing work on things the analyst already vetoed.
- Slight scope creep relative to the four open Charter ideas (attack-path UI, entities tab, engagement-setup wizard) which are arguably higher-leverage and still unfinished.
- Requires a justification/notes field in practice (why was this marked OOS?) to be defensible to clients — otherwise it becomes a silent delete button.

_Approved 2026-06-29T21:06:53.572576+00:00 — suggestion id `019f1532-8501-7783-9416-498a85388019`_

## 2. Burp Suite Pro XML ingest is a natural fit for Phase 10's hybrid import-first model and would extend the existing finding importer with a real scanner format analysts actually use.

**Original suggestion:**

> Want to make the Findings Tab have the ability to properly ingest Burpsuite Pro XML reports, and populate the findings accurately

**Pros:**
- Aligns directly with Phase 10's in-progress 'Hybrid ingest path (nmap/Nessus/recon import)' workstream, which is the right home for scanner-format parsers.
- Builds on already-shipped infrastructure — the CSV/JSON finding importer, `POST /engagements/{slug}/findings/import`, and pending_validation gating — so the new work is a parser plus a UI toggle, not a new pipeline.
- Burp Pro is a staple analyst tool for web findings, so accurate XML ingest directly serves the North Star of 'one pane of glass, no tool-hopping'.
- Respects the 'agents scan, analysts exploit' invariant cleanly — this is analyst-driven ingest of analyst-run scanner output, no agent involvement.
- Severity, evidence (request/response), and host fields in Burp XML map well onto the existing findings schema and the new screenshot/attachment model for evidence storage.

**Cons:**
- Burp XML is verbose and embeds base64 request/response blobs — needs an explicit decision on whether those land as `summary` text, as `Attachment` rows, or both, or the 10 MB attachment limit will bite on large scans.
- Severity mapping (Burp's High/Medium/Low/Information + Certainty) to our severity enum is opinionated and should be settled before coding, or imports will be inconsistent across analysts.
- Risks scope creep into Phase 10 if it's treated as a one-off rather than the first concrete instance of a general scanner-import pattern (Nessus and nmap are already named in 'What Comes Next').
- Burp Pro XML schemas have changed across versions; without pinning a target schema/version the parser will quietly drift.

_Approved 2026-06-29T21:07:41.390853+00:00 — suggestion id `019f1532-8918-7d02-8cd3-a58b20414d98`_

## 3. This suggestion is already approved and on the roadmap (id `019f1536-4ab3-7403-9e77-3a493aa11cf4`, admin-noted "do it nao") — adding submitter/approver attribution to the Suggestion Box feedback flow.

**Original suggestion:**

> For feedback, 

It should say who submitted it, and who approved it.

**Pros:**
- Already approved and queued on the roadmap with an explicit admin 'do it nao' note, so this is in-flight rather than a fresh ask.
- Low-cost change — submitter identity should already exist on the Suggestion row (created_by), and approver identity is captured at the moment of approval, so this is largely a serializer + UI surface change.
- Improves accountability and audit transparency, consistent with the broader audit-logging posture (e.g. `finding.updated`, attachment audit logs) elsewhere in the platform.
- Pairs naturally with UX backlog #19 (analyst activity feed) and #16 (analyst assignment) by making authorship visible in another analyst-facing surface.

**Cons:**
- Duplicate submission — the same idea was already approved on 2026-06-29; re-approving risks creating two tracking entries for one piece of work.
- Needs a small privacy/visibility decision: is submitter shown to all analysts, or only to admins? Worth settling before implementation to avoid rework.

**Admin note:** do it nao

_Approved 2026-06-29T21:08:53.781867+00:00 — suggestion id `019f1536-4ab3-7403-9e77-3a493aa11cf4`_

## 4. A tag-driven "Correlate Findings" action is a lightweight way to surface relationships between findings and dovetails with the planned Entities tab, but it overlaps with already-backlogged tagging and entity-correlation work and needs a clearer definition of what "correlate" produces.

**Original suggestion:**

> We should have a Correlate Findings Button this should be done by tagging the findings

**Pros:**
- Directly serves the Charter's feedback-loop principle by making cross-finding relationships visible without leaving the findings table.
- Tagging is cheap to add on top of the existing first-class `findings` store from Phase 8a and pairs naturally with UX backlog #18 (free-form tagging on findings).
- Acts as a pragmatic stepping stone toward Charter Idea 4 (Entities tab) — shared tags are a poor-man's correlation while structured entity extraction is still proposed.
- Reinforces 'one pane of glass' by letting analysts pivot from a finding to related findings inside the portal instead of mentally cross-referencing.
- Low risk against the 'agents scan, analysts exploit' invariant — this is an analyst-curated organizational feature with no agent execution surface.

**Cons:**
- 'Correlate Findings' is under-specified — unclear whether the button opens a filtered table, a graph view, or generates a correlation report, and that ambiguity will drive UI churn.
- Overlaps significantly with Charter Idea 4 (Entities tab) and UX backlog #1 (person entities) — tag-based correlation may end up as throwaway work once entity extraction lands.
- Duplicates UX backlog #18 (free-form tagging) unless this suggestion is explicitly framed as 'tagging + a correlate action on top'.
- Manual tagging is high-friction and tends to decay — without auto-tagging from finding content or entities, the correlate button will only be as good as analyst discipline.
- Adds scope on top of four still-unfinished Charter ideas (attack-path UI, entities, engagement-setup wizard, feedback loop) and two already-approved roadmap items (OOS state, Burp XML ingest).

**Admin note:** Sounds like a bet

_Approved 2026-06-29T22:29:11.628800+00:00 — suggestion id `019f157e-cf54-7af2-ab3f-a2a765ea8016`_

## 5. Decoupling engagement creation from auto-run is a sensible refinement of Charter Idea 3 that fits naturally with Phase 10's import-first model, though it softens the "Save and start" feel Nasir originally described.

**Original suggestion:**

> When creating an engagement, don't force the user to start a run. Allow them to build the engagement, specify parameters, etc if they have existing documents to provide like the ROE, or any initial findings they want to present, allow that, then create the engagement. Once its created allow for the ability to start the run.

**Pros:**
- Aligns with Phase 10's in-progress 'hybrid execution (import-first model)' — analysts often have RoE docs, prior recon, or existing findings to seed before any agent run kicks off.
- Reinforces the 'analyst in control, agents assist' guiding principle by making run-launch an explicit analyst decision rather than a side effect of creation.
- Pairs cleanly with already-shipped infrastructure: scope bulk import, finding importer (CSV/JSON), and attachments could all be used during the pre-run setup window.
- Complements the approved Burp XML ingest and OOS-finding roadmap items — both assume analysts can stage data into an engagement before agents start suggesting tasks.
- Low-risk change to Charter Idea 3 — splits 'Save and start' into 'Save' + a separate 'Start OSINT' action, which is a small UX/state change, not new infrastructure.

**Cons:**
- Directly softens Charter Idea 3's stated want ('Save and start' kicks off OSINT automatically) — Nasir's original framing should be re-confirmed before this lands as the default behavior.
- Introduces a new engagement lifecycle state ('created, not yet started') that needs decisions: does the Strategic agent fire on imported findings during the pre-start window, or stay dormant until the analyst hits Start?
- Risks scope creep on the still-unfinished engagement-setup wizard (Charter Idea 3) by expanding it from a Nessus-style form into a multi-step staging workflow.
- Needs a clear answer for what 'start the run' means once OSINT is no longer the implicit kickoff — is it a button per phase (OSINT, Vuln Scan…), or a single global 'go'?

_Approved 2026-06-30T12:54:37.593078+00:00 — suggestion id `019f159c-6630-7323-9e46-7374ef4dd4e9`_

## 6. Linking observations to specific findings (and surfacing those back-references on the finding view) is a natural, low-cost extension of the existing Phase 8e observations system that strengthens the feedback-loop principle, but it overlaps with the proposed Entities tab and the recently-approved tagging/correlate work and needs a clear data-model decision before coding.

**Original suggestion:**

> Observations should be able to reference objects in the engagement. For example;

In relation to Domain.com - finding 1;

Observation; This domain is remarkably hardended for its current use case 
Evidence : Findings 1, 2, 3, 6. 

Observations are then tagged back into the finding when you look at them later.

**Pros:**
- Directly serves the Charter's 'feedback loop' principle — observations referencing findings (and vice versa) is exactly the 'found → finding → table updates' bidirectional flow the North Star calls for.
- Builds on already-shipped Phase 8e observations + findings infrastructure; likely just a join table (observation_finding_refs) plus serializer additions, not new subsystems.
- Pairs naturally with the in-progress attack-path slide-over (Phase 9 / Charter Idea 2) — analyst commentary like 'this domain is hardened, see findings 1,2,3,6' is high-signal context to show next to suggested tasks.
- Reinforces 'one pane of glass' by letting analysts pivot from an observation to its supporting findings (and back) without leaving the engagement view.
- Observations already flow into the PDF (Phase 8e), so cross-referenced findings could enrich the report narrative with minimal template work — a clean win for the analyst deliverable.

**Cons:**
- Overlaps meaningfully with the just-approved 'Correlate Findings via tagging' item (id `019f157e-...`) and Charter Idea 4 (Entities tab) — without a coordination decision, you'll end up with three parallel ways to relate findings (tags, entities, observation refs).
- Reference shape is under-specified: are refs typed (finding, entity, scope item, task) or finding-only? — the example mentions 'Domain.com' which is an entity/scope item, not a finding, so the data model needs to settle that up front.
- Back-tagging onto findings ('observations tagged back into the finding when you look at them later') needs a UI decision — a new section in the finding slide-over, a badge count, or both — or it will get lost.
- Lower leverage than the four still-unfinished Charter ideas (attack-path UI completion, entities tab, engagement-setup wizard) and the two earlier-approved roadmap items (OOS state, Burp XML ingest) — risks scope creep on the observations subsystem while bigger gaps remain.
- Free-text references like 'Findings 1, 2, 3, 6' imply either a picker UI or a parser for inline IDs; picking the wrong one (parser vs. structured multi-select) will cause rework once entities land.

**Admin note:** Some Tweaks

_Approved 2026-06-30T12:54:31.621534+00:00 — suggestion id `019f159f-88ff-7c11-90f0-467aa6d8e48e`_
