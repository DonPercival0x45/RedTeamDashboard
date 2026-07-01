"""Schemas for the Costs tab roll-up (GET /engagements/{slug}/costs)."""
from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel

from app.models import AgentName


class CostBucket(BaseModel):
    """Summed usage over a set of agent executions."""

    executions: int
    tokens_in: int
    tokens_out: int
    cost_usd: float


class AgentCost(CostBucket):
    agent: AgentName


class ModelCost(CostBucket):
    provider: str | None
    model: str | None
    # False when the model has no entry in the pricing table — its tokens are
    # counted but cost_usd is $0 and the name appears in unpriced_models.
    priced: bool


class ToolCost(BaseModel):
    """One row in the tool-invocation cost roll-up (v0.15.0). Sits
    beside the LLM cost buckets on the Costs tab so an admin can see
    per-tool compute spend alongside model spend."""

    tool_id: UUID
    tool_name: str
    invocations: int
    total_duration_seconds: float
    cost_usd: float


class ToolCostSummary(BaseModel):
    invocations: int
    total_duration_seconds: float
    cost_usd: float
    by_tool: list[ToolCost]


class CostRollup(BaseModel):
    engagement_id: UUID
    engagement_slug: str
    total: CostBucket
    by_agent: list[AgentCost]
    by_model: list[ModelCost]
    unpriced_models: list[str]
    # v0.15.0: sandbox-runner compute cost for tool invocations. Empty
    # ToolCostSummary when the engagement has no tool_invocations rows.
    tools: ToolCostSummary
