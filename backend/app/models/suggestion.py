from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, uuid7


class SuggestionKind(enum.StrEnum):
    """``task`` → analyst accepts to mint a Task. ``ephemeral`` → recommends
    spinning up a scan_box / attack_box. ``note`` → freeform observation
    Strategic wants surfaced."""

    task = "task"
    ephemeral = "ephemeral"
    note = "note"
    work_item = "work_item"
    strategy_revision = "strategy_revision"


class SuggestionStatus(enum.StrEnum):
    open = "open"
    accepted = "accepted"
    dismissed = "dismissed"


class AgentName(enum.StrEnum):
    """The orchestrator agent that produced the row. Mirrored on
    ``AgentExecution`` so a Suggestion can be traced back to the run.

    ``planner`` is tenant-global (the /settings/suggestions "suggestion box"
    agent), unlike strategic/tactical which are engagement-scoped — so its
    ``AgentExecution`` rows carry a NULL ``engagement_id``."""

    strategic = "strategic"
    tactical = "tactical"
    planner = "planner"
    triage = "triage"
    tool_review = "tool_review"
    correlate = "correlate"
    engagement_strategist = "engagement_strategist"


class Suggestion(Base, TimestampMixin):
    """A recommendation surfaced by Strategic (or Tactical) for analyst review.

    Pure-watcher invariant: nothing happens until the analyst accepts. On
    accept, a kind=``task`` suggestion becomes a ``Task`` (``task_id`` back-
    reference is set).
    """

    __tablename__ = "suggestions"
    __table_args__ = (
        Index(
            "uq_suggestions_open_proposal_key",
            "engagement_id",
            "kind",
            "proposal_key",
            unique=True,
            postgresql_where=text("status = 'open' AND proposal_key IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    engagement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("engagements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    finding_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("findings.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    kind: Mapped[SuggestionKind] = mapped_column(
        Enum(SuggestionKind, name="suggestion_kind"), nullable=False
    )
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, nullable=False
    )
    status: Mapped[SuggestionStatus] = mapped_column(
        Enum(SuggestionStatus, name="suggestion_status"),
        default=SuggestionStatus.open,
        nullable=False,
        index=True,
    )
    created_by_agent: Mapped[AgentName] = mapped_column(
        Enum(AgentName, name="agent_name"), nullable=False
    )
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="SET NULL")
    )
    proposal_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    context_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    objective_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("engagement_objectives.id", ondelete="SET NULL"),
    )
    work_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("work_items.id", ondelete="SET NULL"),
        index=True,
    )
