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

from pydantic import BaseModel, Field, field_validator

from app.models import FindingExclusion, FindingPhase, FindingStatus, Severity


def _normalize_tags(raw: list[str] | None) -> list[str]:
    """Trim, drop empties, de-dup (case-sensitive, order-preserving), and
    cap so a chatty analyst can't blow up the column. Shared by the
    create + update shapes."""
    if not raw:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for tag in raw:
        if not isinstance(tag, str):
            continue
        tag = tag.strip()[:40]
        if not tag or tag in seen:
            continue
        seen.add(tag)
        out.append(tag)
    return out[:20]


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
    # v1.4.0 (part 2): Nessus-style ingest grouping. When set, this row
    # is a "parent" with per-hit records inside ``data.items[]``. When
    # null, this row is a pre-grouping or ungroupable per-hit finding.
    # See docs/FINDINGS_GROUPING.md.
    group_key: str | None = None
    # v1.4.0 (part 2): convenience for the Findings table so it doesn't
    # have to introspect ``data.items[]`` on every render. 0 for
    # un-grouped rows; N for grouped rows where N = len(data.items).
    item_count: int = 0
    validated_at: datetime | None = None
    observed_at: datetime | None = None
    burp_serial_number: str | None = None
    created_at: datetime
    # v1.4.7: free-form analyst tags. Empty list by default; populated
    # via PATCH /findings/{id} or at manual-create time.
    tags: list[str] = Field(default_factory=list)


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
    # v1.4.7: replace the whole tag list. Distinguish not-set from
    # set-to-empty via ``model_fields_set`` — pass ``[]`` to clear.
    tags: list[str] | None = None

    @field_validator("tags")
    @classmethod
    def _normalize_tags_field(cls, v: list[str] | None) -> list[str] | None:
        return _normalize_tags(v) if v is not None else None


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
    # v1.4.7: optional tags at create time.
    tags: list[str] = Field(default_factory=list)

    @field_validator("tags")
    @classmethod
    def _normalize_tags_field(cls, v: list[str] | None) -> list[str]:
        return _normalize_tags(v)


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


class RegroupProposal(BaseModel):
    """One proposed group in a POST /findings/regroup/preview response.

    Every finding_id in ``member_ids`` currently has ``group_key IS NULL``
    and would fold into a row with ``group_key = <group_key>``. If
    ``absorbs_into_existing_parent_id`` is set, that parent already
    exists (from a prior grouped ingest) and the members would be
    appended to its ``details['items']``; else a new parent row is
    minted when the analyst applies the group.
    """

    group_key: str
    tool: str
    proposed_title: str
    member_ids: list[UUID] = Field(..., min_length=2)
    projected_severity: Severity
    projected_item_count: int = Field(
        ...,
        description=(
            "How many entries will land in ``details['items']`` after the "
            "apply. Usually == len(member_ids); higher when a member row "
            "already carried a multi-item data blob (e.g. subfinder's "
            "subdomains list)."
        ),
    )
    absorbs_into_existing_parent_id: UUID | None = None


class RegroupPreview(BaseModel):
    """Dry-run response for POST /engagements/{slug}/findings/regroup/preview.

    ``proposals`` only contains groups of size >= 2 — a "group" of one
    row is meaningless. ``ungroupable_count`` surfaces the number of
    rows the tool vocab couldn't key (unknown source_tool, or manual
    entries without a caller-supplied key), so the analyst can see why
    nothing happened when the response is empty.
    """

    proposals: list[RegroupProposal] = Field(default_factory=list)
    scanned_row_count: int
    ungroupable_count: int


class RegroupApplyRequest(BaseModel):
    """Body for POST /engagements/{slug}/findings/regroup/apply."""

    group_keys: list[str] = Field(
        ...,
        min_length=1,
        max_length=200,
        description=(
            "Group keys the analyst approved in the preview modal. Only "
            "these get merged; keys omitted here are left as-is even if "
            "the preview surfaced them."
        ),
    )


class RegroupApplyResult(BaseModel):
    """What the apply endpoint returns per approved group."""

    group_key: str
    parent_id: UUID
    absorbed_member_count: int
    final_item_count: int
    final_severity: Severity


class RepairGroupsResult(BaseModel):
    """Response for POST /engagements/{slug}/findings/repair-groups (v1.4.3).

    Non-destructive maintenance pass over an existing engagement's grouped
    rows: rebuilds ``details['items']`` from each parent's soft-deleted
    source rows using the current :func:`extract_items` vocab; migrates
    legacy per-tool group keys (subfinder / crt_sh / dns) into the
    unified ``subdomains:{apex}`` shape; folds ungrouped rows that would
    now share a key into their parent.
    """

    parents_scanned: int
    parents_items_repaired: int
    parents_rekeyed: int
    parents_merged: int
    ungrouped_absorbed: int
    total_items_after: int


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
