"""Read models for the non-destructive, entity-centred Findings workspace."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.models import FindingExclusion, FindingPhase, FindingStatus, Severity
from app.schemas.finding import MAX_FINDING_SUMMARY_CHARS, FindingRead

FindingWorkspaceBucket = Literal[
    "needs_review",
    "actionable",
    "inventory",
    "resolved_excluded",
]
FindingHierarchyKind = Literal[
    "ip",
    "domain",
    "service",
    "subdomain",
    "web_surface",
    "finding",
    "other",
]


class FindingHierarchyFindingRef(BaseModel):
    id: UUID
    title: str
    tool: str | None = None
    target: str | None = None
    severity: Severity
    phase: FindingPhase
    status: FindingStatus
    exclusion: FindingExclusion | None = None
    observed_at: datetime | None = None
    created_at: datetime
    bucket: FindingWorkspaceBucket


class FindingHierarchyRollup(BaseModel):
    max_severity: Severity = Severity.info
    needs_review: int = 0
    actionable: int = 0
    inventory: int = 0
    resolved_excluded: int = 0
    distinct_findings: int = 0
    latest_at: datetime | None = None


class FindingHierarchyItem(BaseModel):
    id: str
    kind: FindingHierarchyKind
    canonical_key: str
    label: str
    value: str | None = None
    ip: str | None = None
    hostname: str | None = None
    protocol: str | None = None
    port: int | None = None
    service: str | None = None
    url: str | None = None
    finding_refs: list[FindingHierarchyFindingRef] = Field(default_factory=list)
    children: list[FindingHierarchyItem] = Field(default_factory=list)
    rollup: FindingHierarchyRollup = Field(default_factory=FindingHierarchyRollup)
    create_finding_allowed: bool = True
    suggested_title: str | None = None
    suggested_target: str | None = None


class FindingHierarchyCounts(BaseModel):
    focus: int = 0
    needs_review: int = 0
    actionable: int = 0
    inventory: int = 0
    resolved_excluded: int = 0
    distinct_findings: int = 0


class FindingHierarchyResponse(BaseModel):
    assets: list[FindingHierarchyItem] = Field(default_factory=list)
    ungrouped: list[FindingHierarchyItem] = Field(default_factory=list)
    counts: FindingHierarchyCounts
    generated_at: datetime
    projection_version: str = "finding-hierarchy-v1"


class FindingDuplicateCandidate(BaseModel):
    id: UUID
    title: str
    target: str | None = None
    severity: Severity
    status: FindingStatus
    exclusion: FindingExclusion | None = None
    match_reason: str


class FindingFromHierarchyItemCreate(BaseModel):
    item_id: str = Field(min_length=8, max_length=80)
    title: str = Field(min_length=1, max_length=300)
    summary: str | None = Field(default=None, max_length=MAX_FINDING_SUMMARY_CHARS)
    severity: Severity = Severity.info
    phase: FindingPhase = FindingPhase.general
    target: str | None = Field(default=None, max_length=500)
    observed_at: datetime | None = None
    duplicate_decision: Literal["review", "create_anyway"] = "review"
    reviewed_duplicate_ids: list[UUID] = Field(default_factory=list, max_length=10)
    idempotency_key: UUID

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("title cannot be blank")
        return normalized

    @field_validator("summary", "target")
    @classmethod
    def _normalize_optional(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class FindingFromHierarchyItemResponse(BaseModel):
    state: Literal["duplicate_warning", "created"]
    candidates: list[FindingDuplicateCandidate] = Field(default_factory=list)
    finding: FindingRead | None = None


FindingHierarchyItem.model_rebuild()
