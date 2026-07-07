# Red Team Dashboard — Approved Roadmap

Approved suggestions from the in-portal Suggestion Box (`/settings/suggestions`). Generated for Claude Code to pick up as future PR work.

## Open (Approved · Not Shipped)

Ordered by priority (P1 = highest, then P2… then unranked). Unranked rows haven't been triaged yet — treat them as lower priority than any numbered row unless an admin note says otherwise.

### 1. [P1] A tag-driven "Correlate Findings" action is a lightweight way to surface relationships between findings and dovetails with the planned Entities tab, but it overlaps with already-backlogged tagging and entity-correlation work and needs a clearer definition of what "correlate" produces.

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

### 2. [P1] Adding scope-derived quick-action chips (e.g. "Enum domain X", "nmap IP Y") next to the Scope tab's Start-a-run prompt is a low-cost analyst accelerator that reinforces the feedback loop, but it overlaps with the Strategic agent's suggestion surface and needs a clear rule for which actions are agent-eligible vs. analyst-only.

**Original suggestion:**

> In the SCOPE of an engagement, 

You have a Section called Start a run and it asks for prompt. In addition to the prompt it should provide some quick actions based on what was entered in the scope. Like "Enum X domain", "nmap IP address" and then the ability to fire them all, or fire them individually.

**Pros:**
- Serves the 'one pane of glass' North Star by turning parsed scope items directly into runnable actions instead of forcing the analyst to hand-type prompts for obvious first moves.
- Builds cleanly on already-shipped infrastructure — the scope parser already classifies lines as domain/ip/cidr/url/email/org, so mapping kind→suggested action is a small frontend addition plus a thin catalog on the backend.
- Reinforces the 'findings first' feedback loop from Charter Idea 5: kick off enum on scope → findings populate → Strategic agent suggests next tasks, closing the loop faster on a fresh engagement.
- Pairs naturally with Charter Idea 3 (Nessus-style setup) — 'Save and start' auto-launches OSINT, and these quick actions extend the same 'configure → launch' feel to later phases (vuln scan, recon).
- Fire-all / fire-individually gives analysts a graceful default without removing control, consistent with the 'analyst in control, agents assist' guiding principle.

**Cons:**
- Overlaps with the Phase 9 Strategic agent's suggestion surface — analysts will have two places proposing tasks (scope-derived chips vs. finding-derived suggestions), and the relationship between them needs pinning before coding.
- Must respect the 'agents scan, analysts exploit' Decided invariant — the quick-action catalog needs an explicit allowlist (enum, scan, recon) with any validation/PoC-flavored action gated to analyst-only, or the Tactical agent's `TacticalRefusedExploit` boundary will start firing on user-initiated runs.
- Under-specified on the action catalog — 'Enum X domain' and 'nmap IP address' are examples, but the full mapping (domain → subfinder/amass/whois? ip → nmap flavor? cidr → sweep?) needs to be enumerated and versioned, or the chip set will drift per analyst expectation.
- 'Fire them all' can produce a burst of concurrent runs that stresses the ephemeral executor still in flight under Phase 10 and inflates LLM/tool cost tracked by the Phase 11 Costs tab — needs a concurrency cap or confirmation step.
- Competes for attention with four still-unfinished Charter ideas (attack-path UI completion, entities tab, engagement-setup wizard, feedback loop) and several higher-priority approved items (Findings Cleanup P1, Status Tab Cleanup P2), so it likely rides behind them rather than jumping the queue.

**Admin note:** Scope Fix

_Approved 2026-07-06T20:34:50.015567+00:00 — suggestion id `019f390f-cedd-7df0-8be8-e2f4db7a99ba`_

### 3. [P1] Per-engagement and per-task model/key selection is a natural extension of BYO keys and the Phase 11 cost engine, and it overlaps meaningfully with already-approved UX-backlog #7 ("per-role multi-model support") — worth folding into that item rather than tracking separately.

**Original suggestion:**

> I put multiple keys on the key manager. I'd like the ability to select which key (denoted by label) for specific engagements. As well as for specific tasks. 

Some LLMs are good at some tasks, and bad at others, and the ability to customize who does what would be great and would be good for cost management as well.

