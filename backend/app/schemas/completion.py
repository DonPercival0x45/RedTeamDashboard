"""Deterministic engagement coverage and completion wire models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CompletionRef(BaseModel):
    type: Literal[
        "work_item",
        "coverage_item",
        "task",
        "agent_execution",
        "approval",
        "finding",
        "report_check",
    ]
    id: str


class CompletionCheck(BaseModel):
    key: str
    severity: Literal["blocker", "warning", "info"]
    count: int
    waivable: bool = False
    refs: list[CompletionRef] = Field(default_factory=list)
    message: str


class AcceptedGapCandidate(BaseModel):
    ref: CompletionRef
    key: str
    message: str


class CompletionReadiness(BaseModel):
    work_state: str
    work_state_version: int
    ready: bool
    readiness_hash: str
    checks: list[CompletionCheck]
    accepted_gap_candidates: list[AcceptedGapCandidate] = Field(default_factory=list)
    generated_at: datetime


class CompletionException(BaseModel):
    ref: CompletionRef
    rationale: str = Field(min_length=1, max_length=2000)


class StartCompletionReview(BaseModel):
    expected_work_state_version: int = Field(ge=1)
    readiness_hash: str = Field(min_length=64, max_length=64)
    idempotency_key: str = Field(min_length=1, max_length=100)


class ApproveCompletion(BaseModel):
    expected_work_state_version: int = Field(ge=1)
    readiness_hash: str = Field(min_length=64, max_length=64)
    idempotency_key: str = Field(min_length=1, max_length=100)
    accepted_exceptions: list[CompletionException] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_exception_refs(self) -> ApproveCompletion:
        keys = [(item.ref.type, item.ref.id) for item in self.accepted_exceptions]
        if len(keys) != len(set(keys)):
            raise ValueError("accepted exception references must be unique")
        return self


class ReopenCompletion(BaseModel):
    prior_completion_decision_id: uuid.UUID
    expected_work_state_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=4000)
    idempotency_key: str = Field(min_length=1, max_length=100)


class CompletionDecisionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    engagement_id: uuid.UUID
    action: str
    from_work_state: str
    to_work_state: str
    readiness_hash: str | None = None
    readiness_snapshot: dict[str, Any] | None = None
    accepted_exceptions: list[dict[str, Any]] = Field(default_factory=list)
    strategy_revision_id: uuid.UUID | None = None
    prior_completion_decision_id: uuid.UUID | None = None
    reason: str | None = None
    idempotency_key: str
    decided_by_user_id: uuid.UUID
    created_at: datetime


class CompletionMutationResponse(BaseModel):
    work_state: str
    work_state_version: int
    decision: CompletionDecisionRead
    readiness: CompletionReadiness | None = None


class CoverageItemCreate(BaseModel):
    objective_id: uuid.UUID | None = None
    scope_item_id: uuid.UUID | None = None
    target_kind: str = Field(min_length=1, max_length=40)
    target_key: str = Field(min_length=1, max_length=500)
    activity_category: str
    status: str = "not_started"
    supporting_refs: list[dict[str, Any]] = Field(default_factory=list)
    reason: str | None = Field(default=None, max_length=4000)


class CoverageItemUpdate(BaseModel):
    expected_row_version: int = Field(ge=1)
    status: str
    supporting_refs: list[dict[str, Any]] | None = None
    reason: str | None = Field(default=None, max_length=4000)


class CoverageItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    engagement_id: uuid.UUID
    objective_id: uuid.UUID | None = None
    scope_item_id: uuid.UUID | None = None
    target_kind: str
    target_key: str
    activity_category: str
    status: str
    supporting_refs: list[dict[str, Any]]
    reason: str | None = None
    accepted_by_user_id: uuid.UUID | None = None
    accepted_at: datetime | None = None
    row_version: int
    created_at: datetime
    updated_at: datetime
