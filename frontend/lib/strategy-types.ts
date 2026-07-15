export type StrategyRevisionState =
  | "draft"
  | "proposed"
  | "current"
  | "rejected"
  | "superseded";

export type ObjectiveStatus =
  | "planned"
  | "active"
  | "blocked"
  | "completed"
  | "deferred"
  | "cancelled";
export type ObjectivePriority = "low" | "medium" | "high" | "critical";

export type WorkItemStatus =
  | "ready"
  | "in_progress"
  | "blocked"
  | "completed"
  | "deferred"
  | "cancelled";
export type WorkItemPriority = "low" | "medium" | "high" | "critical";
export type WorkItemExecutor =
  | "unassigned"
  | "analyst"
  | "finding_agent"
  | "engagement_strategist"
  | "tactical";
export type WorkItemResolution =
  | "completed"
  | "disproved"
  | "not_applicable"
  | "duplicate"
  | "superseded"
  | "unable_to_complete";
export type WorkItemFindingRelationship =
  | "primary"
  | "related"
  | "produced_by"
  | "blocks";
export type WorkItemResultState =
  | "proposed"
  | "accepted"
  | "rejected"
  | "superseded";

export interface StrategyRevision {
  id: string;
  engagement_id: string;
  version: number;
  state: StrategyRevisionState;
  based_on_revision_id: string | null;
  summary: string | null;
  body: string;
  structured: Record<string, unknown>;
  created_by_user_id: string | null;
  proposed_by_execution_id: string | null;
  proposal_reason: string | null;
  decided_by_user_id: string | null;
  decided_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface StrategyRevisionCreate {
  body: string;
  summary?: string | null;
  structured?: Record<string, unknown>;
  state?: StrategyRevisionState;
  based_on_revision_id?: string | null;
  proposal_reason?: string | null;
}

export interface Objective {
  id: string;
  engagement_id: string;
  title: string;
  description: string | null;
  success_criteria: string | null;
  status: ObjectiveStatus;
  priority: ObjectivePriority;
  display_order: number;
  owner_user_id: string | null;
  target_date: string | null;
  created_by_user_id: string | null;
  completed_by_user_id: string | null;
  completed_at: string | null;
  row_version: number;
  created_at: string;
  updated_at: string;
}

export interface ObjectiveCreate {
  title: string;
  description?: string | null;
  success_criteria?: string | null;
  status?: ObjectiveStatus;
  priority?: ObjectivePriority;
  display_order?: number;
  owner_user_id?: string | null;
  target_date?: string | null;
}

export interface ObjectiveUpdate extends Partial<ObjectiveCreate> {
  expected_row_version: number;
}

export interface WorkItemFindingLink {
  work_item_id: string;
  finding_id: string;
  relationship: WorkItemFindingRelationship;
  created_at: string;
}

export interface WorkItem {
  id: string;
  engagement_id: string;
  objective_id: string | null;
  parent_work_item_id: string | null;
  title: string;
  description: string | null;
  rationale: string | null;
  acceptance_criteria: string[];
  status: WorkItemStatus;
  priority: WorkItemPriority;
  executor_type: WorkItemExecutor;
  assigned_user_id: string | null;
  created_by_user_id: string | null;
  created_by_execution_id: string | null;
  started_at: string | null;
  blocked_reason: string | null;
  due_at: string | null;
  resolution_outcome: WorkItemResolution | null;
  resolution_note: string | null;
  completed_by_user_id: string | null;
  completed_at: string | null;
  row_version: number;
  created_at: string;
  updated_at: string;
  finding_links: WorkItemFindingLink[];
}

export interface WorkItemCreate {
  title: string;
  description?: string | null;
  rationale?: string | null;
  acceptance_criteria?: string[];
  status?: WorkItemStatus;
  priority?: WorkItemPriority;
  executor_type?: WorkItemExecutor;
  objective_id?: string | null;
  parent_work_item_id?: string | null;
  assigned_user_id?: string | null;
  due_at?: string | null;
  finding_links?: Array<{
    finding_id: string;
    relationship?: WorkItemFindingRelationship;
  }>;
}

export interface WorkItemUpdate {
  expected_row_version: number;
  title?: string;
  description?: string | null;
  rationale?: string | null;
  acceptance_criteria?: string[];
  priority?: WorkItemPriority;
  executor_type?: WorkItemExecutor;
  objective_id?: string | null;
  parent_work_item_id?: string | null;
  assigned_user_id?: string | null;
  due_at?: string | null;
}

export interface WorkItemFilters {
  status?: WorkItemStatus;
  priority?: WorkItemPriority;
  executor_type?: WorkItemExecutor;
  assigned_user_id?: string;
  objective_id?: string;
  finding_id?: string;
  needs_decision?: boolean;
  q?: string;
  limit?: number;
  cursor?: string;
}

export interface WorkItemResult {
  id: string;
  work_item_id: string;
  revision: number;
  state: WorkItemResultState;
  summary: string;
  structured: Record<string, unknown>;
  evidence_refs: Array<Record<string, unknown>>;
  proposed_by_user_id: string | null;
  proposed_by_execution_id: string | null;
  decided_by_user_id: string | null;
  decided_at: string | null;
  created_at: string;
}

export type StrategySignalStatus = "open" | "incorporated" | "dismissed" | "superseded";
export interface StrategySignal {
  id: string;
  engagement_id: string;
  source_finding_id: string | null;
  source_work_item_id: string | null;
  source_work_item_result_id: string | null;
  source_execution_id: string | null;
  signal_type: string;
  summary: string;
  confidence: "low" | "medium" | "high";
  evidence_refs: Array<Record<string, unknown>>;
  suggested_effect: Record<string, unknown>;
  dedup_key: string;
  status: StrategySignalStatus;
  decided_by_user_id: string | null;
  decided_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface WorkItemRollupBucket {
  remaining: number;
  blocked: number;
  proposals: number;
  deferred: number;
}
export interface WorkItemRollup {
  by_finding: Record<string, WorkItemRollupBucket>;
  engagement: WorkItemRollupBucket;
}

export interface Checkpoint {
  id: string;
  engagement_id: string;
  strategy_revision_id: string | null;
  created_by_user_id: string | null;
  created_by_execution_id: string | null;
  material_event_cursor: string;
  facts: Record<string, unknown>;
  narrative: string | null;
  created_at: string;
}

export interface ResumeBriefing {
  current_focus: Record<string, unknown>;
  since_checkpoint: Record<string, unknown>;
  active_work: WorkItem[];
  blocked_work: WorkItem[];
  decisions_required: Array<Record<string, unknown>>;
  recommended_starting_records: Array<Record<string, unknown>>;
  coverage_summary: Record<string, unknown>;
  report_readiness: Record<string, unknown>;
  generated_at: string;
}

export type CoverageStatus =
  | "not_started"
  | "planned"
  | "active"
  | "covered"
  | "blocked"
  | "deferred"
  | "accepted_gap"
  | "not_applicable";

export interface CoverageItem {
  id: string;
  engagement_id: string;
  objective_id: string | null;
  scope_item_id: string | null;
  target_kind: string;
  target_key: string;
  activity_category: string;
  status: CoverageStatus;
  supporting_refs: Array<Record<string, unknown>>;
  reason: string | null;
  accepted_by_user_id: string | null;
  accepted_at: string | null;
  row_version: number;
  created_at: string;
  updated_at: string;
}

export interface CompletionRef {
  type:
    | "work_item"
    | "coverage_item"
    | "task"
    | "agent_execution"
    | "approval"
    | "finding"
    | "report_check";
  id: string;
}

export interface CompletionCheck {
  key: string;
  severity: "blocker" | "warning" | "info";
  count: number;
  waivable: boolean;
  refs: CompletionRef[];
  message: string;
}

export interface CompletionException {
  ref: CompletionRef;
  rationale: string;
}

export interface CompletionReadiness {
  work_state: "active" | "completion_review" | "completed";
  work_state_version: number;
  ready: boolean;
  readiness_hash: string;
  checks: CompletionCheck[];
  accepted_gap_candidates: Array<{
    ref: CompletionRef;
    key: string;
    message: string;
  }>;
  generated_at: string;
}

export interface CompletionDecision {
  id: string;
  engagement_id: string;
  action: "review_started" | "approved" | "reopened";
  from_work_state: string;
  to_work_state: string;
  readiness_hash: string | null;
  readiness_snapshot: Record<string, unknown> | null;
  accepted_exceptions: Array<Record<string, unknown>>;
  strategy_revision_id: string | null;
  prior_completion_decision_id: string | null;
  reason: string | null;
  idempotency_key: string;
  decided_by_user_id: string;
  created_at: string;
}

export interface CompletionMutationResponse {
  work_state: string;
  work_state_version: number;
  decision: CompletionDecision;
  readiness: CompletionReadiness | null;
}
