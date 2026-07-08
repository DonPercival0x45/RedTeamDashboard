# Red Team Dashboard — Approved Roadmap

Approved suggestions from the in-portal Suggestion Box (`/settings/suggestions`). Generated for Claude Code to pick up as future PR work.

## Open (Approved · Not Shipped)

Ordered by priority (P1 = highest, then P2… then unranked). Unranked rows haven't been triaged yet — treat them as lower priority than any numbered row unless an admin note says otherwise.

### 1. [P1] AI-assisted rewrite of manually-entered finding descriptions is a low-cost analyst accelerator that plugs cleanly into the existing summary editor and BYO-keys plumbing, but it needs guardrails against fabrication and a clear boundary from the Strategic agent's suggestion surface.

**Original suggestion:**

> We need the ability to be able to use AI triage on Manual findings for the description so that it can be more technically sound

**Pros:**
- Fits the 'analyst in control, agents assist' guiding principle — the analyst writes the raw finding, AI polishes tone/technical accuracy, and the analyst still validates before it hits the PDF.
- Builds directly on already-shipped infrastructure: the Analyst UX summary editor (PATCH /findings/{id}), BYO provider keys, and Phase 11 cost attribution can all be reused, so this is mostly a new endpoint + button, not new subsystems.
- Improves the client deliverable quality — since only validated findings hit the PDF (Phase 8a) and summaries render in the report, better-written summaries directly raise report polish with minimal risk.
- Complements the just-approved finding importer and manual add-finding modal by giving analysts a fast way to level up terse manual entries into report-grade prose.
- No conflict with the 'agents scan, analysts exploit' invariant — this is text rewriting, not task dispatch or execution.

**Cons:**
- LLM rewrite of a finding description risks hallucinating technical details (CVE numbers, affected versions, exploit mechanics) that the analyst didn't write — needs a clear 'rewrite tone, don't add facts' prompt discipline and probably a diff/accept-reject UI rather than in-place replacement.
- Overlaps with approved roadmap item #1 (dedicated platform key for feedback AI) and #2/#5 (per-engagement model+key selection) — needs an explicit decision on whether triage uses the analyst's BYO key, a platform key, or per-engagement selection before coding.
- Under-specified scope: is this only the `summary` field, or also `title`, `severity` suggestion, and `phase`? — each additional field expands the trust surface and the review-UI complexity.
- Slight tension with the Strategic agent's role (Phase 9) — Strategic already reasons over findings to suggest tasks; a second AI surface that rewrites the finding text itself needs a clear boundary or the two will step on each other (e.g. Strategic suggesting tasks against AI-fabricated details).
- Pure analyst-UX polish — doesn't advance any of the four still-unfinished Charter ideas (attack-path UI, entities tab, engagement-setup wizard, feedback loop) or in-progress Phase 10 hybrid ingest, so it competes for attention with higher-leverage work.
- Adds a new audit consideration — `finding.updated` audit log should probably distinguish human edits from AI-rewrite edits so the provenance of report language is defensible to clients.

**Admin note:** Yes

_Approved 2026-07-08T20:41:01.706220+00:00 — suggestion id `019f4373-12b5-7961-9972-00a11417dcd0`_

### 2. [P1] A per-finding activity log ("who ran what against this finding, when") directly serves the feedback-loop North Star and is already captured as UX backlog #2 ("Finding work log / activity drill-down") — worth folding into that item rather than tracked separately.

**Original suggestion:**

> Inside of Findings, there should be a small log of activity related to it. For example..

Ken initiated crt.sh agent - datetime
Ken initiated domain scan - datetime
Nasir initiated tool - datetime.

**Pros:**
- Already captured as UX backlog item #2 ('Finding work log / activity drill-down — what was tried against this finding'), so this suggestion sharpens an existing need rather than introducing net-new scope.
- Directly serves the Charter's feedback-loop principle by making the 'found → finding → tasks → act → found again' cycle visible on the finding itself.
- Reinforces the 'one pane of glass' North Star — analysts stop hunting across Status/Costs/Runs tabs to reconstruct what was tried against a given finding.
- Low implementation cost given existing infrastructure: `agent_executions`, `tasks`, and audit-logged actions (`finding.updated`, attachment uploads) already carry `created_by` + timestamps + finding_id linkage, so this is largely a serializer + slide-over UI addition.
- Pairs naturally with several approved items — #9 run→finding causality visibility, #15 submitter/approver attribution, UX #19 analyst activity feed — all of which want the same 'who did what, when' surface at different scopes.