**Pros:**
- Builds directly on already-shipped infrastructure — BYO keys (merged) and Phase 11 cost attribution — so plumbing model/key selection through to agent_executions is mostly a routing + settings-UI change, not new subsystems.
- Serves the 'analyst in control, agents assist' guiding principle by letting the analyst tune which model handles Strategic vs. Tactical vs. summarization work.
- Directly supports cost management, which is the whole point of the Phase 11 Costs tab — cheap models for high-volume enum, premium models for reasoning-heavy Strategic runs is a clear win.
- Aligns with UX backlog item #7 (per-role multi-model support, incl. GLM-5 / OpenAI-compatible endpoints), so this suggestion effectively sharpens an already-captured need.
- No conflict with the 'agents scan, analysts exploit' invariant — this is routing/config, not an execution-surface change.

**Cons:**
- Overlaps with UX backlog #7 (per-role multi-model support) — should be merged into that item rather than tracked as a separate roadmap entry to avoid duplicate work.
- Selection granularity is under-specified: per-engagement, per-agent-role (Strategic/Tactical), per-task-kind, or all three? — each level implies a different data model (engagement setting vs. task payload field vs. user default) and needs pinning before coding.
- Introduces a resolution-order problem (task override → engagement default → user default → org fallback) that must be defined explicitly, or analysts will be surprised by which key actually ran a job.
- Adds settings-UI surface area and a new selector inside the task/run kickoff flow, which competes with the already-approved 'scope-derived quick actions' work (id `019f390f-...`) that is also modifying that same run-kickoff surface.
- Lower leverage than the four still-unfinished Charter ideas (attack-path UI, entities tab, engagement-setup wizard, feedback loop) and Phase 10 hybrid ingest — likely rides behind the existing P1/P2 queue.

_Approved 2026-07-06T20:38:06.093669+00:00 — suggestion id `019f3924-2820-7583-b2df-0baf1d800b22`_

### 4. [P2] Visually distinguishing failed/empty agent runs from successful ones is a small, high-value observability fix that fits squarely inside the already-approved Status Tab Cleanup bundle rather than standing alone.

**Original suggestion:**

> New feedback

If an agent run happens, and it errors out, provides no results, or was generally unsuccessful, theres no way to differentiate from successful runs with actual information. Differentiate between successful runs by an agent and failed runs.

**Pros:**
- Directly reinforces the approved Status Tab Cleanup item (id `019f1a44-...`) which already calls for 'what I tried to do, what happened, or why I failed' — surfacing success/failure state is the atomic version of that ask.
- Pairs cleanly with the approved unique-run-ID + toast work (id `019f1a48-...`) and the step-log item (id `019f23c3-...`); together they form a coherent run-detail story instead of three disjoint tweaks.
- Very low cost — `agent_executions` from Phase 9/11 already exists and almost certainly carries enough state (exit status, error, token/finding counts) to derive a success/failure/empty badge without new infrastructure.
- Serves the 'analyst in control' principle and the feedback-loop North Star by making it obvious at a glance which runs need analyst follow-up versus which produced usable output.
- No conflict with the 'agents scan, analysts exploit' invariant — this is pure observability over existing agent output.

**Cons:**
- Overlaps enough with the three already-approved Status Tab Cleanup items that tracking it as a separate roadmap entry risks duplicate work; better bundled under that admin grouping.
- Under-specified on the taxonomy — at minimum needs decisions on Success / Failure / Empty-but-OK / Partial, otherwise the badge will be inconsistent across agents (Strategic vs. Tactical vs. importer runs).
- 'No results' is ambiguous: a scan that legitimately finds nothing is not a failure, and conflating the two will train analysts to ignore the badge — the rule for empty-vs-failed needs pinning before coding.
- Lower leverage than the four still-unfinished Charter ideas (attack-path UI, entities tab, engagement-setup wizard, feedback loop) and Phase 10 hybrid ingest, so it should ride along with Status Tab Cleanup rather than pull attention away.

**Admin note:** Status Tab Cleanup

_Approved 2026-07-02T17:01:24.354225+00:00 — suggestion id `019f1ea2-3abd-7e01-a498-c7a19b630087`_

### 5. [P2] (no summary)

