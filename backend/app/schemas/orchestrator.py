"""Wire schemas for the Phase 9 orchestrator layer.

Mirrors the SQLAlchemy models in ``app/models/{task,suggestion,agent_execution}.py``.
Kept in a single file because the three entities are read together (the slide-
over surface shows suggestions + the tasks they spawned).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.agent_execution import AgentExecutionStatus, AgentTrigger
from app.models.suggestion import AgentName, SuggestionKind, SuggestionStatus
from app.models.task import OwnerEligibility, TaskKind, TaskStatus


class TaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    engagement_id: UUID
    finding_id: UUID | None
    title: str
    kind: TaskKind
    owner_eligibility: OwnerEligibility
    status: TaskStatus
    payload: dict[str, Any]
    run_id: UUID | None
    dispatched_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class SuggestionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    engagement_id: UUID
    finding_id: UUID | None
    title: str
    body: str | None
    kind: SuggestionKind
    payload: dict[str, Any]
    status: SuggestionStatus
    created_by_agent: AgentName
    decided_by: UUID | None
    decided_at: datetime | None
    task_id: UUID | None
    created_at: datetime
    updated_at: datetime


class AnalyzeFindingResponse(BaseModel):
    """What ``POST /findings/{id}/analyze`` returns: the Strategic agent's
    suggestions plus the AgentExecution id so the caller can correlate."""

    execution_id: UUID
    suggestions: list[SuggestionRead]


class TriageFindingResponse(BaseModel):
    """What ``POST /findings/{id}/triage`` returns. The frontend drops
    ``summary`` into the slide-over textarea; the analyst edits + saves
    manually. ``execution_id`` is included so the Costs tab can attribute
    the call back to a single row."""

    execution_id: UUID
    summary: str


class AcceptSuggestionResponse(BaseModel):
    """Returned by ``POST /suggestions/{id}/accept``.

    ``task`` is the newly minted Task row (only for kind=``task``).
    ``dispatched`` is true when Tactical immediately fired a worker run for it
    (agent-eligible + scan/enum). Active/destructive tools still pause at the
    existing approval gate inside the worker."""

    suggestion: SuggestionRead
    task: TaskRead | None
    dispatched: bool


class AgentExecutionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    engagement_id: UUID
    agent: AgentName
    trigger: AgentTrigger
    input: dict[str, Any]
    output: dict[str, Any] | None
    model_provider: str | None
    model_name: str | None
    tokens_in: int | None
    tokens_out: int | None
    cost_usd: float | None = Field(default=None)
    status: AgentExecutionStatus
    error: str | None
    started_at: datetime
    completed_at: datetime | None


class FindingActivityEntry(BaseModel):
    """One row in the finding's activity timeline (pane of glass, Phase 1).

    A flat ``(ts, kind, label, actor, detail, ref)`` row merged from Tasks,
    agent executions, and the audit log so the frontend renders the
    timeline uniformly without knowing the source model.
    """

    ts: str | None = None
    kind: str
    label: str
    actor: str | None = None
    detail: str | None = None
    ref_type: str | None = None
    ref_id: str | None = None


class FindingChatMessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    conversation_id: UUID
    role: Literal["user", "assistant", "system"]
    content: str
    action_payload: dict[str, Any] | None = None
    execution_id: UUID | None = None
    created_at: datetime


class FindingChatState(BaseModel):
    conversation_id: UUID | None = None
    messages: list[FindingChatMessageRead]


class FindingChatRequest(BaseModel):
    conversation_id: UUID | None = None
    message: str = Field(..., min_length=1, max_length=4000)


class FindingChatResponse(BaseModel):
    conversation_id: UUID
    user_message: FindingChatMessageRead
    assistant_message: FindingChatMessageRead
    execution_id: UUID | None = None


class FindingChatActionRequest(BaseModel):
    action_index: int = Field(default=0, ge=0, le=10)


class FindingChatActionResponse(BaseModel):
    message: FindingChatMessageRead
    action_index: int
    action_type: str
    status: Literal["accepted", "dismissed"]
    result: dict[str, Any] = Field(default_factory=dict)
