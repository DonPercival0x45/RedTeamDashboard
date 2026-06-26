// Wire-format types for orchestrator tasks, suggestions, and agent
// execution responses (Phase 9).

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
  project_id: string;
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

export type SuggestionKind = "task" | "ephemeral" | "note";
export type SuggestionStatus = "open" | "accepted" | "dismissed";
export type AgentName = "strategic" | "tactical";

export interface Suggestion {
  id: string;
  project_id: string;
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

export interface AcceptSuggestionResponse {
  suggestion: Suggestion;
  task: Task | null;
  dispatched: boolean;
}
