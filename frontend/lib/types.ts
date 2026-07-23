// Wire-format types that match the Pydantic schemas in app/schemas/*.

export type EngagementStatus = "active" | "archived" | "flushed";
export type EngagementArchitecture = "legacy" | "v3";
export type EngagementPhase = "baseline" | "exploration";
export type IntelligenceMode =
  | "strategy"
  | "analysis"
  | "ideation"
  | "coverage_review";

export type APIKeyScope = "viewer" | "cli" | "admin";

// What GET /api-keys/me returns. The viewer calls this per Source to learn
// the key's scope so it can render mutation surfaces conditionally.
export interface APIKeyInfo {
  id: string;
  name: string;
  scope: APIKeyScope;
  created_by: string | null;
  revoked_at: string | null;
  last_used_at: string | null;
  created_at: string;
}

// Per-(engagement, tool) standing session grant. A row with revoked_at=null is
// active and the gate auto-approves matching active calls.
export interface Authorization {
  id: string;
  engagement_id: string;
  tool_name: string;
  granted_by: string | null;
  note: string | null;
  revoked_at: string | null;
  revoked_by: string | null;
  created_at: string;
  updated_at: string;
}
export type ScopeKind = "domain" | "cidr" | "ip" | "url";
export type RiskLevel = "passive" | "active" | "destructive";
export type ApprovalStatus =
  | "pending"
  | "approved"
  | "denied"
  | "edited"
  | "auto";

export type EngagementTimeFrame =
  | "repeatable"
  | "point_in_time_continuous"
  | "point_in_time"
  | "custom";

export interface Engagement {
  id: string;
  name: string;
  slug: string;
  description: string | null;
  status: EngagementStatus;
  work_state: "active" | "completion_review" | "completed";
  work_state_version: number;
  auto_assess_enabled: boolean;
  intelligence_architecture: EngagementArchitecture;
  converted_to_v3_at: string | null;
  phase: EngagementPhase;
  baseline_completed_at: string | null;
  methodology_id: string | null;
  methodology_slug: string | null;
  methodology_version: number | null;
  methodology_selected_at: string | null;
  time_frame: EngagementTimeFrame;
  start_date: string | null;
  end_date: string | null;
  created_by: string | null;
  archived_at: string | null;
  flushed_at: string | null;
  created_at: string;
  updated_at: string;
  // v1.4.5: scope quick-actions. Optional so existing fixtures / cached
  // payloads from before the field shipped don't break the type.
  scope_count?: number;
  exclusion_count?: number;
  // v2.4.0: whether the engagement has a `state = current` strategy
  // revision. Used to derive isPendingEngagement() on the frontend.
  has_strategy?: boolean;
}

export interface MethodologyRead {
  id: string;
  slug: string;
  version: number;
  name: string;
  description: string | null;
  source_url: string | null;
  node_count: number;
}

export interface IntelligenceConversionResponse {
  engagement_id: string;
  intelligence_architecture: EngagementArchitecture;
  converted_to_v3_at: string | null;
  methodology_id: string | null;
  phase: EngagementPhase;
  seeded_memory_element_ids: string[];
  already_converted: boolean;
}

export interface IntelligenceRunResponse {
  execution_id: string;
  mode: IntelligenceMode;
  status: "pending" | "running" | "completed" | "failed" | "cancelled";
  parsed: Record<string, unknown> | null;
  error: string | null;
}

export interface ScopeItem {
  id: string;
  engagement_id: string;
  kind: ScopeKind;
  value: string;
  is_exclusion: boolean;
  note: string | null;
  // v1.4.13: provenance (roadmap #5). "defined" = client-provided,
  // "found" = added from findings.
  source?: string;
  created_at: string;
  updated_at: string;
}