**Original suggestion:**

> Let me add entities we found to the scope under a different section. 

Defined Scope = Things the client provided to us formally, 
Found Scope = Entities we found and should be able to run quick actions on to generate findings.

_Approved 2026-07-07T19:16:11.001484+00:00 — suggestion id `019f3dfb-a7f7-7ad1-adb4-0cdc3e2909a9`_

### 6. [P3] Admin-driven user/guest lifecycle management is a reasonable platform-hygiene ask, but it sits outside the Charter's North Star (analyst workflow / findings loop) and partially duplicates capabilities Entra already provides in a single-tenant deployment.

**Original suggestion:**

> Admins should have the ability to delete Users and guests accounts. as well as add from the management console

**Pros:**
- Closes a real operational gap — there is no admin surface in HANDOFF for user/guest CRUD today, only Entra SSO at the identity layer and BYO provider keys at the user-settings layer.
- Pairs naturally with the already-approved submitter/approver attribution work (id `019f1536-...`) — both require a clearer notion of who 'a user' is inside the portal and an admin view over them.
- Low risk against the 'agents scan, analysts exploit' invariant — this is a platform/governance feature with no agent execution surface, so it doesn't conflict with any Charter Decided items.
- An admin console for invites/removals supports audit posture consistent with existing audit-logged actions (finding.updated, attachment uploads, etc.).

**Cons:**
- Outside the Charter's North Star and the four still-unfinished Charter ideas (attack-path UI, entities tab, engagement-setup wizard, feedback loop) — this is platform admin, not analyst workflow.
- Partially duplicates Entra ID, which is the single-tenant identity source per Phase 7 — adding/removing users is typically done in Entra, and an in-portal 'add user' flow needs a clear decision on whether it invites via Entra B2B, creates local-only rows, or both.
- Under-specified: 'delete' could mean soft-delete, hard-delete, or revoke-access — and findings, suggestions, attachments, and audit logs all carry `created_by` references that need a deletion/anonymization policy decided up front.
- No 'management console' exists yet in the codebase per HANDOFF (only a Settings page for provider keys), so this implies net-new admin surface area, role model, and route guards — more infrastructure than the one-line ask suggests.
- Adds scope on top of six already-approved roadmap items and four open Charter ideas, with lower analyst-facing leverage than any of them.

_Approved 2026-07-02T17:00:45.372153+00:00 — suggestion id `019f191d-a0a7-7043-9755-eb2d0458f0bc`_

### 7. [P3] Routing Suggestion Box AI evaluations through a dedicated, cheap platform key (e.g. GPT-4o) instead of the submitter's BYO key is a sensible consistency + fairness fix, but it introduces a new billing/secret-management surface that needs an owner and a cost cap.

**Original suggestion:**

> Feedback needs to use its own API key separate from the user. Use Chatgpt 4o or something cheap so you get consistent AI feedback.

**Pros:**
- Removes an unfair cost externality — analysts submitting feedback shouldn't spend their own BYO provider credits (per the BYO keys system merged pre-Phase 11) to have the platform evaluate their idea.
- Guarantees consistent evaluation quality and tone across all submissions by pinning one model, which matters because these evaluations feed the admin's Approve/Reject decision on the roadmap.
- Removes a failure mode where a user with no configured key, an expired key, or a local/unpriced model produces low-quality or missing feedback on suggestions.
- Cost engine from Phase 11 already exists and can attribute this spend to a synthetic 'platform/feedback' agent bucket, so tracking is essentially free.
- No conflict with the 'agents scan, analysts exploit' invariant — this is meta-tooling over the Suggestion Box, not an execution surface.

**Cons:**
- Introduces a new org-level secret distinct from the existing per-user Fernet-encrypted BYO keys — needs a decision on where it lives (env var, Key Vault, a new `PlatformProviderKey` model) before coding.
- Creates an unbounded org-paid spend vector; without a per-user or per-day rate limit on suggestion submissions, a chatty analyst (or a loop) directly bills the platform owner.
- Model choice ('ChatGPT 4o or something cheap') is under-specified and drifts over time — needs to be a configurable setting, not hardcoded, or it will rot the same way `_RATE_TABLE` in `pricing.py` can.
- Slight tension with the BYO-keys design intent (per-user provider choice, per-user cost) — worth an explicit admin call that feedback evaluation is a platform function, not a user function.
- Pure meta-tooling — doesn't advance any of the four still-unfinished Charter ideas (attack-path UI, entities tab, engagement-setup wizard, feedback loop) or Phase 10 hybrid ingest, so it competes for attention with higher-leverage work.