**Cons:**
- Duplicates UX backlog #2 — should be merged into that entry rather than tracked as a standalone roadmap item, or you'll get parallel implementations.
- Overlaps with approved item #9 (visible run→finding feedback) and UX #19 (per-engagement analyst activity feed) — needs an explicit decision on whether this is a filtered view of a global activity stream or its own per-finding log.
- Scope of 'activity' is under-specified: agent runs only, or also manual finding edits, attachment uploads, status/severity changes, task creation, and observation links? Each has a different data source (agent_executions vs. audit log vs. tasks table).
- Manual tool invocations ('Nasir initiated tool') aren't currently first-class events — analysts run tools outside the portal and upload results, so capturing those requires either a manual 'log an action' UI or hooking Phase 10's hybrid ingest.
- Lower leverage than the four still-unfinished Charter ideas (attack-path UI completion, entities tab, engagement-setup wizard, feedback loop) and Phase 10 hybrid ingest — a nice observability polish but shouldn't jump the queue.

**Admin note:** Next Up

_Approved 2026-07-08T20:41:52.966449+00:00 — suggestion id `019f4376-06cd-7ec2-a7ab-c44b10272081`_

### 3. [P2] A "quick add to scope from finding" action is a natural glue between findings, entities, and scope that reinforces the feedback loop, but it materially overlaps with three already-approved roadmap items (#3 quick actions on entities, #4 Found Scope vs. Defined Scope, and Charter Idea 4 Entities tab) and should be folded into that bundle rather than tracked separately.

**Original suggestion:**

> If a finding unearths things that look like entities (IPs, Users, domains, Machines etc) there should be a way to add it to the scope from the finding itself. 

'Quick add' + surface some intel from the finding.

**Pros:**
- Directly serves the Charter's feedback-loop principle — finding → extracted entity → scope → new tasks → more findings is exactly the 'loop keeps turning' flow the North Star calls for.
- Complements already-approved roadmap item #4 (Found Scope section for analyst-discovered entities) by providing the UI entry point that populates it, so this suggestion sharpens rather than competes with that work.
- Complements approved item #3 (quick actions against known Entities) — once an entity is promoted to scope, the quick-actions surface has something to act on.
- Low implementation cost given existing infrastructure: the scope parser (`backend/app/api/scope.py`) already does per-line kind detection (domain/ip/cidr/url/email/org), so a 'quick add' button just needs to feed detected tokens into that existing endpoint.
- No conflict with the 'agents scan, analysts exploit' invariant — this is scope curation by the analyst, not an execution surface.
- Reinforces the 'one pane of glass' North Star by removing a tool-hop (finding → copy value → Scope tab → paste → parse).

