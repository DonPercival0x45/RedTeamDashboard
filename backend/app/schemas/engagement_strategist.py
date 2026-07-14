"""Structured Engagement Strategist contract and conversation shapes."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class RecordRef(BaseModel):
    type: Literal[
        "engagement",
        "strategy_revision",
        "objective",
        "work_item",
        "work_item_result",
        "finding",
        "observation",
        "entity",
        "task",
        "coverage_item",
        "strategy_signal",
    ]
    id: uuid.UUID


class StrategistFact(BaseModel):
    statement: str = Field(max_length=2000)
    refs: list[RecordRef] = Field(default_factory=list, max_length=20)


class StrategistInference(BaseModel):
    statement: str = Field(max_length=2000)
    confidence: Literal["low", "medium", "high"]
    refs: list[RecordRef] = Field(default_factory=list, max_length=20)


class StrategistHypothesis(BaseModel):
    statement: str = Field(max_length=2000)
    confidence: Literal["low", "medium", "high"]
    validation_needed: str = Field(max_length=2000)


class StrategistWorkProposal(BaseModel):
    proposal_key: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=4000)
    rationale: str | None = Field(default=None, max_length=4000)
    objective_id: uuid.UUID | None = None
    priority: Literal["critical", "high", "medium", "low"] = "medium"
    executor_type: Literal[
        "analyst", "finding_agent", "engagement_strategist", "tactical", "unassigned"
    ] = "unassigned"
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=20)
    finding_links: list[dict[str, Any]] = Field(default_factory=list, max_length=50)


class StrategyRevisionProposal(BaseModel):
    proposal_key: str = Field(min_length=1, max_length=200)
    summary: str | None = Field(default=None, max_length=300)
    body: str = Field(min_length=1, max_length=30000)
    structured: dict[str, Any] = Field(default_factory=dict)
    reason: str | None = Field(default=None, max_length=4000)
    based_on_revision_id: uuid.UUID | None = None


class StrategistOutput(BaseModel):
    situation_summary: str = Field(max_length=5000)
    facts: list[StrategistFact] = Field(default_factory=list, max_length=30)
    inferences: list[StrategistInference] = Field(default_factory=list, max_length=20)
    hypotheses: list[StrategistHypothesis] = Field(default_factory=list, max_length=20)
    work_item_proposals: list[StrategistWorkProposal] = Field(default_factory=list, max_length=5)
    strategy_revision_proposal: StrategyRevisionProposal | None = None
    coverage_gaps: list[str] = Field(default_factory=list, max_length=20)
    warnings: list[str] = Field(default_factory=list, max_length=20)


class StrategistRunResponse(BaseModel):
    execution_id: uuid.UUID
    context_hash: str
    output: StrategistOutput
    suggestion_ids: list[uuid.UUID] = Field(default_factory=list)


class StrategistChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    conversation_id: uuid.UUID | None = None


class StrategistChatMessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    conversation_id: uuid.UUID
    role: Literal["user", "assistant", "system"]
    content: str
    action_payload: dict[str, Any] | None = None
    execution_id: uuid.UUID | None = None
    created_at: datetime


class StrategistChatState(BaseModel):
    conversation_id: uuid.UUID | None = None
    messages: list[StrategistChatMessageRead] = Field(default_factory=list)


class StrategistChatResponse(BaseModel):
    conversation_id: uuid.UUID
    user_message: StrategistChatMessageRead
    assistant_message: StrategistChatMessageRead
    execution_id: uuid.UUID


class StrategistActionDecision(BaseModel):
    action_index: int = Field(ge=0, le=20)


class StrategistActionResult(BaseModel):
    message: StrategistChatMessageRead
    suggestion_id: uuid.UUID | None = None
    status: Literal["accepted", "denied"]


class StrategistSummary(BaseModel):
    summary: str
    message_count: int
