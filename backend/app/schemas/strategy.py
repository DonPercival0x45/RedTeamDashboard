"""Wire contracts for the manual Engagement Strategy and work ledger."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models import (
    ObjectivePriority,
    ObjectiveStatus,
    StrategyRevisionState,
    StrategySignalStatus,
    WorkItemExecutor,
    WorkItemFindingRelationship,
    WorkItemPriority,
    WorkItemResolution,
    WorkItemResultState,
    WorkItemStatus,
)


class StrategyRevisionCreate(BaseModel):
    body: str = Field(min_length=1, max_length=100_000)
    summary: str | None = Field(default=None, max_length=300)
    structured: dict[str, Any] = Field(default_factory=dict)
    state: StrategyRevisionState = StrategyRevisionState.draft
    based_on_revision_id: UUID | None = None
    proposal_reason: str | None = Field(default=None, max_length=5_000)


class StrategyRevisionDecision(BaseModel):
    based_on_revision_id: UUID | None = None
    reason: str | None = Field(default=None, max_length=5_000)


class StrategyRevisionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    engagement_id: UUID
    version: int
    state: StrategyRevisionState
    based_on_revision_id: UUID | None
    summary: str | None
    body: str
    structured: dict[str, Any]
    created_by_user_id: UUID | None
    proposed_by_execution_id: UUID | None
    proposal_reason: str | None
    decided_by_user_id: UUID | None
    decided_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ObjectiveCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=20_000)
    success_criteria: str | None = Field(default=None, max_length=20_000)
    status: ObjectiveStatus = ObjectiveStatus.planned
    priority: ObjectivePriority = ObjectivePriority.medium
    display_order: int = Field(default=0, ge=-100_000, le=100_000)
    owner_user_id: UUID | None = None
    target_date: date | None = None


class ObjectiveUpdate(BaseModel):
    expected_row_version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=20_000)
    success_criteria: str | None = Field(default=None, max_length=20_000)
    status: ObjectiveStatus | None = None
    priority: ObjectivePriority | None = None
    display_order: int | None = Field(default=None, ge=-100_000, le=100_000)
    owner_user_id: UUID | None = None
    target_date: date | None = None


class VersionedReason(BaseModel):
    expected_row_version: int = Field(ge=1)
    reason: str | None = Field(default=None, max_length=5_000)


class ObjectiveRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    engagement_id: UUID
    title: str
    description: str | None
    success_criteria: str | None
    status: ObjectiveStatus
    priority: ObjectivePriority
    display_order: int
    owner_user_id: UUID | None
    target_date: date | None
    created_by_user_id: UUID | None
    completed_by_user_id: UUID | None
    completed_at: datetime | None
    row_version: int
    created_at: datetime
    updated_at: datetime


class WorkItemFindingInput(BaseModel):
    finding_id: UUID
    relationship: WorkItemFindingRelationship = WorkItemFindingRelationship.related


class WorkItemCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=20_000)
    rationale: str | None = Field(default=None, max_length=20_000)
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=100)
    status: WorkItemStatus = WorkItemStatus.ready
    priority: WorkItemPriority = WorkItemPriority.medium
    executor_type: WorkItemExecutor = WorkItemExecutor.unassigned
    objective_id: UUID | None = None
    parent_work_item_id: UUID | None = None
    assigned_user_id: UUID | None = None
    due_at: datetime | None = None
    finding_links: list[WorkItemFindingInput] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def unique_links(self) -> WorkItemCreate:
        keys = {(row.finding_id, row.relationship) for row in self.finding_links}
        if len(keys) != len(self.finding_links):
            raise ValueError("finding_links must not contain duplicates")
        return self


class WorkItemUpdate(BaseModel):
    expected_row_version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=20_000)
    rationale: str | None = Field(default=None, max_length=20_000)
    acceptance_criteria: list[str] | None = Field(default=None, max_length=100)
    priority: WorkItemPriority | None = None
    executor_type: WorkItemExecutor | None = None
    objective_id: UUID | None = None
    parent_work_item_id: UUID | None = None
    assigned_user_id: UUID | None = None
    due_at: datetime | None = None


class WorkItemBlock(BaseModel):
    expected_row_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=5_000)


class WorkItemResolve(BaseModel):
    expected_row_version: int = Field(ge=1)
    outcome: WorkItemResolution
    note: str | None = Field(default=None, max_length=20_000)
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list, max_length=100)


class WorkItemFindingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    work_item_id: UUID
    finding_id: UUID
    relationship: WorkItemFindingRelationship
    created_at: datetime


class WorkItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    engagement_id: UUID
    objective_id: UUID | None
    parent_work_item_id: UUID | None
    title: str
    description: str | None
    rationale: str | None
    acceptance_criteria: list[str]
    status: WorkItemStatus
    priority: WorkItemPriority
    executor_type: WorkItemExecutor
    assigned_user_id: UUID | None
    created_by_user_id: UUID | None
    created_by_execution_id: UUID | None
    started_at: datetime | None
    blocked_reason: str | None
    due_at: datetime | None
    resolution_outcome: WorkItemResolution | None
    resolution_note: str | None
    completed_by_user_id: UUID | None
    completed_at: datetime | None
    row_version: int
    created_at: datetime
    updated_at: datetime
    finding_links: list[WorkItemFindingRead] = Field(default_factory=list)


class WorkItemFindingAdd(BaseModel):
    finding_id: UUID
    relationship: WorkItemFindingRelationship = WorkItemFindingRelationship.related
    expected_work_item_version: int = Field(ge=1)


class WorkItemResultCreate(BaseModel):
    summary: str = Field(min_length=1, max_length=100_000)
    structured: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    proposed_by_execution_id: UUID | None = None


class WorkItemResultRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    work_item_id: UUID
    revision: int
    state: WorkItemResultState
    summary: str
    structured: dict[str, Any]
    evidence_refs: list[dict[str, Any]]
    proposed_by_user_id: UUID | None
    proposed_by_execution_id: UUID | None
    decided_by_user_id: UUID | None
    decided_at: datetime | None
    created_at: datetime


class WorkItemResultAccept(BaseModel):
    expected_work_item_version: int = Field(ge=1)
    resolve_work_item: bool = False
    resolution_outcome: WorkItemResolution | None = None
    resolution_note: str | None = Field(default=None, max_length=20_000)
    share_with_strategy: bool = False

    @model_validator(mode="after")
    def resolution_is_complete(self) -> WorkItemResultAccept:
        if self.resolve_work_item and self.resolution_outcome is None:
            raise ValueError("resolution_outcome is required when resolve_work_item=true")
        if not self.resolve_work_item and self.resolution_outcome is not None:
            raise ValueError("resolution_outcome requires resolve_work_item=true")
        return self


class WorkItemResultReject(BaseModel):
    reason: str | None = Field(default=None, max_length=5_000)


class SignalCreate(BaseModel):
    signal_type: str = Field(min_length=1, max_length=80)
    summary: str = Field(min_length=1, max_length=20_000)
    confidence: str = Field(default="medium", pattern="^(low|medium|high)$")
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    suggested_effect: dict[str, Any] = Field(default_factory=dict)
    dedup_key: str = Field(min_length=1, max_length=200)
    source_work_item_result_id: UUID | None = None
    source_execution_id: UUID | None = None


class SignalDecision(BaseModel):
    reason: str | None = Field(default=None, max_length=5_000)


class StrategySignalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    engagement_id: UUID
    source_finding_id: UUID | None
    source_work_item_id: UUID | None
    source_work_item_result_id: UUID | None
    source_execution_id: UUID | None
    signal_type: str
    summary: str
    confidence: str
    evidence_refs: list[dict[str, Any]]
    suggested_effect: dict[str, Any]
    dedup_key: str
    status: StrategySignalStatus
    decided_by_user_id: UUID | None
    decided_at: datetime | None
    created_at: datetime
    updated_at: datetime


class WorkItemRollupBucket(BaseModel):
    remaining: int = 0
    blocked: int = 0
    proposals: int = 0
    deferred: int = 0


class WorkItemRollup(BaseModel):
    by_finding: dict[str, WorkItemRollupBucket]
    engagement: WorkItemRollupBucket


class ResultDecisionResponse(BaseModel):
    work_item: WorkItemRead
    result: WorkItemResultRead
    strategy_signal: StrategySignalRead | None = None
    rollup: WorkItemRollup


class CheckpointCreate(BaseModel):
    narrative: str | None = Field(default=None, max_length=50_000)


class CheckpointRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    engagement_id: UUID
    strategy_revision_id: UUID | None
    created_by_user_id: UUID | None
    created_by_execution_id: UUID | None
    material_event_cursor: datetime
    facts: dict[str, Any]
    narrative: str | None
    created_at: datetime


class ResumeResponse(BaseModel):
    current_focus: dict[str, Any]
    since_checkpoint: dict[str, Any]
    active_work: list[WorkItemRead]
    blocked_work: list[WorkItemRead]
    decisions_required: list[dict[str, Any]]
    recommended_starting_records: list[dict[str, Any]]
    coverage_summary: dict[str, Any]
    report_readiness: dict[str, Any]
    generated_at: datetime
    current_tasks: list[dict[str, Any]] = Field(default_factory=list)
    recent_findings: list[dict[str, Any]] = Field(default_factory=list)
    recently_closed: list[dict[str, Any]] = Field(default_factory=list)
    recent_activity: list[dict[str, Any]] = Field(default_factory=list)