**Admin note:** Key FIx Issues

_Approved 2026-07-06T20:33:58.451343+00:00 — suggestion id `019f3922-08a2-7712-b151-9879d32f74d3`_

### 8. [P3] (no summary)

**Original suggestion:**

> More quick actions, make them entity dependent and engagement aware. So if we've already run domain scan, maybe suggest something else? If there are tools available, suggest running tools, etc.

_Approved 2026-07-07T19:16:05.062589+00:00 — suggestion id `019f3e01-01e7-7ef1-b91b-a27a5d7ad0b8`_

### 9. [P4] (no summary)

**Original suggestion:**

> In relation to AI keys, 

1. A test button. allow us to test the key and endpoint to see if they're alive
2. Instead of asking me which model I want to use, just tap the endpoint provided to see all the models. usually they have them at /models/ for some providers so it fills in dynamically. It would future proof for model changes/endpoint changes etc
3. Maybe build a chatbot that uses your keys, kinda designed like a small terminal input to help navigate the site + manage the engagement live.

_Approved 2026-07-02T16:58:53.646161+00:00 — suggestion id `019f1eae-eeef-7712-9f1e-77e6c73203f6`_

### 10. [P4] (no summary)

**Original suggestion:**

> Need the ability to run quick actions against known Entities.

_Approved 2026-07-07T19:16:12.276157+00:00 — suggestion id `019f3dfa-1a6c-7a62-9a1f-bfc91630f1cf`_

### 11. [P5] (no summary)

**Original suggestion:**

> When clicking on the engagement, its not immediately clear where you should click to open it. Either make the whole card clickable with a separate clickable + to add or make a tiny clickable 'Enter' 'Start' or 'Continue' or something to make it easy to find.

_Approved 2026-07-07T19:16:07.757638+00:00 — suggestion id `019f3dff-be61-7bb0-8b37-37cc5f698997`_

### 12. [P7] (no summary)

**Original suggestion:**

> Allow analysts to tweak which model they wish to use before doing actions. Either set the default model in the key setting or have per Engagement settings where the models the analyst wants to use are togglable. To find which models belong to which endpoints setup probe endpoints to detect all the current available models for use. Then save those preferences to the user.

_Approved 2026-07-07T19:16:09.658665+00:00 — suggestion id `019f3dfe-40e6-7bb0-9ff5-0dcc1cae3340`_

### 13. [P8] (no summary)

**Original suggestion:**

> Modes to cater to analyst skill levels.

