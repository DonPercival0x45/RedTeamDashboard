"""API schemas for the playbook runner (A3b)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.playbook_execution import PlaybookStepExecutionStatus
from app.services.playbook.policy import (
    normalize_entity_types,
    validate_category,
)


class PlaybookStepRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sort_order: int
    tool_slug: str
    args_template: dict
    satisfies_node_ids: list[str]
    description: str | None = None


class PlaybookRead(BaseModel):
    """Catalog entry — list view."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    version: int
    name: str
    description: str | None = None
    applies_to_asset_class: str
    applicable_entity_types: list[str] = Field(default_factory=list)
    category: str = "other"
    origin: str = "system"
    created_by: uuid.UUID | None = None
    supersedes_id: uuid.UUID | None = None
    can_edit: bool = False
    has_runs: bool = False
    active: bool
    step_count: int = 0
    required_executor: str = "internal"
    required_credentials: list[str] = Field(default_factory=list)
    step_preview: list[str] = Field(default_factory=list)
    expands_targets: bool = False
    execution_paths: list[str] = Field(default_factory=list)


class PlaybookDetail(PlaybookRead):
    """Catalog entry — full steps."""

    steps: list[PlaybookStepRead] = Field(default_factory=list)


class PlaybookPlanPayload(BaseModel):
    """Targets used to generate an authoritative execution plan."""

    playbook_slug: str
    playbook_version: int | None = None
    scope_subset: list[str] = Field(default_factory=list, max_length=100)
    executor: str | None = None


class PlaybookRunPayload(PlaybookPlanPayload):
    """A run request must prove review of the current server plan."""

    plan_sha256: str | None = Field(default=None, min_length=64, max_length=64)


class PlaybookExecutionPlanStepRead(BaseModel):
    step_id: uuid.UUID
    sort_order: int
    tool_slug: str
    description: str | None = None
    transport: str
    risk: str
    credential: str | None = None
    arguments_sha256: str
    coverage_node_ids: list[str] = Field(default_factory=list)
    target_count: int
    expands_targets: bool
    target_source: str | None = None
    on_error: str


class PlaybookExecutionPlanRead(BaseModel):
    format_version: int
    plan_sha256: str
    playbook_id: uuid.UUID
    playbook_slug: str
    playbook_version: int
    playbook_name: str
    approval_required: bool
    required_executor: str
    execution_paths: list[str] = Field(default_factory=list)
    required_credentials: list[str] = Field(default_factory=list)
    scope_subset: list[str] = Field(default_factory=list)
    minimum_calls: int
    maximum_calls: int
    dynamic_expansion: bool
    steps: list[PlaybookExecutionPlanStepRead] = Field(default_factory=list)
    safety_notes: list[str] = Field(default_factory=list)


class EvidenceArtifactSummaryRead(BaseModel):
    """Bounded evidence metadata included with a step receipt."""

    id: uuid.UUID
    finding_id: uuid.UUID | None = None
    sha256: str
    size_bytes: int
    truncated: bool
    redacted: bool


class EvidenceArtifactRead(EvidenceArtifactSummaryRead):
    """Full redacted evidence payload, fetched only when requested."""

    engagement_id: uuid.UUID
    playbook_run_id: uuid.UUID | None = None
    playbook_step_execution_id: uuid.UUID | None = None
    kind: str
    source_tool: str
    target: str
    payload: dict = Field(default_factory=dict)
    captured_at: datetime


class PlaybookStepExecutionRead(BaseModel):
    """One durable step/target execution receipt."""

    id: uuid.UUID
    playbook_step_id: uuid.UUID | None = None
    sort_order: int
    tool_slug: str
    target: str
    transport: str
    attempt: int
    status: PlaybookStepExecutionStatus
    arguments: dict = Field(default_factory=dict)
    started_at: datetime
    completed_at: datetime | None = None
    duration_ms: int | None = None
    error: str | None = None
    evidence: EvidenceArtifactSummaryRead | None = None


