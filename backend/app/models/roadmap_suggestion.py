"""Tenant-global "suggestion box" rows.

Distinct from :class:`app.models.suggestion.Suggestion` (which is the
Strategic-agent's engagement-scoped output, tied to a Finding). A
``RoadmapSuggestion`` is an analyst-submitted product idea — the agent reads
the suggestion + the project's CHARTER/HANDOFF docs and emits pros/cons; an
admin approves or rejects; approved items export to ``ROADMAP.md`` for
Claude Code to pick up as future PR work.

Shared across all tenant users (no per-user filter at read time, mirroring
:class:`app.models.engagement.Engagement`). ``author_user_id`` is provenance
only — not access control.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, uuid7


class RoadmapSuggestionStatus(enum.StrEnum):
    """Lifecycle. ``pending_review`` — agent has produced pros/cons, awaiting
    an admin Yes/No. ``approved`` — included in the ROADMAP.md export.
    ``rejected`` — kept for audit but excluded from export."""

    pending_review = "pending_review"
    approved = "approved"
    rejected = "rejected"


class RoadmapSuggestion(Base, TimestampMixin):
    __tablename__ = "roadmap_suggestions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    author_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)

    # Structured-output from the PlanningAgent.
    agent_pros: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    agent_cons: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    agent_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    agent_execution_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_executions.id", ondelete="SET NULL"),
        nullable=True,
    )

    status: Mapped[RoadmapSuggestionStatus] = mapped_column(
        Enum(RoadmapSuggestionStatus, name="roadmap_suggestion_status"),
        default=RoadmapSuggestionStatus.pending_review,
        nullable=False,
        index=True,
    )
    reviewed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Where the row came from. ``ui`` means an analyst submitted via
    # /settings/feedback; ``discord:<username>`` means the Discord bot
    # relayed a message from a configured channel. Used for the outbound
    # webhook loop-prevention guard.
    source: Mapped[str] = mapped_column(
        String(120), nullable=False, default="ui", server_default="ui"
    )