export interface Approval {
  id: string;
  engagement_id: string;
  thread_id: string;
  node: string | null;
  tool_name: string;
  tool_args: Record<string, unknown>;
  risk: RiskLevel;
  scope_check: Record<string, unknown>;
  status: ApprovalStatus;
  decided_by: string | null;
  decision_args: Record<string, unknown> | null;
  authorization_id: string | null;
  decided_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ApprovalInboxItem extends Approval {
  engagement_slug: string;
  engagement_name: string;
}

export type Severity = "info" | "low" | "medium" | "high" | "critical";

export type FindingPhase =
  | "osint"
  | "vuln_scan"
  | "exploit"
  | "phishing"
  | "general";

export type FindingValidationStatus =
  | "pending_validation"
  | "validated"
  | "rejected"
  | "false_positive"
  | "needs_review";

// v1.4.0: analyst-set reportability marker, orthogonal to
// FindingValidationStatus. `null` = default (included in report).
// `out_of_scope` = real but not in the client-declared scope;
// `outside_roe` = real but off-limits per the engagement's rules of
// engagement / legal terms. Both keep the row visible in the Findings
// tab (dimmed + badge); the report exporter drops them when the Report
// tab's "Omit excluded" toggle is on.
export type FindingExclusion = "out_of_scope" | "outside_roe";

// Persisted finding as returned by GET /engagements/{slug}/findings. Mirrors
// the SSE `finding.created` event's tool/args/data so the table can render
// hydrated and live findings the same way.
export interface Finding {
  id: string;
  thread_id: string | null;
  tool: string | null;
  target: string | null;
  args: Record<string, unknown>;
  data: Record<string, unknown>;
  severity: Severity;
  title: string;
  summary?: string | null;
  phase: FindingPhase;
  status: FindingValidationStatus;
  exclusion?: FindingExclusion | null;
  // v1.4.0 (part 2): Nessus-style ingest grouping. `group_key` is the
  // stable identity that lets re-runs of the same tool against the
  // same target fold into one row. `item_count` is 0 for legacy
  // per-hit rows, N for grouped rows where `data.items` holds N
  // per-hit records.
  group_key?: string | null;
  item_count?: number;
  validated_at: string | null;
  observed_at: string | null;
  burp_serial_number: string | null;
  created_at: string;
  // v1.4.7: free-form analyst tags (correlate / filter foundation).
  tags?: string[];
}

// v1.4.0: body for POST /engagements/{slug}/findings — the manual
// "Add finding" modal. Only `title` is required.
export interface FindingCreate {
  title: string;
  summary?: string | null;
  severity?: Severity;
  phase?: FindingPhase;
  target?: string | null;
  observed_at?: string | null;
  tags?: string[];
}

// v1.4.0: one cluster proposed by the CorrelateAgent. `finding_ids` first
// entry is the proposed parent (survives the merge); rest are children.
export interface CorrelateGroup {
  rationale: string;
  finding_ids: string[];
}

export interface CorrelateResponse {
  groups: CorrelateGroup[];
  total_considered: number;
}

// v1.4.1: deterministic auto-grouping. Preview surfaces every set of
// ungrouped rows that share a proposed group_key; apply commits the
// analyst's approvals.
export interface RegroupProposal {
  group_key: string;
  tool: string;
  proposed_title: string;
  member_ids: string[];
  projected_severity: Severity;
  projected_item_count: number;
  absorbs_into_existing_parent_id: string | null;
}

export interface RegroupPreview {
  proposals: RegroupProposal[];
  scanned_row_count: number;
  ungroupable_count: number;
}

export interface RegroupApplyResult {
  group_key: string;
  parent_id: string;
  absorbed_member_count: number;
  final_item_count: number;
  final_severity: Severity;
}

// v1.4.3: response of POST /findings/repair-groups. Non-destructive
// maintenance pass over an engagement's grouped rows.
export interface RepairGroupsResult {
  parents_scanned: number;
  parents_items_repaired: number;
  parents_rekeyed: number;
  parents_merged: number;
  ungrouped_absorbed: number;
  total_items_after: number;
}

// Sort order for GET /engagements/{slug}/findings?sort=…
export type FindingSort = "newest" | "severity" | "observed";

// Captured SSE event for the Status tab's Live events panel (v0.8.2).
// Defined here so both the engagement-page subscriber (which captures
// the event) and the StatusView (which renders it) can import the shape
// without circular component imports.
export interface LoggedEvent {
  sseId: string;
  receivedAt: number;
  event: RunEvent;
}

// ── Status tab (v0.8.0) ───────────────────────────────────────────────────

export type StatusColor = "active" | "pending" | "completed" | "failed";
export type StatusKind = "agent" | "task" | "approval";
// v1.2.0: sub-outcome nuance under the four colours. null on
// still-running / pending rows.
export type StatusOutcome = "success" | "empty" | "partial" | "errored";

export interface StatusTransition {
  status: StatusColor;
  raw_status: string;
  at: string;
}

export interface StatusEntity {
  id: string;
  kind: StatusKind;
  title: string;
  subtitle: string | null;
  color: StatusColor;
  raw_status: string;
  started_at: string | null;
  completed_at: string | null;
  retryable: boolean;
  finding_id?: string | null;
  work_item_id?: string | null;
  task_id?: string | null;
  log: Record<string, unknown>;
  history: StatusTransition[];
  // v1.2.0
  run_slug: string;
  outcome: StatusOutcome | null;
  synopsis: string | null;
}

export interface EngagementStatusResponse {
  agents: StatusEntity[];
  tasks: StatusEntity[];
  approvals: StatusEntity[];
}

// v1.2.0: one line in the per-entity step log. Newest last.
export interface StepEntry {
  at: string;
  kind: string;
  label: string;
  detail: Record<string, unknown> | null;
}

export interface StepLogResponse {
  steps: StepEntry[];
  truncated: boolean;
}

// One entry in a finding's immutable summary history. Newest first.
// `findings.summary` on the parent row is the denormalized cache of the
// latest entry's body (for the Report tab / JSON export).
export interface FindingSummaryEntry {
  id: string;
  finding_id: string;
  body: string;
  author_user_id: string | null;
  author_email: string | null;
  author_display_name: string | null;
  created_at: string;
}

// v0.21.0 (finding pane): one row in the finding's activity timeline.
export interface FindingActivityEntry {
  ts: string | null;
  kind: string;
  label: string;
  actor: string | null;
  detail: string | null;
  ref_type: string | null;
  ref_id: string | null;
}

export type FindingChatRole = "user" | "assistant" | "system";

export type FindingChatActionType =
  | "next_step"
  | "tag_incident"
  | "add_finding"
  | "run_tool"
  | "context";

export interface FindingChatAction {
  type: FindingChatActionType;
  title: string;
  description?: string;
  params?: Record<string, unknown>;
  status?: "proposed" | "accepted" | "denied";
  result?: Record<string, unknown>;
}

export interface FindingChatActionPayload {
  actions?: FindingChatAction[];
}

export interface FindingChatMessage {
  id: string;
  conversation_id: string;
  role: FindingChatRole;
  content: string;
  action_payload: FindingChatActionPayload | null;
  execution_id: string | null;
  created_at: string;
}

export interface FindingChatState {
  conversation_id: string | null;
  messages: FindingChatMessage[];
}

export interface FindingChatResponse {
  conversation_id: string;
  user_message: FindingChatMessage;
  assistant_message: FindingChatMessage;
  execution_id: string | null;
}

export interface FindingChatActionResponse {
  message: FindingChatMessage;
  action_index: number;
  action_type: FindingChatActionType;
  status: "accepted" | "denied";
  result: Record<string, unknown>;
}

// Payload for POST /engagements/{slug}/findings/import
export interface FindingImport {
  title: string;
  severity?: Severity;
  phase?: FindingPhase;
  summary?: string;
  target?: string;
  source_tool?: string;
  details?: Record<string, unknown>;
  observed_at?: string | null;
  burp_serial_number?: string | null;
}

// Response shape for POST /engagements/{slug}/findings/import/nessus
// (Phase 10 — .nessus v2 XML upload).
export interface NessusImportResult {
  imported: Finding[];
  skipped_info: number;
  skipped_out_of_scope: number;
  total_items: number;
}

// Response shape for POST /engagements/{slug}/findings/import/burp
// (v0.7.0 — Burp Pro Issue Export XML upload).
export interface NmapImportResult {
  imported: Finding[];
  total_ports: number;
  skipped_closed: number;
  skipped_out_of_scope: number;
  observed_at: string | null;
}

export interface BurpImportResult {
  imported: Finding[];
  skipped_info: number;
  skipped_out_of_scope: number;
  skipped_duplicate: number;
  total_items: number;
  export_time: string | null;
}

export type ScannerImportSource = "nessus" | "burp" | "nmap";

export interface ScannerScopeReason {
  code: string;
  count: number;
  message: string;
}

export interface ScannerPreviewGroup {
  selection_key: string;
  title: string;
  severity: Severity;
  phase: FindingPhase;
  item_count: number;
  target_count: number;
  targets: string[];
  targets_truncated: boolean;
  scope_decision: string;
  scope_reasons: ScannerScopeReason[];
  in_scope_item_count: number;
  out_of_scope_item_count: number;
  duplicate_state: "new" | "partial" | "existing";
  duplicate_item_count: number;
  default_selected: boolean;
}

export interface ScannerImportPreview {
  source: ScannerImportSource;
  file_sha256: string;
  total_source_rows: number;
  groups: ScannerPreviewGroup[];
  counts: Record<string, number>;
  parser_counts: Record<string, number>;
  // v2.7.0: false for Burp — the wizard should let the analyst select
  // out-of-scope groups and the backend will commit them anyway.
  scope_enforced?: boolean;
}

export interface ScannerImportCommitResult {
  source: ScannerImportSource;
  file_sha256: string;
  selected_group_count: number;
  selected_item_count: number;
  skipped_out_of_scope: number;
  skipped_duplicate: number;
  imported: Finding[];
  parser_counts: Record<string, number>;
}

// Phase 10 — stored entities (Maltego import target + future sources).
// Complements the existing derived-from-findings Entity (above).
export interface StoredEntityFindingRef {
  id: string;
  title: string;
  tool: string | null;
  severity: Severity;
  phase: FindingPhase;
  status: FindingValidationStatus;
}

export interface StoredEntityGroupRef {
  id: string;
  canonical_entity_id: string | null;
  label: string | null;
  member_count: number;
  suppressed_member_count: number;
  row_version: number;
}

export interface StoredEntity {
  id: string;
  type: string;
  value: string;
  normalized_value: string;
  properties: Record<string, unknown>;
  source_tool: string;
  source_attribution: string | null;
  finding_refs: StoredEntityFindingRef[];
  group: StoredEntityGroupRef | null;
  suppressed: boolean;
  suppression_reason: string | null;
  row_version: number;
  created_at: string;
  updated_at: string;
}

export interface EntityDuplicateCandidate {
  type: string;
  normalized_value: string;
  suggested_canonical_entity_id: string;
  entities: Array<{
    id: string;
    type: string;
    value: string;
    source_tool: string;
    source_attribution: string | null;
    finding_count: number;
  }>;
}

export interface EntityGroup {
  id: string;
  engagement_id: string;
  canonical_entity_id: string | null;
  label: string | null;
  reason: string;
  entity_ids: string[];
  row_version: number;
  created_at: string;
  updated_at: string;
}

export interface EntityGroupMergeDeleteResult {
  status: string;
  group_id: string;
  canonical_entity_id: string;
  suppressed_entity_ids: string[];
  transferred_link_count: number;
  merged_property_keys: string[];
  canonical_entity: StoredEntity;
}

export interface FindingContextCandidate {
  type: string;
  value: string;
  entity_id: string | null;
  scope_item_id: string | null;
  scope_source: string | null;
  scope_compatible: boolean;
}

export interface FindingContextPromotionItem {
  type: string;
  value: string;
  add_to_entities: boolean;
  add_to_scope: boolean;
}

export interface FindingContextPromotionResult {
  entities_created: number;
  entity_links_created: number;
  scope_items_created: number;
  candidates: FindingContextCandidate[];
}

export interface MaltegoImportResult {
  inserted: number;
  merged: number;
  skipped_empty: number;
  skipped_unknown: number;
  total_nodes: number;
  entities: StoredEntity[];
}

// Phase 10 — DarkWeb data import (Dehashed JSON/CSV first, future
// sources slot into the same response shape).
export interface DarkwebImportResult {
  source: string; // "dehashed" today
  inserted: number;
  merged: number;
  skipped_no_identifier: number;
  skipped_malformed: number;
  total_rows: number;
  databases: string[];
  entities: StoredEntity[];
}

// Attachment metadata (raw bytes fetched separately via GET /attachments/{id})
export interface Attachment {
  id: string;
  finding_id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  created_at: string;
}

export type EntityType =
  | "email"
  | "ip"
  | "cidr"
  | "domain"
  | "subdomain"
  | "url"
  | "host";

export interface EntityFindingRef {
  id: string;
  title: string;
  tool: string | null;
  severity: Severity;
  phase: FindingPhase;
}

// v2.19.0: scope tag on each derived entity. "live" = matches current
// scope, "legacy" = matched a scope item that was deleted after v2.19
// shipped (older hard-deletes left no trace, so those show as "oos"),
// "oos" = never a scope target.
export type EntityScopeStatus = "live" | "legacy" | "oos";

// Correlated entity derived from findings (GET /engagements/{slug}/entities).
export interface Entity {
  type: string;
  value: string;
  count: number;
  severity: Severity;
  first_seen: string;
  last_seen: string;
  findings: EntityFindingRef[];
  scope_status: EntityScopeStatus;
}

export interface Observation {
  id: string;
  content: string;
  phase: FindingPhase | null;
  created_by: string | null;
  created_at: string;
  // v1.4.8: findings this observation references (supports / evidence).
  finding_ids?: string[];
}

// ─── BYO provider keys ─────────────────────────────────────────────────────

export type ProviderKeyKind = "model_provider" | "mcp_server" | "other";

export interface ProviderKey {
  id: string;
  user_id: string;
  kind: ProviderKeyKind;
  name: string;
  provider: string;
  is_local: boolean;
  models: string[];
  endpoint: string | null;
  key_last4: string | null;
  extra: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

// ─── Phase 9 orchestrator ──────────────────────────────────────────────────

export type TaskKind = "scan" | "enum" | "exploit";
export type OwnerEligibility = "agent" | "analyst" | "either";
export type TaskStatus =
  | "pending"
  | "dispatched"
  | "running"
  | "completed"
  | "failed"
  | "deferred"
  | "cancelled";

export interface Task {
  id: string;
  engagement_id: string;
  finding_id: string | null;
  work_item_id: string | null;
  title: string;
  kind: TaskKind;
  owner_eligibility: OwnerEligibility;
  status: TaskStatus;
  payload: Record<string, unknown>;
  run_id: string | null;
  dispatched_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ProviderKeyEntry {
  name: string;
  provider: string;
  kind?: ProviderKeyKind;
  models?: string[];
  is_local?: boolean;
  endpoint?: string | null;
  api_key?: string | null;
  extra?: Record<string, unknown>;
}

export interface ProviderKeyImportPayload {
  providers: ProviderKeyEntry[];
}

export interface ProviderKeyImportErrorRow {
  index: number;
  name: string | null;
  reason: string;
}

export interface ProviderKeyImportResult {
  created: ProviderKey[];
  errors: ProviderKeyImportErrorRow[];
  duplicates: ProviderKeyImportErrorRow[];
}

export interface ProviderKeyProbeResult {
  ok: boolean;
  reachable: boolean;
  supported: boolean;
  status_code: number | null;
  latency_ms: number | null;
  models: string[];
  checked_url: string | null;
  error: string | null;
}

export type SuggestionKind = "task" | "ephemeral" | "note" | "work_item" | "strategy_revision";
export type SuggestionStatus = "open" | "accepted" | "dismissed";
export type AgentName =
  | "strategic"
  | "engagement_strategist"
  | "tactical"
  | "correlate"
  | "triage"
  | "planner"
  | "tool_review";

export interface Suggestion {
  id: string;
  engagement_id: string;
  finding_id: string | null;
  title: string;
  body: string | null;
  kind: SuggestionKind;
  payload: Record<string, unknown>;
  status: SuggestionStatus;
  created_by_agent: AgentName;
  decided_by: string | null;
  decided_at: string | null;
  task_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface AnalyzeFindingResponse {
  execution_id: string;
  suggestions: Suggestion[];
}

export interface TriageFindingResponse {
  execution_id: string;
  summary: string;
}

export interface AcceptSuggestionResponse {
  suggestion: Suggestion;
  task: Task | null;
  dispatched: boolean;
}

// ─── Scope bulk-import ─────────────────────────────────────────────────────

export interface ScopeImportPreviewRow {
  line: number;
  value: string;
  kind: ScopeKind;
  is_exclusion: boolean;
}

export interface ScopeImportErrorRow {
  line: number;
  raw: string;
  reason: string;
}

export interface ScopeImportDuplicateRow {
  line: number;
  value: string;
  kind: ScopeKind;
  is_exclusion: boolean;
}

export interface ScopeImportPreview {
  preview: ScopeImportPreviewRow[];
  errors: ScopeImportErrorRow[];
  would_create: number;
}

export interface ScopeImportResult {
  created: ScopeItem[];
  errors: ScopeImportErrorRow[];
  duplicates: ScopeImportDuplicateRow[];
}

// v0.8.1: providers mirror the /settings/keys Quick Add presets so the
// Scope-tab dropdown matches what the analyst can upload a key for.
// Backend routes the 8 OpenAI-compatible providers via ChatOpenAI with
// a per-provider base_url (see strategic._make_chat_model).
export type LLMProvider =
  | "anthropic"
  | "openai"
  | "azure"
  | "ollama"
  | "google"
  | "xai"
  | "mistral"
  | "cohere"
  | "together"
  | "groq"
  | "deepseek"
  | "custom";

export interface RunModel {
  provider: LLMProvider;
  name: string;
  // v1.4.12: pin a specific cached provider key by id (roadmap #3).
  key_id?: string | null;
}

export interface RunStartResponse {
  engagement_id: string;
  thread_id: string;
  events_stream: string;
  model: RunModel;
}

// SSE events emitted from the outbound stream.

export type RunEvent =
  | { type: "run.started"; thread_id: string; prompt: string }
  | {
      type: "approval.pending";
      thread_id: string;
      approval_id: string;
      tool: string;
      args: Record<string, unknown>;
      risk: RiskLevel;
      scope: Record<string, unknown>;
      tool_call_id: string;
    }
  | {
      type: "tool.denied";
      thread_id: string;
      tool: string;
      args: Record<string, unknown>;
      reason: string;
      scope: Record<string, unknown>;
    }
  | {
      type: "tool.auto_approved";
      thread_id: string;
      tool: string;
      args: Record<string, unknown>;
      risk: string;
      authorization_id: string;
    }
  | {
      type: "finding.created";
      thread_id: string;
      tool: string;
      args: Record<string, unknown>;
      data: Record<string, unknown>;
      target: string | null;
      severity: Severity;
      title: string | null;
      finding_id: string;
      phase: FindingPhase;
      status: FindingValidationStatus;
    }
  | { type: "run.completed"; thread_id: string }
  | { type: "run.errored"; thread_id: string; error: string };

export type RunEventType = RunEvent["type"];

// ─── Costs (Phase 11) ───────────────────────────────────────────────────────

export type AgentCostName = AgentName;

export interface CostBucket {
  executions: number;
  tokens_in: number;
  tokens_out: number;
  cost_usd: number;
}

export interface AgentCost extends CostBucket {
  agent: AgentCostName;
}

export interface ModelCost extends CostBucket {
  provider: string | null;
  model: string | null;
  priced: boolean;
}

export interface ToolCost {
  tool_id: string;
  tool_name: string;
  invocations: number;
  total_duration_seconds: number;
  cost_usd: number;
}

export interface ToolCostSummary {
  invocations: number;
  total_duration_seconds: number;
  cost_usd: number;
  by_tool: ToolCost[];
}

export interface ReportReadinessCheck {
  key: string;
  level: "blocker" | "warning" | "info";
  count: number;
  message: string;
  finding_ids: string[];
  target_view: string | null;
}

export interface ReportReadiness {
  ready: boolean;
  generated_at: string;
  reportable_count: number;
  total_findings: number;
  checks: ReportReadinessCheck[];
}

export interface CostRollup {
  engagement_id: string;
  engagement_slug: string;
  total: CostBucket;
  by_agent: AgentCost[];
  by_model: ModelCost[];
  unpriced_models: string[];
  tools: ToolCostSummary;
}

// ── /me + roadmap suggestions ────────────────────────────────────────────

export type UserRole = "admin" | "user" | "guest";

export interface Me {
  id: string;
  email: string;
  display_name: string | null;
  is_admin: boolean;
  role: UserRole;
  // v1.4.11: per-analyst default model (roadmap #3 / #12).
  default_llm_provider?: string | null;
  default_llm_model?: string | null;
}

export type RoadmapSuggestionStatus =
  | "pending_review"
  | "approved"
  | "rejected";

// One entry as returned by the GitHub Releases API (only the fields the
// What's-New surface actually uses).
export interface ReleaseNote {
  tag_name: string;
  name: string | null;
  published_at: string;
  body: string | null;
  html_url: string;
  // v1.3.0: bucketed commit titles between the previous tag and this
  // one, stamped by install.sh at deploy time. Missing on releases
  // stamped by an older install.sh — the What's New page falls back
  // to raw-body render in that case. Empty buckets are still emitted
  // so the frontend can rely on the shape being present when the
  // field exists.
  categories?: ReleaseCategories;
}

export interface ReleaseCategoryEntry {
  title: string;
  sha: string;
  pr: number | null;
}

export interface ReleaseCategories {
  features: ReleaseCategoryEntry[];
  fixes: ReleaseCategoryEntry[];
  qol: ReleaseCategoryEntry[];
  ops: ReleaseCategoryEntry[];
}

export interface RoadmapSuggestion {
  id: string;
  author_user_id: string | null;
  // v1.4.4: resolved names/emails for attribution display.
  author_display_name: string | null;
  author_email: string | null;
  body: string;
  agent_pros: string[];
  agent_cons: string[];
  agent_summary: string | null;
  agent_execution_id: string | null;
  status: RoadmapSuggestionStatus;
  reviewed_by_user_id: string | null;
  reviewed_by_display_name: string | null;
  reviewed_by_email: string | null;
  reviewed_at: string | null;
  review_note: string | null;
  source: string;
  // v0.16.0
  priority: number | null;
  combined_into_id: string | null;
  // v1.1.0: "Mark completed" markers — orthogonal to `status`. When
  // `implemented_at` is set, the ROADMAP.md renderer moves this row from
  // the Open section to the Shipped section.
  implemented_at: string | null;
  implemented_by_user_id: string | null;
  implemented_by_display_name: string | null;
  implemented_by_email: string | null;
  created_at: string;
  updated_at: string;
}

// v0.16.0 — feedback prioritization agent ops
export interface CombineClusterRead {
  primary_id: string;
  member_ids: string[];
  reasoning: string;
}

export interface CombineDetectResponse {
  clusters: CombineClusterRead[];
  pool_size: number;
  model: string;
  tokens_in: number;
  tokens_out: number;
  execution_id: string | null;
  error: string | null;
}

export interface RankedRowRead {
  id: string;
  priority: number;
  reasoning: string;
}

export interface BulkRankResponse {
  rankings: RankedRowRead[];
  pool_size: number;
  applied: boolean;
  model: string;
  tokens_in: number;
  tokens_out: number;
  execution_id: string | null;
  error: string | null;
}

export interface RoadmapListFilters {
  status?: RoadmapSuggestionStatus;
  priority_min?: number;
  priority_max?: number;
  include_unranked?: boolean;
  show_combined?: boolean;
}

// ── External integrations — v0.9.0 provider catalog ─────────────────────

// v0.9.0: type is a free-form string now (was a closed union). The
// provider catalog in lib/integrations-catalog.ts is the source of truth
// for which slugs the UI knows about; the backend accepts any string so
// new providers ship as a frontend module edit.
export type IntegrationType = string;

export type IntegrationPurpose =
  | "feedback"
  | "status_alerts"
  | "roadmap_push"
  | "manual";

export interface Integration {
  id: string;
  type: IntegrationType;
  purpose: IntegrationPurpose;
  name: string;
  display_name: string | null;
  logo_url: string | null;
  enabled: boolean;
  // Provider-defined JSONB. Secrets (bot_token / pat_token / api_key)
  // come back masked (…1234); the modal sends the masked string back as
  // a "keep the stored value" signal on update.
  config: Record<string, unknown>;
  created_by_user_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface IntegrationCreate {
  type: IntegrationType;
  purpose: IntegrationPurpose;
  name: string;
  display_name?: string | null;
  logo_url?: string | null;
  enabled: boolean;
  config: Record<string, unknown>;
}

export interface IntegrationUpdate {
  purpose?: IntegrationPurpose;
  name?: string;
  display_name?: string | null;
  logo_url?: string | null;
  enabled?: boolean;
  config?: Record<string, unknown>;
}

// ── Admin user management ────────────────────────────────────────────────

export interface AdminUser {
  id: string;
  email: string;
  display_name: string | null;
  role: UserRole;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

// ── Contributions (v0.10.0) ──────────────────────────────────────────────

export type ContributionSource = "audit" | "agent_exec";
export type ContributionActorKind = "analyst" | "agent" | "system";

export interface ContributionActor {
  id: string;
  kind: ContributionActorKind;
  label: string;
}

export interface ContributionDay {
  date: string; // YYYY-MM-DD (UTC)
  count: number;
}

export interface ContributionHeatmap {
  start_date: string;
  end_date: string;
  max_count: number;
  days: ContributionDay[];
  actors: ContributionActor[];
}

export interface ContributionEntry {
  when: string; // ISO timestamp
  actor_id: string | null;
  actor_kind: ContributionActorKind;
  actor_label: string;
  source: ContributionSource;
  action: string;
  summary: string;
}

export interface ContributionEntries {
  start_date: string;
  end_date: string;
  total: number;
  limit: number;
  offset: number;
  entries: ContributionEntry[];
}

// ── Tools tab (v0.11.0) ────────────────────────────────────────────────

export type ToolKind = "python" | "shell" | "binary";
export type ToolLane = "analyst" | "admin";
export type ToolStatus = "draft" | "approved" | "revoked";
export type ToolTaskKind = "enum" | "scan" | "exploit";
export type ToolRiskLevel = "passive" | "active" | "destructive";

export interface ToolRead {
  id: string;
  name: string;
  description: string | null;
  kind: ToolKind;
  lane: ToolLane;
  risk_level: string;
  task_kind: ToolTaskKind;
  status: ToolStatus;
  manifest: Record<string, unknown>;
  validation: Record<string, unknown>;
  has_artifact: boolean;
  version: number;
  // v1.11.0: curated one-liner shown as a Scope-tab "Current Tools"
  // button. Null → fall back to a "Run <name>" template client-side.
  example_prompt: string | null;
  created_by_user_id: string | null;
  approved_by_user_id: string | null;
  approved_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ToolUploadResponse {
  tool: ToolRead;
  validation_ok: boolean;
  validation_errors: string[];
}

// v1.12.0: built-in orchestrator MCP tool (subfinder, dns_lookup, port_scan,
// etc.) — the tools shipped in the backend image and dispatched by the
// Tactical agent. Separate universe from ``ToolRead`` (analyst-uploaded
// runtime tools in the ``tools`` DB catalog). Served from
// ``GET /orchestrator/tools``.
export interface OrchestratorTool {
  name: string;
  description: string;
  // v1.12.1: what type of scope-item the tool accepts (domain / ip /
  // cidr / url). NOT the charter task-kind (enum/scan/exploit) — that
  // grouping is derived from ``phase``.
  scope_kind: string;
  // FindingPhase: osint | vuln_scan | exploit | phishing | general.
  // This is what the UI groups by (see ``lib/tool-phases.ts``).
  phase: string;
  risk: string; // passive | active | destructive
  target_arg: string;
  example_prompt: string;
  // v2.24.1: true when this tool needs a BYO API key resolved from
  // /settings/keys (freeipapi, ipinfo, wigle). The Scope-tab ToolsPanel
  // shows a "key ready" / "needs key" pill based on this + the
  // analyst's uploaded provider keys.
  needs_secret?: boolean;
}

// Response from POST /tools/infer — the auto-detect upload path.
export interface ToolInferResponse {
  name: string | null;
  description: string | null;
  entrypoint: string;
  kind: string;
  lane: string;
  fields: Record<string, unknown>;
  missing: string[];
  warnings: string[];
  manifest_yaml: string;
}

export type ToolInvocationStatus =
  | "queued"
  | "running"
  | "completed"
  | "failed"
  | "timeout";

export interface ToolInvocationRead {
  id: string;
  tool_id: string;
  tool_version: number;
  tool_name: string | null;
  engagement_id: string;
  invoker_user_id: string | null;
  args: Record<string, unknown>;
  runtime_ref: string | null;
  status: ToolInvocationStatus;
  exit_code: number | null;
  stdout: string | null;
  stderr: string | null;
  error: string | null;
  started_at: string;
  completed_at: string | null;
}

// ─── v1.24.0 Settings > Configurations ────────────────────────────────────

// The three engagement-scoped agent roles this bundle covers. Adding
// planner/triage/etc later widens this union without a migration.
export type ConfigurableAgentRole =
  | "strategic"
  | "engagement_strategist"
  | "tactical"
  | "correlate";

export interface AgentConfigRead {
  engagement_id: string;
  engagement_slug: string;
  strategic: string | null;
  engagement_strategist: string | null;
  tactical: string | null;
  correlate: string | null;
  updated_at: string | null;
}

export interface AgentConfigListResponse {
  configurations: AgentConfigRead[];
}

// Body for PUT /agent-configurations/{slug}. Missing keys are left
// unchanged; explicit ``null`` clears that specific role.
export interface AgentConfigPut {
  strategic?: string | null;
  engagement_strategist?: string | null;
  tactical?: string | null;
  correlate?: string | null;
}

export interface AgentConfigRolePayload {
  strategic?: string | null;
  engagement_strategist?: string | null;
  tactical?: string | null;
  correlate?: string | null;
}

export interface AgentConfigExport {
  version: number;
  exported_at: string;
  exported_by_user_id: string;
  configurations: Record<string, AgentConfigRolePayload>;
}

export interface AgentConfigImportResult {
  applied_slugs: string[];
  skipped_unknown_slugs: string[];
}

// v2.10.0 Infrastructure tab — admin-only VM inventory.
export type VmPowerState =
  | "running"
  | "stopped"
  | "deallocated"
  | "starting"
  | "stopping"
  | "deallocating"
  | "unknown";

export interface VmSummary {
  arm_id: string;
  name: string;
  subscription_id: string;
  resource_group: string;
  location: string;
  size: string;
  os_type: string;
  os_offer: string | null;
  power_state: VmPowerState;
  public_ip: string | null;
  private_ip: string | null;
  tags: Record<string, string>;
}

export interface InfraSubscription {
  subscription_id: string;
  display_name: string;
  state: string;
}

export interface InfraStatus {
  configured: boolean;
  mock: boolean;
  subscription_count: number;
}

// v2.11.0 Auto-shutdown schedule — Microsoft.DevTestLab/schedules.
// `time_hhmm` is 4-digit local time ("1900"). `timezone_id` is a Windows
// TZ id (Azure's native format — not IANA). `null` webhook = notifications off.
export interface AutoShutdown {
  enabled: boolean;
  time_hhmm: string;
  timezone_id: string;
  notification_webhook_url: string | null;
  notification_minutes: number;
}

export interface AutoShutdownWrite {
  enabled: boolean;
  time_hhmm: string;
  timezone_id: string;
  notification_webhook_url?: string | null;
  notification_minutes?: number;
}

// v2.12.0 — one-shot Run Command against a VM. Backend wraps Azure's
// LRO; the response is the parsed final output. `exit_code` is null
// because Azure's runCommand LRO doesn't surface it directly.
export interface RunCommandResult {
  stdout: string;
  stderr: string;
  exit_code: number | null;
  duration_ms: number;
  timed_out: boolean;
}

// v3 Track A — Playbook catalog + runs (A3/A4/A5/A5b).
//
// Runs move through:
//   awaiting_approval (only if playbook.active) → pending → running →
//   completed | partial | failed | cancelled
//
// See ``services/playbook/runner.py`` for the state machine.
export type PlaybookRunStatus =
  | "awaiting_approval"
  | "pending"
  | "running"
  | "completed"
  | "partial"
  | "failed"
  | "cancelled";

export type PlaybookExecutorKind = "internal" | "mcp";

export interface PlaybookStepRead {
  sort_order: number;
  tool_slug: string;
  args_template: Record<string, unknown>;
  satisfies_node_ids: string[];
  description: string | null;
}

export interface PlaybookRead {
  id: string;
  slug: string;
  version: number;
  name: string;
  description: string | null;
  applies_to_asset_class: string;
  active: boolean;
  step_count: number;
}

export interface PlaybookDetail extends PlaybookRead {
  steps: PlaybookStepRead[];
}

export interface PlaybookRunRead {
  id: string;
  engagement_id: string;
  playbook_id: string;
  playbook_slug: string;
  playbook_version: number;
  status: PlaybookRunStatus;
  executor: PlaybookExecutorKind;
  scope_subset: unknown[];
  started_at: string | null;
  completed_at: string | null;
  steps_total: number;
  steps_succeeded: number;
  steps_failed: number;
  findings_new: number;
  findings_unvalidated: number;
  findings_high_severity: number;
  findings_total: number;
  last_error: string | null;
  approved_by: string | null;
  approved_at: string | null;
  approval_reason: string | null;
  rejected_by: string | null;
  rejected_at: string | null;
  rejection_reason: string | null;
}

export interface PlaybookRunCreate {
  playbook_slug: string;
  playbook_version?: number;
  scope_subset: string[];
  executor?: PlaybookExecutorKind;
}
