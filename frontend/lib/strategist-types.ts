export type StrategistRecordType =
  | "engagement"
  | "strategy_revision"
  | "objective"
  | "work_item"
  | "work_item_result"
  | "finding"
  | "observation"
  | "entity"
  | "task"
  | "coverage_item"
  | "strategy_signal";

export interface StrategistRecordRef {
  type: StrategistRecordType;
  id: string;
}

export interface StrategistOutput {
  situation_summary: string;
  facts: Array<{ statement: string; refs: StrategistRecordRef[] }>;
  inferences: Array<{
    statement: string;
    confidence: "low" | "medium" | "high";
    refs: StrategistRecordRef[];
  }>;
  hypotheses: Array<{
    statement: string;
    confidence: "low" | "medium" | "high";
    validation_needed: string;
  }>;
  work_item_proposals: Array<{
    proposal_key: string;
    title: string;
    description: string | null;
    rationale: string | null;
    objective_id: string | null;
    priority: "critical" | "high" | "medium" | "low";
    executor_type:
      | "analyst"
      | "finding_agent"
      | "engagement_strategist"
      | "tactical"
      | "unassigned";
    acceptance_criteria: string[];
    finding_links: Array<Record<string, unknown>>;
  }>;
  strategy_revision_proposal: {
    proposal_key: string;
    summary: string | null;
    body: string;
    structured: Record<string, unknown>;
    reason: string | null;
    based_on_revision_id: string | null;
  } | null;
  coverage_gaps: string[];
  warnings: string[];
}

export interface StrategistRunResponse {
  execution_id: string;
  context_hash: string;
  output: StrategistOutput;
  suggestion_ids: string[];
}

export interface StrategistChatAction {
  type: "suggestion";
  suggestion_id: string;
  suggestion_kind: string;
  title: string;
  status: "proposed" | "accepted" | "denied";
}

export interface StrategistChatMessage {
  id: string;
  conversation_id: string;
  role: "user" | "assistant" | "system";
  content: string;
  action_payload: {
    actions?: StrategistChatAction[];
    analysis?: StrategistOutput;
  } | null;
  execution_id: string | null;
  created_at: string;
}

export interface StrategistChatState {
  conversation_id: string | null;
  messages: StrategistChatMessage[];
}

export interface StrategistChatResponse {
  conversation_id: string;
  user_message: StrategistChatMessage;
  assistant_message: StrategistChatMessage;
  execution_id: string;
}

export interface StrategistActionResult {
  message: StrategistChatMessage;
  suggestion_id: string | null;
  status: "accepted" | "denied";
}

export interface StrategistSummary {
  summary: string;
  message_count: number;
}