Simple: Agent handholding, plug and play, very little customization (Baby's first engagement)
Normal: Some handholding, some customizations, just the every day experience. 
Advanced: Customizations for every aspect of the experience.

_Approved 2026-07-02T16:58:55.862818+00:00 — suggestion id `019f1eaa-139e-7ba2-af03-5422cf1aae48`_

### 14. [P9] (no summary)

**Original suggestion:**

> Graphspy integration pls

_Approved 2026-07-02T16:58:33.698557+00:00 — suggestion id `019f1f26-d5ee-73d3-95a0-d98ecbc5fe57`_

### 15. [unranked] Adding an "Out of Scope / Outside RoE" finding state with report-omission is a low-cost, high-value extension of the existing validation workflow that fills a real engagement-hygiene gap.

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

### 16. [unranked] This suggestion is already approved and on the roadmap (id `019f1536-4ab3-7403-9e77-3a493aa11cf4`, admin-noted "do it nao") — adding submitter/approver attribution to the Suggestion Box feedback flow.

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

### 17. [unranked] Linking observations to specific findings (and surfacing those back-references on the finding view) is a natural, low-cost extension of the existing Phase 8e observations system that strengthens the feedback-loop principle, but it overlaps with the proposed Entities tab and the recently-approved tagging/correlate work and needs a clear data-model decision before coding.

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

### 18. [unranked] Reformatting the "What's New" surface to hide infra-only changes (Deploy/CLI/Images) and group entries by user-facing categories (Bug Fixes / Features / QoL / Ops) is a low-cost polish that improves the release-notes experience but is pure presentation work with no Charter or roadmap leverage.

**Original suggestion:**

> Want the Whats New Windows to actually remove Deploy, CLI, Images and have them have a format that is broken down into categories of what was fixed. For example, going in no particular order and not limited to these: ["Bug Fixes","Features","QualityOfLife","Operations"] . This should be set up like how Development companies release Updates to show the end user all they really care about.

**Pros:**
- Aligns with the 'one pane of glass' North Star by making in-portal release notes feel like a real product changelog rather than a raw commit/area dump.
- Hiding Deploy/CLI/Images categories matches the analyst-facing framing — those areas are operator concerns, not analyst workflow, and don't belong in the analyst's What's New.
- Category grouping (Bug Fixes / Features / QoL / Ops) is a well-understood pattern and should be a small frontend change plus a tagging convention on changelog entries, not new infrastructure.
- Pairs well with the recently-approved submitter/approver attribution work on the Suggestion Box (id `019f1536-...`) — both are part of tightening the in-portal feedback/communication surfaces.

**Cons:**
- Pure presentation polish — doesn't advance any of the four still-unfinished Charter ideas (attack-path UI, entities tab, engagement-setup wizard, feedback loop) or unblock Phase 10.
- Category taxonomy needs to be pinned before coding (the suggestion explicitly says 'not limited to these'), or entries will be tagged inconsistently and the grouped view will degrade.
- Requires a decision on where category metadata lives — hand-curated per release-note entry, derived from PR labels, or mapped from the current Deploy/CLI/Images areas — and each option has different maintenance cost.
- Filtering out Deploy/CLI/Images entirely is a stronger claim than re-grouping; some operations changes (e.g. Entra setup, cost engine) genuinely affect analysts and shouldn't be silently hidden — needs a clearer rule than 'remove these three'.
- Competes for attention with two already-approved roadmap items (OOS findings state, Burp XML ingest) and the in-flight Phase 10 work, which have higher analyst-workflow leverage.

**Admin note:** Whats New CleanUp

_Approved 2026-07-01T15:29:24.258938+00:00 — suggestion id `019f193a-1835-7260-bed5-4195ee3e518a`_

## Shipped

Approved items that have landed. Kept here (not deleted) so the roadmap doubles as a running changelog.

- **2026-07-06** — This is a duplicate of an already-approved P1 roadmap item ("Findings Cleanup", id `019f2472-...`) requesting a centered modal for manually adding findings with typed fields, dropdowns, and a date picker. (suggestion id `019f2472-e644-7411-a38d-fc3db03f2d01`)
- **2026-07-02** — Decoupling engagement creation from auto-run is a sensible refinement of Charter Idea 3 that fits naturally with Phase 10's import-first model, though it softens the "Save and start" feel Nasir originally described. (suggestion id `019f159c-6630-7323-9e46-7374ef4dd4e9`)
- **2026-07-02** — Burp Suite Pro XML ingest is a natural fit for Phase 10's hybrid import-first model and would extend the existing finding importer with a real scanner format analysts actually use. (suggestion id `019f1532-8918-7d02-8cd3-a58b20414d98`)
- **2026-07-02** — In status; (suggestion id `019f1a44-d1b2-7153-8456-90a33073abbc`)
- **2026-07-02** — This suggestion is already approved and on the roadmap (id `019f1a48-d786-79a1-acbc-8fce8fae5859`, admin-noted "Status Tab Cleanup") — adding unique run IDs with a trackable toast on agent run kickoff. (suggestion id `019f1a48-d786-79a1-acbc-8fce8fae5859`)
- **2026-07-02** — A per-run step-by-step execution log in the expanded Status view is a natural extension of the already-approved Status Tab Cleanup work and directly serves observability needs, though it overlaps enough with those items that it should be scoped as part of that bundle rather than a standalone effort. (suggestion id `019f23c3-877f-78c3-8cbf-aaef6a613cfd`)
