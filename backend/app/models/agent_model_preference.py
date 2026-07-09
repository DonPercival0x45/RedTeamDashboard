from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, uuid7
from app.models.suggestion import AgentName


class AgentModelPreference(Base):
    """Per-analyst per-engagement pinning: which LLM model to use for a
    given agent role. Backing table for Settings > Configurations.

    Resolution at run-time chains: this row (if present) -> the analyst's
    ``users.default_model`` -> the agent's hardcoded default. The provider
    key comes from the analyst's ephemeral BYO cache independently.

    Storage type for ``agent_role`` reuses the ``agent_name`` enum so the
    same values (``strategic``/``tactical``/``correlate``) that identify
    an ``AgentExecution`` also identify a routing preference. The API
    surface currently accepts only the three engagement-scoped roles;
    widening to ``planner``/``triage`` later needs no migration.
    """

    __tablename__ = "agent_model_preference"

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
    agent_role: Mapped[AgentName] = mapped_column(
        Enum(AgentName, name="agent_name", create_type=False),
        nullable=False,
    )
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default="now()",
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default="now()",
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "engagement_id",
            "agent_role",
            name="uq_agent_model_pref_user_engagement_role",
        ),
    )
