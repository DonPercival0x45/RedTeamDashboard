"""Wire-format model for persisted findings.

The shape mirrors the SSE ``finding.created`` event (``tool``/``args``/``data``)
so the frontend can hydrate the findings table from the DB on load and append
live events without two code paths. The worker stores findings with
``details = {thread_id, args, **tool_data}``; the API unpacks that back out (see
``_finding_to_read`` in ``app.api.engagements``).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.models import FindingExclusion, FindingPhase, FindingStatus, Severity


class FindingRead(BaseModel):
    id: UUID
    thread_id: str | None = None
    tool: str | None = None
    target: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    data: dict[str, Any] = Field(default_factory=dict)
    severity: Severity
    title: str
    summary: str | None = None
    phase: FindingPhase
    status: FindingStatus
    # v1.4.0: analyst-set reportability marker. Null = default (included
    # in report). See :class:`FindingExclusion` for the meaning of each
    # value.
    exclusion: FindingExclusion | None = None
    validated_at: datetime | None = None
    observed_at: datetime | None = None
    burp_serial_number: str | None = None
    created_at: datetime


class FindingUpdate(BaseModel):
    """Editable fields on a persisted finding. Only set fields are applied.

    ``exclusion`` distinguishes not-set from set-to-null via
    ``model_fields_set`` — pass ``null`` explicitly to clear an
    existing exclusion.
    """

    title: str | None = None
    summary: str | None = None
    severity: Severity | None = None
    phase: FindingPhase | None = None
    exclusion: FindingExclusion | None = None


class FindingCreate(BaseModel):
    """Body for POST /engagements/{slug}/findings — manual analyst-drafted
    finding, distinct from the bulk /findings/import path (which takes a
    list). Only ``title`` is required; everything else has a sensible
    default the analyst can override in the modal.
    """

    title: str = Field(..., min_length=1, max_length=300)
    summary: str | None = None
    severity: Severity = Severity.info
    phase: FindingPhase = FindingPhase.general
    target: str | None = None
    observed_at: datetime | None = None


class CorrelateGroup(BaseModel):
    """One proposed cluster of related findings.

    The agent groups by root cause / entity / attack path — anything the
    analyst would themselves have merged if they'd noticed. The
    ``rationale`` is shown to the analyst before they approve so they
    know why the group exists.
    """

    rationale: str = Field(
        ...,
        description="One-line reason these findings should be treated as one.",
    )
    finding_ids: list[UUID] = Field(
        ...,
        min_length=2,
        description=(
            "IDs of the findings in this group. The first ID is the "
            "proposed parent (survives the merge); the rest are children "
            "(soft-deleted). Minimum 2 — a group of 1 is meaningless."
        ),
    )


class CorrelateResponse(BaseModel):
    """Response for POST /engagements/{slug}/findings/correlate."""

    groups: list[CorrelateGroup] = Field(default_factory=list)
    total_considered: int = Field(
        ...,
        description=(
            "Number of open findings the agent looked at. Helps the "
            "analyst calibrate 'no groups found' — 0 considered vs. 47 "
            "considered are very different empty states."
        ),
    )


class MergeRequest(BaseModel):
    """Body for POST /findings/{parent_id}/merge."""

    child_ids: list[UUID] = Field(
        ...,
        min_length=1,
        max_length=50,
        description=(
            "IDs of findings to fold into the parent. Cap at 50 to keep "
            "the audit_log payload from ballooning."
        ),
    )


class FindingValidate(BaseModel):
    # 'validated' promotes to report-eligible; the others remove it from the
    # report while keeping an audit trail.
    decision: FindingStatus = FindingStatus.validated
    reason: str | None = None


class FindingSummaryCreate(BaseModel):
    """Body for POST /findings/{id}/summaries.

    A new entry is APPENDED to the immutable history; nothing is replaced.
    Empty bodies are rejected — clearing the displayed summary is done by
    leaving the cache untouched and showing only the history.
    """

    body: str = Field(min_length=1, max_length=20_000)


class FindingSummaryRead(BaseModel):
    """One row from the finding's summary history.

    Author display fields are joined at read time; they survive the row's
    author being deleted (set null) so the historical record never loses
    its "who said this" attribution to a placeholder.
    """

    id: UUID
    finding_id: UUID
    body: str
    author_user_id: UUID | None = None
    author_email: str | None = None
    author_display_name: str | None = None
    created_at: datetime


class AttachmentRead(BaseModel):
    """Metadata for a finding attachment. Raw bytes served via GET /attachments/{id}."""

    id: UUID
    finding_id: UUID
    filename: str
    content_type: str
    size_bytes: int
    created_at: datetime


class EntityFindingRef(BaseModel):
    id: UUID
    title: str
    tool: str | None = None
    severity: Severity
    phase: FindingPhase


class EntityRead(BaseModel):
    """A correlated entity derived from findings (CHARTER Idea 4)."""

    type: str  # email | ip | cidr | domain | subdomain | url | host
    value: str
    count: int
    severity: Severity
    first_seen: datetime
    last_seen: datetime
    findings: list[EntityFindingRef]