**Cons:**
- Heavy overlap with roadmap items #3, #4, and Charter Idea 4 (Entities tab) — should be folded into that bundle as the 'promote-to-scope' action rather than tracked as an independent roadmap entry, or you'll get parallel implementations.
- Entity extraction from finding text is under-specified — regex on summary, structured extraction by the Strategic agent, or analyst-highlight-and-tag? Each has different accuracy/cost tradeoffs and Charter open question #3 already flags this as unresolved.
- Needs a decision on the Defined Scope vs. Found Scope split (per approved item #4) before this ships, or 'quick add' will pollute the client-formal scope with analyst-discovered items.
- 'Surface some intel from the finding' is vague — is it the raw matched token, the surrounding sentence, a Strategic-agent summary, or a link back to the source finding? Needs pinning before design.
- Lower leverage than the four still-unfinished Charter ideas in aggregate (attack-path UI, entities tab proper, engagement-setup wizard, feedback loop) — it's a nice slice of the entity story but shouldn't jump ahead of the Entities tab itself.

**Admin note:** Agreed

_Approved 2026-07-08T20:39:28.177475+00:00 — suggestion id `019f4374-0615-7a71-9d9b-0264c9ae6503`_

### 4. [P3] Routing Suggestion Box AI evaluations through a dedicated, cheap platform key (e.g. GPT-4o) instead of the submitter's BYO key is a sensible consistency + fairness fix, but it introduces a new billing/secret-management surface that needs an owner and a cost cap.

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

### 5. [P3] Per-engagement and per-task model/key selection is a natural extension of BYO keys and the Phase 11 cost engine, and it overlaps meaningfully with already-approved UX-backlog #7 ("per-role multi-model support") — worth folding into that item rather than tracking separately.

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

### 6. [P3] (no summary)

**Original suggestion:**

> Need the ability to run quick actions against known Entities.

_Approved 2026-07-07T19:16:12.276157+00:00 — suggestion id `019f3dfa-1a6c-7a62-9a1f-bfc91630f1cf`_

### 7. [P3] (no summary)

**Original suggestion:**

> Let me add entities we found to the scope under a different section. 

Defined Scope = Things the client provided to us formally, 
Found Scope = Entities we found and should be able to run quick actions on to generate findings.

_Approved 2026-07-07T19:16:11.001484+00:00 — suggestion id `019f3dfb-a7f7-7ad1-adb4-0cdc3e2909a9`_

### 8. [P3] (no summary)

**Original suggestion:**

> Allow analysts to tweak which model they wish to use before doing actions. Either set the default model in the key setting or have per Engagement settings where the models the analyst wants to use are togglable. To find which models belong to which endpoints setup probe endpoints to detect all the current available models for use. Then save those preferences to the user.

_Approved 2026-07-07T19:16:09.658665+00:00 — suggestion id `019f3dfe-40e6-7bb0-9ff5-0dcc1cae3340`_

### 9. [P3] (no summary)

**Original suggestion:**

> When clicking on the engagement, its not immediately clear where you should click to open it. Either make the whole card clickable with a separate clickable + to add or make a tiny clickable 'Enter' 'Start' or 'Continue' or something to make it easy to find.

_Approved 2026-07-07T19:16:07.757638+00:00 — suggestion id `019f3dff-be61-7bb0-8b37-37cc5f698997`_

### 10. [P3] (no summary)

**Original suggestion:**

> More quick actions, make them entity dependent and engagement aware. So if we've already run domain scan, maybe suggest something else? If there are tools available, suggest running tools, etc.

_Approved 2026-07-07T19:16:05.062589+00:00 — suggestion id `019f3e01-01e7-7ef1-b91b-a27a5d7ad0b8`_

### 11. [P3] Moving per-engagement agent settings out of the Scope tab into their own dedicated configuration screen is a sensible separation-of-concerns cleanup that also creates a natural home for several already-approved model/key-selection items, but it's config-surface plumbing that competes with the four still-unfinished Charter ideas.

**Original suggestion:**

> Instead of putting agent settings inside the scope, put it in its on configuration screen per engagement.

**Pros:**
- Cleanly separates two orthogonal concerns — scope defines targets, agent settings define behavior — which matches the Charter's 'whole-page navigation, not nested scrolling' principle and avoids overloading the Scope tab.
- Creates a natural landing surface for already-approved roadmap items #2 (per-engagement/per-task model+key selection) and #5 (per-engagement togglable models with endpoint probing), consolidating three related asks into one screen rather than scattering them.
- Fits well with roadmap item #11's Modes (Simple/Normal/Advanced), since a dedicated Agent Config screen is where the 'Advanced' knobs would live without cluttering the everyday Scope workflow.
- Low risk against the 'agents scan, analysts exploit' invariant — this is pure configuration surface, not an execution path.
- Aligns with Charter Idea 3's Nessus-style setup shape (left sub-nav: Basic / Discovery / Assessment / Advanced), where a dedicated agent-config pane maps naturally to the 'Assessment' or 'Advanced' section.

**Cons:**
- Under-specified — 'agent settings' currently lives in Scope by convention, but the suggestion doesn't say which settings (model selection, key selection, run cadence, task-kind toggles, Strategic on/off) should move, and each has different data-model implications.
- Overlaps meaningfully with approved items #2, #5, and #7 (UX backlog per-role multi-model) — should be folded into those as the delivery surface rather than tracked as an independent roadmap entry, or you'll get parallel implementations.
- Pure config plumbing — doesn't advance any of the four still-unfinished Charter ideas (attack-path UI, entities tab, engagement-setup wizard, feedback loop) or Phase 10 hybrid ingest.
- Adds another destination to the engagement's left nav (already: OSINT · Vuln Scan · Exploit · Phishing · Results · Costs · Observations), which needs a naming and placement decision before it grows the shell further.
- Needs a resolution-order call (engagement agent config → user default → org fallback) that item #2 already flagged as unresolved — this suggestion inherits that same open question and can't ship cleanly until it's settled.

**Admin note:** Hell Yea

_Approved 2026-07-08T20:37:39.617724+00:00 — suggestion id `019f436b-c5b4-7cf2-98b7-2fc0e4c7b780`_

### 12. [P4] A bundle of three related asks — scope-aware run suggestions on engagement start, visible run→finding feedback, and an onboarding tutorial + tooltips — that collectively target the "disconnect" analysts feel between agent runs and the findings-first loop; individually strong, but should be split before landing.

**Original suggestion:**

> So a few things;

When starting a new engagement, it asks for a prompt? Why not just read from the current scope document and provide a list of starting suggestions to see what should run first. Second theres visible way for agent runs to become findings. It's not immediately apparent whats happening in the background when a run happens, so a little more context or help to understand whats going on in the background + more fleshed out results. Currently when you execute a run you're not given any feedback as to what occured on that run, no confirmation that there were any new findings automatically added, no way to review what the result of the run was, no way to understand what actually happens anyway so it all gets lost in translation. 

A tutorial should explain to an analyst how to get started, how to generate your first few findings, the overall feedback loop of generating findings, doing runs, validating, generating new findings all in one neat presentation. Questions lead to answers lead to more questions, and that slowly fills out the engagement. Right now it feels like a disconnect. While on the topic of tutorials, provide tooltips for the core functionality explaining how to use it.

**Pros:**
- Scope-derived starting suggestions align tightly with Charter Idea 3 (Nessus-style setup → auto-launch OSINT) and with roadmap item #5's decoupled 'Save' + 'Start' lifecycle — reading the already-parsed scope to seed first-run options is a natural fit for the Strategic agent, which is exactly its Phase 9 role.
- Making run→finding causality visible directly serves the North Star's feedback-loop principle ('found → finding → tasks → table updates') that HANDOFF calls out as still feeling disconnected in practice.
- Heavy overlap with already-approved Status Tab Cleanup items (#8 per-run synopsis, #9 run-ID toast, #14 step-by-step execution log) means the run-visibility half of this ask is largely in-flight — this suggestion mostly sharpens the acceptance criteria with a concrete analyst pain point ('no confirmation new findings were added').
- A 'this run produced N new findings' toast/link is cheap given `agent_executions` and the SSE-to-cache bridge from v1.0.0 already exist — it's a serializer + toast, not new infrastructure.
- The tutorial/tooltip ask pairs naturally with approved item #11 (skill-level Modes — Simple/Normal/Advanced), since 'Simple' mode is essentially guided onboarding; bundling them avoids building two parallel handholding surfaces.

**Cons:**
- This is really three suggestions in one (scope-to-suggestions, run feedback, tutorial+tooltips) — admin should split them before roadmap entry or tracking will get muddled and duplicate existing Status Tab Cleanup items.
- The run-feedback half substantially duplicates roadmap items #8, #9, and #14 (all admin-noted 'Status Tab Cleanup'); re-approving as a standalone entry risks a fourth parallel tracking record for the same workstream.
- 'Prompt on new engagement' framing suggests the current engagement-setup flow drifted from Charter Idea 3's Nessus-style form — worth confirming with Nasir/Ken whether the prompt is intentional or a regression before designing scope-derived suggestions on top of it.
- Auto-generating starting suggestions from scope needs a clear boundary against the 'agents scan, analysts exploit' invariant — suggestions must stay in enum/scan territory, which Strategic already enforces but should be explicitly restated for this entry point.
- Tutorial + tooltips are pure onboarding polish and compete for attention with four still-unfinished Charter ideas (attack-path UI, entities tab, engagement-setup wizard, feedback loop) and in-progress Phase 10 hybrid ingest — high value for new analysts, but low leverage against the current build order.
- Tooltip content is a maintenance tax that decays quickly as the UI evolves (v1.0.0 just restructured the data layer) — needs an owner and a review cadence, or it will go stale and mislead the analysts it's meant to help.

**Admin note:** Quick Start Guide

_Approved 2026-07-07T19:46:25.416760+00:00 — suggestion id `019f1ea0-2127-7ac1-9007-c323da232945`_

### 13. [P4] (no summary)

**Original suggestion:**

> In relation to AI keys, 

1. A test button. allow us to test the key and endpoint to see if they're alive
2. Instead of asking me which model I want to use, just tap the endpoint provided to see all the models. usually they have them at /models/ for some providers so it fills in dynamically. It would future proof for model changes/endpoint changes etc
3. Maybe build a chatbot that uses your keys, kinda designed like a small terminal input to help navigate the site + manage the engagement live.

_Approved 2026-07-02T16:58:53.646161+00:00 — suggestion id `019f1eae-eeef-7712-9f1e-77e6c73203f6`_

### 14. [P5] Admin-driven user/guest lifecycle management is a reasonable platform-hygiene ask, but it sits outside the Charter's North Star (analyst workflow / findings loop) and partially duplicates capabilities Entra already provides in a single-tenant deployment.

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

### 15. [P8] (no summary)

**Original suggestion:**

> Modes to cater to analyst skill levels.

Simple: Agent handholding, plug and play, very little customization (Baby's first engagement)
Normal: Some handholding, some customizations, just the every day experience. 
Advanced: Customizations for every aspect of the experience.

_Approved 2026-07-02T16:58:55.862818+00:00 — suggestion id `019f1eaa-139e-7ba2-af03-5422cf1aae48`_

### 16. [P9] (no summary)

**Original suggestion:**

> Graphspy integration pls

_Approved 2026-07-02T16:58:33.698557+00:00 — suggestion id `019f1f26-d5ee-73d3-95a0-d98ecbc5fe57`_

### 17. [P9] (no summary)

**Original suggestion:**

> We need a mobile version of the Dashboard for phones and portable devices like tablets.

_Approved 2026-07-08T20:37:44.436769+00:00 — suggestion id `019f3ed6-0c37-7e30-b37f-f750ad07f51a`_

### 18. [unranked] Adding an "Out of Scope / Outside RoE" finding state with report-omission is a low-cost, high-value extension of the existing validation workflow that fills a real engagement-hygiene gap.

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

### 19. [unranked] This suggestion is already approved and on the roadmap (id `019f1536-4ab3-7403-9e77-3a493aa11cf4`, admin-noted "do it nao") — adding submitter/approver attribution to the Suggestion Box feedback flow.

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

### 20. [unranked] Linking observations to specific findings (and surfacing those back-references on the finding view) is a natural, low-cost extension of the existing Phase 8e observations system that strengthens the feedback-loop principle, but it overlaps with the proposed Entities tab and the recently-approved tagging/correlate work and needs a clear data-model decision before coding.

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

### 21. [unranked] Reformatting the "What's New" surface to hide infra-only changes (Deploy/CLI/Images) and group entries by user-facing categories (Bug Fixes / Features / QoL / Ops) is a low-cost polish that improves the release-notes experience but is pure presentation work with no Charter or roadmap leverage.

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

### 22. [unranked] Splitting the finding view into a lightweight slide-over (quick info + quick actions) plus a full-page detail view is a natural evolution of the Phase 9 slide-over that's becoming overloaded, and it aligns cleanly with the Charter's "whole-page navigation, not nested scrolling" principle.

**Original suggestion:**

> Give us a full screen view of the finding, I appreciate the blade, but now its becoming too cluttered. I'd like the quick info + quick actions to be in a blade, but If I need to do anything more It should expand into its own full pane. Similar to how in sentinel you get an incident preview, then you open the full page it gives you the full details with more options.

**Pros:**
- Directly reinforces the Charter's 'whole-page navigation, not nested scrolling' guiding principle — the slide-over is exactly the kind of stacked panel the North Star pushes back against once it grows past quick-glance content.
- The slide-over is visibly accreting scope: summary editor, attachment grid, Phase 9 suggestions with Analyst/Agent buttons, and approved-but-not-shipped work (per-finding activity log, AI-rewrite diff UI, out-of-scope status, observation back-refs, correlate/tag actions) all want to live there — a full-page view gives that work somewhere to land without further cluttering the blade.
- The Sentinel incident-preview → full-page pattern is a well-understood analyst mental model and maps cleanly onto the existing 'click finding → slide-over' entry point, so this is an additive route (e.g. `/e/{slug}/findings/{id}`) rather than a rewrite.
- Low infra cost given v1.0.0 — TanStack Query cache, SSE-to-cache bridge, and nav prefetch already make a dedicated page fast and stale-free, and the finding data model (Phase 8a) is already first-class enough to address by ID.
- Creates a natural home for several unfinished Charter ideas — Idea 2's full attack-path Paths+Steps UI, and the entity/observation back-references from Idea 4 — that never fit comfortably in a slide-over.
- No conflict with the 'agents scan, analysts exploit' invariant — this is a view-layer split, not an execution-surface change.

**Cons:**
- Charter Idea 2 explicitly leans slide-over ('so the findings table stays visible behind it') and lists box-vs-window-vs-page as Open Question #1 — this suggestion effectively answers that question and should be flagged to Nasir/Ken for an explicit call before coding.
- Scope boundary between blade and full page is under-specified — needs a crisp rule for what stays in the blade (quick info + quick actions only?) vs. what only exists on the full page (summary editor, attachments, suggestions, activity log, attack paths), or the blade will re-accrete over time.
- Materially overlaps with in-flight Phase 9 attack-path work — the full-page finding view is arguably where 'Path B: … [HIGH]' with ordered Steps belongs, so this should be coordinated with that effort rather than shipped as a standalone reskin.
- Adds a new route + deep-link/back-nav/breadcrumb design surface (page ↔ blade ↔ findings table), which is more product design than the one-paragraph ask suggests.
- Competes with higher-leverage unfinished work — Phase 10 hybrid ingest, entities tab (Idea 4), engagement-setup wizard (Idea 3), and the P1/P2 roadmap items — so worth sequencing behind or bundling with the Phase 9 attack-path completion rather than jumping the queue.

_Approved 2026-07-08T22:47:26.837585+00:00 — suggestion id `019f4377-b6d1-7382-8dce-15459b07420e`_

### 23. [unranked] A "N new findings added" toast after an agent run is a small, high-leverage observability fix that closes a specific pain point already flagged in approved bundle #12, and should almost certainly ship as part of the Status Tab Cleanup workstream rather than as a standalone item.

**Original suggestion:**

> If a new finding was added, give me a toast notification after an agent run to let me know it added something.

**Pros:**
- Directly serves the Charter's feedback-loop principle by making the 'run → new findings on the table' step visible instead of silent, which HANDOFF/roadmap #12 explicitly calls out as a current disconnect ('no confirmation that there were any new findings automatically added').
- Very low implementation cost given existing infrastructure — v1.0.0's SSE-to-cache bridge and TanStack Query cache already surface new findings client-side, and `agent_executions` + the run-ID toast from shipped Status Tab Cleanup item `019f1a48-...` give a natural place to attach a delta count.
- Fits cleanly inside the already-approved Status Tab Cleanup bundle (roadmap items #8 per-run synopsis, #9 run-ID toast, #14 step-by-step execution log, and #12's run-feedback half) — this suggestion sharpens the acceptance criteria rather than opening new scope.
- Reinforces the 'one pane of glass' North Star — analysts stop tabbing between Status and Findings to confirm a run actually produced something.
- No conflict with the 'agents scan, analysts exploit' invariant — this is a notification surface, not an execution path, and all resulting findings still land as pending_validation per Phase 8a.

**Cons:**
- Duplicates the run-feedback half of already-approved roadmap item #12 ('no confirmation that there were any new findings automatically added') and overlaps with #8/#9 — should be folded into that bundle rather than tracked as a separate roadmap entry, or you'll get a fifth parallel record for the same workstream.
- Under-specified on click behavior — does the toast deep-link to the new findings, filter the Findings table to just the run's output, or open the first new finding's slide-over? Each has different UX and routing implications.
- Needs a rule for zero-finding runs — silent, a neutral 'run complete, no new findings' toast, or rolled into the failed/empty-run visual treatment from shipped item `019f1ea2-...` — otherwise analysts will still wonder whether the run actually finished.
- Toast-only notifications are ephemeral; without a persistent counterpart (e.g. the per-run synopsis in Status, or approved item #17's persistent approval notifications pattern), an analyst who misses the toast has no recovery path.
- Pure observability polish — doesn't advance any of the four still-unfinished Charter ideas (attack-path UI, entities tab, engagement-setup wizard, feedback loop proper) or Phase 10 hybrid ingest, though its cost is small enough that leverage-per-hour is still favorable.

_Approved 2026-07-08T23:01:31.360011+00:00 — suggestion id `019f43ee-c72b-7203-82b5-22871adb1f01`_

### 24. [unranked] A "this run already produced findings" indicator on the Status page is a small, high-leverage observability fix that fits squarely inside the already-approved Status Tab Cleanup / run→finding causality bundle rather than standing alone.

**Original suggestion:**

> If an agent run already yielded a finding, make it say that in the status page or something so we don't re-run the same agent runs over and over.

**Pros:**
- Directly serves the Charter's feedback-loop North Star by making the run→finding relationship visible where the analyst decides what to run next.
- Very cheap given existing infrastructure — `agent_executions`, the finding-created events, and the v1.0.0 SSE-to-cache bridge already carry everything needed to badge a run with 'produced N findings'.
- Reduces wasted LLM spend and duplicate agent work, which reinforces the Phase 11 Costs tab investment.
- Reinforces the 'one pane of glass' principle by keeping the 'should I re-run this?' decision on the Status view instead of forcing a hunt through the Findings tab.
- Overlaps cleanly with already-approved roadmap items #8 (per-run synopsis), #9 (visible run→finding feedback), #10 (engagement-aware quick actions that suggest something else if a run already happened), and shipped #019f1a44 — should be folded into that bundle.

**Cons:**
- Substantial duplicate of already-approved roadmap item #12's run-feedback half and prior Status Tab Cleanup entries — risks becoming a fourth parallel tracking record for the same workstream.
- Under-specified on scope: does 'already yielded a finding' mean any finding ever from that tool+target pair, only findings from that specific run, or a dedup by (tool, target, engagement)? Each has different data-model implications.
- Doesn't advance any of the four still-unfinished Charter ideas (attack-path UI, entities tab, engagement-setup wizard, feedback loop proper) or Phase 10 hybrid ingest, so it should ride inside the Status Cleanup bundle rather than jump the queue.
- Needs a UI decision (badge on the run row, disabled 'Run again' button, or a soft warning toast) that should be settled alongside item #10's engagement-aware quick actions to avoid two inconsistent 'already ran this' surfaces.

_Approved 2026-07-08T23:01:29.689604+00:00 — suggestion id `019f43f0-8d57-78a0-9f74-049facea52ed`_

### 25. [unranked] Adding filters + table/card view toggle to the Status screen is a reasonable observability polish that sits squarely inside the already-approved "Status Tab Cleanup" bundle and should be folded into that workstream rather than tracked as an independent roadmap item.

**Original suggestion:**

> Custom Filter options in the status screen. Table view vs Card View... let filter for all Strategic runs, Filter for Tactical runs, Filter various other parameters.. etc

**Pros:**
- Fits naturally with the in-flight Status Tab Cleanup bundle (shipped items 019f1a44/019f1a48/019f23c3 and approved run→finding causality work #12), so this sharpens acceptance criteria rather than opening a new workstream.
- Reinforces the 'one pane of glass' North Star — analysts can slice Strategic vs. Tactical runs, failed vs. successful, per-finding, etc., without hunting across sub-tabs.
- Low implementation cost given v1.0.0's TanStack Query data layer and the existing `agent_executions` table already carrying agent kind, status, model, and finding_id — filters are largely a query-param + UI change, not new infrastructure.
- A table view pairs well with the Charter's 'findings as a clickable table' aesthetic (Idea 1) and gives power users a scannable surface, while cards remain the friendlier default — consistent with the platform's existing design language.
- No conflict with the 'agents scan, analysts exploit' invariant — this is pure view-layer filtering over already-recorded executions.

**Cons:**
- Overlaps materially with the already-approved Status Tab Cleanup items (per-run synopsis #8, run-ID toast #9, step-by-step log #14, failed/empty run visual distinction shipped 2026-07-07) — should be merged into that bundle to avoid a parallel tracking record.
- Filter dimensions are under-specified ('various other parameters') — needs an explicit list (agent kind, status, model, date range, finding_id, dispatch_method?) before design, or the filter bar will grow unbounded.
- Table vs. card is a design-language decision that affects more than just Status (findings, entities, observations all have similar tension) — worth a broader call with Nasir/Ken rather than settling it inside one tab.
- Pure observability polish — doesn't advance any of the four still-unfinished Charter ideas (attack-path UI, entities tab, engagement-setup wizard, feedback loop) or in-progress Phase 10 hybrid ingest, so it competes for attention with higher-leverage work.
- Filter state persistence (URL params vs. per-user preference vs. session-only) is an unresolved sub-question that will bite on first review if not decided up front.

_Approved 2026-07-08T23:01:28.146372+00:00 — suggestion id `019f43f2-2b3d-70d1-aee2-8389c9255422`_

### 26. [unranked] Adding time-of-day (not just date) to finding timestamps with a per-user UTC/local toggle is a small, sensible precision fix that supports audit and activity-log work, but it's UI-layer polish that doesn't advance any unfinished Charter idea and needs a clear per-user preference story before coding.

**Original suggestion:**

> Timestamp as well as dates in findings. Can be set to UTC, or Local based on user settings or preferences

**Pros:**
- Directly supports the already-approved per-finding activity log (roadmap #2 / UX backlog #2), which will be unreadable if events only carry dates — timestamps are a prerequisite for 'Ken initiated crt.sh - datetime'.
- Low implementation cost — the backend audit log, agent_executions, and finding rows already store full UTC timestamps; this is largely a serializer/formatter and a user-settings toggle, not a data-model change.
- Improves audit defensibility across the platform (finding.updated, attachment uploads, suggestion approvals) — pairs naturally with the submitter/approver attribution work (id `019f1536-...`) that's already queued.
- Per-user UTC vs. local preference fits the existing Settings page pattern established by BYO provider keys, so there's a natural home for the toggle without new surface area.
- No conflict with the 'agents scan, analysts exploit' invariant — this is pure display/preference work.

**Cons:**
- Pure display polish — doesn't advance any of the four still-unfinished Charter ideas (attack-path UI, entities tab, engagement-setup wizard, feedback loop) or Phase 10 hybrid ingest.
- Under-specified scope: 'findings' alone, or all timestamped surfaces (findings, observations, tasks, agent runs, suggestions, audit log, What's New)? — inconsistent formatting across the portal is worse than the current date-only state.
- Needs a decision on storage vs. display — canonical UTC in DB (already the case) with client-side rendering is the clean answer, but the suggestion phrasing ('can be set to UTC or Local') could be misread as storing local time, which would corrupt cross-user audit trails.
- Introduces a user-preferences surface that doesn't exist yet beyond BYO keys — timezone, 12h/24h, ISO vs. relative ('2h ago') are all adjacent asks that will land next and should be scoped together rather than piecemeal.
- Competes for attention with higher-leverage P1/P2 roadmap items (AI-rewrite of finding summaries, activity log, quick-add-to-scope) that would benefit from timestamps but shouldn't be blocked on this if it grows.

_Approved 2026-07-08T23:01:26.207132+00:00 — suggestion id `019f43f4-21b1-77e1-875d-3fd1414ab83d`_

### 27. [unranked] A concrete UX refinement of the already-approved model-probe work (roadmap #13) and per-engagement model selection (#5, #8) — replace the free-text model input in the default-model selector with a dropdown populated by probing the provider endpoint.

**Original suggestion:**

> In the default model selector, USE THE PROBE and LET ME PICK FROM A LIST. Don't make me type it in.

**Pros:**
- Sharpens already-approved roadmap item #13.2 (probe the endpoint's /models to enumerate available models) with a specific UX requirement — dropdown, not typed input — so this is scope refinement rather than net-new work.
- Directly serves the 'analyst in control' principle by removing a footgun: typo'd model names silently route to unpriced or nonexistent models and break Phase 11 cost attribution (`_RATE_TABLE` substring matching in `pricing.py`).
- Complements approved items #5 and #8 (per-engagement/per-task model selection, togglable model preferences) — a probe-backed picker is the obvious input control for those settings screens.
- Low implementation cost given BYO keys infrastructure already exists; this is a probe endpoint + a Select component, not a new subsystem.
- No conflict with the 'agents scan, analysts exploit' invariant — pure settings/UX plumbing.

**Cons:**
- Duplicates part of approved item #13 — should be folded into that entry as an acceptance criterion, not tracked as a separate roadmap row, or you'll get two parallel implementations of the same probe.
- Providers vary: OpenAI-compatible endpoints expose `/models`, but Azure OpenAI (deployment names) and some local runtimes don't follow the same shape — needs a per-provider probe strategy with a graceful fallback to free-text when probing fails.
- Under-specified failure UX: what happens when the key is invalid, the endpoint is unreachable, or the provider returns hundreds of models (OpenRouter-style)? Needs pinning before coding.
- Probe results should probably be cached per-key to avoid hammering provider endpoints on every settings-page render — small but real infra decision (TTL, invalidation on key edit).
- Pure settings-surface polish — doesn't advance any of the four still-unfinished Charter ideas (attack-path UI, entities tab, engagement-setup wizard, feedback loop) or Phase 10 hybrid ingest.

_Approved 2026-07-08T23:01:24.525446+00:00 — suggestion id `019f43f4-ef2b-7791-a9f5-5433a0335c56`_

## Shipped

Approved items that have landed. Kept here (not deleted) so the roadmap doubles as a running changelog.

- **2026-07-07** — Adding scope-derived quick-action chips (e.g. "Enum domain X", "nmap IP Y") next to the Scope tab's Start-a-run prompt is a low-cost analyst accelerator that reinforces the feedback loop, but it overlaps with the Strategic agent's suggestion surface and needs a clear rule for which actions are agent-eligible vs. analyst-only. (suggestion id `019f390f-cedd-7df0-8be8-e2f4db7a99ba`)
- **2026-07-07** — A tag-driven "Correlate Findings" action is a lightweight way to surface relationships between findings and dovetails with the planned Entities tab, but it overlaps with already-backlogged tagging and entity-correlation work and needs a clearer definition of what "correlate" produces. (suggestion id `019f157e-cf54-7af2-ab3f-a2a765ea8016`)
- **2026-07-07** — Visually distinguishing failed/empty agent runs from successful ones is a small, high-value observability fix that fits squarely inside the already-approved Status Tab Cleanup bundle rather than standing alone. (suggestion id `019f1ea2-3abd-7e01-a498-c7a19b630087`)
- **2026-07-06** — This is a duplicate of an already-approved P1 roadmap item ("Findings Cleanup", id `019f2472-...`) requesting a centered modal for manually adding findings with typed fields, dropdowns, and a date picker. (suggestion id `019f2472-e644-7411-a38d-fc3db03f2d01`)
- **2026-07-02** — Decoupling engagement creation from auto-run is a sensible refinement of Charter Idea 3 that fits naturally with Phase 10's import-first model, though it softens the "Save and start" feel Nasir originally described. (suggestion id `019f159c-6630-7323-9e46-7374ef4dd4e9`)
- **2026-07-02** — Burp Suite Pro XML ingest is a natural fit for Phase 10's hybrid import-first model and would extend the existing finding importer with a real scanner format analysts actually use. (suggestion id `019f1532-8918-7d02-8cd3-a58b20414d98`)
- **2026-07-02** — In status; (suggestion id `019f1a44-d1b2-7153-8456-90a33073abbc`)
- **2026-07-02** — This suggestion is already approved and on the roadmap (id `019f1a48-d786-79a1-acbc-8fce8fae5859`, admin-noted "Status Tab Cleanup") — adding unique run IDs with a trackable toast on agent run kickoff. (suggestion id `019f1a48-d786-79a1-acbc-8fce8fae5859`)
- **2026-07-02** — A per-run step-by-step execution log in the expanded Status view is a natural extension of the already-approved Status Tab Cleanup work and directly serves observability needs, though it overlaps enough with those items that it should be scoped as part of that bundle rather than a standalone effort. (suggestion id `019f23c3-877f-78c3-8cbf-aaef6a613cfd`)
