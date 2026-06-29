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
    created_at: datetime
    updated_at: datetime
