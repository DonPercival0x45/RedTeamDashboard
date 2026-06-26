// Wire-format types for LLM cost tracking (Phase 11).

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

export interface CostRollup {
  project_id: string;
  engagement_slug: string;
  total: CostBucket;
  by_agent: AgentCost[];
  by_model: ModelCost[];
  unpriced_models: string[];
}