class PlaybookRunRead(BaseModel):
    """One playbook run — status + counts + timing."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    engagement_id: uuid.UUID
    engagement_slug: str
    playbook_id: uuid.UUID
    playbook_slug: str
    playbook_version: int
    status: str
    executor: str = "internal"
    scope_subset: list = Field(default_factory=list)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    steps_total: int = 0
    steps_succeeded: int = 0
    steps_failed: int = 0
    findings_new: int = 0
    findings_unvalidated: int = 0
    findings_high_severity: int = 0
    findings_total: int = 0
    last_error: str | None = None
    plan_sha256: str | None = None
    planned_at: datetime | None = None
    execution_plan: PlaybookExecutionPlanRead | None = None
    # Request identity is durable even though execution happens in a worker.
    requested_by: uuid.UUID | None = None
    # A5 approval attribution — populated when the run passed through the
    # awaiting_approval gate.
    approved_by: uuid.UUID | None = None
    approved_at: datetime | None = None
    approval_reason: str | None = None
    rejected_by: uuid.UUID | None = None
    rejected_at: datetime | None = None
    rejection_reason: str | None = None
    # Populated by the single-run detail endpoint; list/mutation responses keep
    # this empty so large histories do not duplicate receipt metadata.
    step_executions: list[PlaybookStepExecutionRead] = Field(default_factory=list)


class PlaybookApprovalPayload(BaseModel):
    """Request body for approve/reject endpoints.

    ``reason`` is optional on approve (audit context), required on reject
    (analyst needs to tell the requestor why).
    """

    reason: str | None = None


class PlaybookStepCreatePayload(BaseModel):
    """One analyst-authored step.

    Arguments and coverage are accepted for compatibility with older clients,
    but authoring endpoints replace them with server-owned safe templates and
    empty coverage mappings.
    """

    tool_slug: str = Field(min_length=1, max_length=120)
    source_step_id: uuid.UUID | None = None
    args_template: dict = Field(default_factory=dict)
    satisfies_node_ids: list[str] = Field(default_factory=list)
    sort_order: int | None = Field(default=None, ge=0, le=10_000)
    description: str | None = Field(default=None, max_length=500)


class PlaybookCreatePayload(BaseModel):
    """Request body for an analyst-authored catalog entry."""

    slug: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=120)
    name: str = Field(min_length=1, max_length=200)
    applies_to_asset_class: str | None = Field(default=None, max_length=80)
    applicable_entity_types: list[str] = Field(default_factory=list, max_length=8)
    category: str = "other"
    description: str | None = Field(default=None, max_length=4_000)
    active: bool = False
    steps: list[PlaybookStepCreatePayload] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_catalog_metadata(self) -> PlaybookCreatePayload:
        values = self.applicable_entity_types or (
            [self.applies_to_asset_class] if self.applies_to_asset_class else []
        )
        try:
            self.applicable_entity_types = normalize_entity_types(values)
            self.category = validate_category(self.category)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        self.applies_to_asset_class = self.applicable_entity_types[0]
        return self


class PlaybookNewVersionPayload(BaseModel):
    """Complete replacement recipe used to edit through immutable versions."""

    expected_supersedes_id: uuid.UUID
    expected_version: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=200)
    applicable_entity_types: list[str] = Field(min_length=1, max_length=8)
    category: str = "other"
    description: str | None = Field(default=None, max_length=4_000)
    active: bool = False
    steps: list[PlaybookStepCreatePayload] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def validate_catalog_metadata(self) -> PlaybookNewVersionPayload:
        try:
            self.applicable_entity_types = normalize_entity_types(self.applicable_entity_types)
            self.category = validate_category(self.category)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        return self


class PlaybookPatchPayload(BaseModel):
    """Legacy in-place metadata patch for a never-run custom recipe."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4_000)
    applies_to_asset_class: str | None = Field(default=None, max_length=80)
    applicable_entity_types: list[str] | None = Field(default=None, max_length=8)
    category: str | None = None
    active: bool | None = None

    @model_validator(mode="after")
    def validate_catalog_metadata(self) -> PlaybookPatchPayload:
        if self.applicable_entity_types is not None:
            try:
                self.applicable_entity_types = normalize_entity_types(self.applicable_entity_types)
            except ValueError as exc:
                raise ValueError(str(exc)) from exc
        if self.category is not None:
            try:
                self.category = validate_category(self.category)
            except ValueError as exc:
                raise ValueError(str(exc)) from exc
        return self


class PlaybookToolRead(BaseModel):
    slug: str
    name: str
    description: str
    target_kinds: list[str]
    transport: str
    risk: str
    credential: str | None = None


class PlaybookCatalogOptionsRead(BaseModel):
    categories: list[str]
    entity_types: list[str]
    tools: list[PlaybookToolRead]


class PlaybookStepPatchPayload(BaseModel):
    """Request body for PATCH /playbooks/{slug}/steps/{step_id}."""

    tool_slug: str | None = Field(default=None, min_length=1, max_length=120)
    args_template: dict | None = None
    satisfies_node_ids: list[str] | None = None
    sort_order: int | None = None
    description: str | None = None
