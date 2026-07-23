"""Prompt-mode model preferences (v3 B4a).

Per-(analyst, engagement, prompt-mode) model pinning — the substrate for the
one-agent refactor (B4). A v3 prompt-mode (strategy / analysis / ideation /
coverage_review) can pick a different model on the same engagement.

Separate from v1's ``AgentModelPreference`` (which keys on ``agent_role`` and
is NOT NULL there) so the mode axis is cleanly orthogonal with no agent_role
coupling. Resolution chains: this row -> the analyst's ``users.default_model``
-> the caller's hardcoded default (mirrors v1's ``resolve_agent_model``).
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, uuid7


class AgentPromptMode(enum.StrEnum):
    """The v3 one-agent prompt-modes — different system prompts/personas on a
    single shared Memory thread. The model resolver lets each mode run a
    different model (e.g. ideation on a strong model, analysis on a mini)."""

    strategy = "strategy"
    analysis = "analysis"
    ideation = "ideation"
    coverage_review = "coverage_review"


class AgentModeModelPreference(Base):
    """Per-analyst per-engagement model preference for a v3 prompt-mode."""

    __tablename__ = "agent_mode_model_preference"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    engagement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("engagements.id", ondelete="CASCADE"),
        nullable=False,
    )
    mode: Mapped[AgentPromptMode] = mapped_column(
        Enum(AgentPromptMode, name="agent_prompt_mode"),
        nullable=False,
    )
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default="now()", nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default="now()", nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "engagement_id",
            "mode",
            name="uq_agent_mode_pref_user_engagement_mode",
        ),
    )
