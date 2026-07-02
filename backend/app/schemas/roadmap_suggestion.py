"""Wire schemas for the tenant-global "suggestion box" feature.

Mirrors :mod:`app.models.roadmap_suggestion`. The agent's pros/cons are
typed as ``list[str]`` over the wire — concise, easy to render as bullet
lists in the UI.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.roadmap_suggestion import RoadmapSuggestionStatus


class RoadmapSuggestionCreate(BaseModel):
    """POST body — analyst types a suggestion into the textarea."""

    body: str = Field(min_length=4, max_length=8000)


class RoadmapSuggestionDecision(BaseModel):
    """PATCH body — admin approves or rejects.

    ``status`` must be ``approved`` or ``rejected``; flipping back to
    ``pending_review`` isn't allowed (re-submit the idea instead).
    """

    status: RoadmapSuggestionStatus = Field(
        ..., description="approved or rejected — pending_review is rejected as input."
    )
    note: str | None = Field(default=None, max_length=2000)


class RoadmapSuggestionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    author_user_id: UUID | None
    body: str
    agent_pros: list[str]
    agent_cons: list[str]
    agent_summary: str | None
    agent_execution_id: UUID | None
    status: RoadmapSuggestionStatus
    reviewed_by_user_id: UUID | None
    reviewed_at: datetime | None
    review_note: str | None
    source: str
    # v0.16.0: analyst-set or LLM-set. 1..10, 1 = highest. NULL = unranked.
    priority: int | None = None
    # v0.16.0: when set, this row was merged into another suggestion by
    # an analyst-confirmed combine. Hidden from list by default.
    combined_into_id: UUID | None = None
    # v1.1.0: "Mark completed" markers — orthogonal to ``status``. When
    # ``implemented_at`` is set, the renderer emits this row in the
    # Shipped section of ROADMAP.md instead of the Open section.
    implemented_at: datetime | None = None
    implemented_by_user_id: UUID | None = None
    created_at: datetime
    updated_at: datetime


class CompletionUpdate(BaseModel):
    """PATCH body — admin marks an approved row shipped or reopens it.

    ``completed=true`` stamps ``implemented_at`` (server-side ``now()``)
    and ``implemented_by_user_id``. ``completed=false`` clears both.
    """

    completed: bool


class PriorityUpdate(BaseModel):
    """PATCH body — analyst sets a per-row priority. NULL clears."""

    priority: int | None = Field(default=None, ge=1, le=10)


class CombineRequest(BaseModel):
    """POST body — analyst confirms a merge. The URL primary id is the
    survivor; the ``member_ids`` in the body fold into it."""

    member_ids: list[UUID] = Field(..., min_length=1, max_length=50)


class CombineClusterRead(BaseModel):
    """One proposed merge from the LLM combine-detect op."""

    primary_id: UUID
    member_ids: list[UUID]
    reasoning: str


class CombineDetectResponse(BaseModel):
    clusters: list[CombineClusterRead]
    pool_size: int
    model: str
    tokens_in: int
    tokens_out: int
    execution_id: UUID | None = None
    error: str | None = None


class RankedRowRead(BaseModel):
    id: UUID
    priority: int
    reasoning: str


class BulkRankResponse(BaseModel):
    rankings: list[RankedRowRead]
    pool_size: int
    applied: bool
    model: str
    tokens_in: int
    tokens_out: int
    execution_id: UUID | None = None
    error: str | None = None


class BulkRankApplyRequest(BaseModel):
    """POST body — admin confirms a rank result and applies it. The
    rankings echo back what the LLM produced so the client can't
    accidentally apply a stale set."""

    rankings: list[RankedRowRead] = Field(..., min_length=1, max_length=200)
