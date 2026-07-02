// Wire-format types that match the Pydantic schemas in app/schemas/*.

export type EngagementStatus = "active" | "archived" | "flushed";

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
  time_frame: EngagementTimeFrame;
  start_date: string | null;
  end_date: string | null;
  created_by: string | null;
  archived_at: string | null;
  flushed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ScopeItem {
  id: string;
  engagement_id: string;
  kind: ScopeKind;
  value: string;
  is_exclusion: boolean;
  note: string | null;
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
  validated_at: string | null;
  observed_at: string | null;
  burp_serial_number: string | null;
  created_at: string;
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
  log: Record<string, unknown>;
  history: StatusTransition[];
}

export interface EngagementStatusResponse {
  agents: StatusEntity[];
  tasks: StatusEntity[];
  approvals: StatusEntity[];
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
export interface BurpImportResult {
  imported: Finding[];
  skipped_info: number;
  skipped_out_of_scope: number;
  skipped_duplicate: number;
  total_items: number;
  export_time: string | null;
}

// Phase 10 — stored entities (Maltego import target + future sources).
// Complements the existing derived-from-findings Entity (above).
export interface StoredEntity {
  id: string;
  type: string;
  value: string;
  properties: Record<string, unknown>;
  source_tool: string;
  source_attribution: string | null;
  created_at: string;
  updated_at: string;
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

// Correlated entity derived from findings (GET /engagements/{slug}/entities).
export interface Entity {
  type: string;
  value: string;
  count: number;
  severity: Severity;
  first_seen: string;
  last_seen: string;
  findings: EntityFindingRef[];
}

export interface Observation {
  id: string;
  content: string;
  phase: FindingPhase | null;
  created_by: string | null;
  created_at: string;
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

export type SuggestionKind = "task" | "ephemeral" | "note";
export type SuggestionStatus = "open" | "accepted" | "dismissed";
export type AgentName = "strategic" | "tactical";

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

export type AgentCostName = "strategic" | "tactical";

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
}

export interface RoadmapSuggestion {
  id: string;
  author_user_id: string | null;
  body: string;
  agent_pros: string[];
  agent_cons: string[];
  agent_summary: string | null;
  agent_execution_id: string | null;
  status: RoadmapSuggestionStatus;
  reviewed_by_user_id: string | null;
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
