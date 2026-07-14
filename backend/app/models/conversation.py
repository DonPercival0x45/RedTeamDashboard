from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, uuid7


class ConversationContextType(enum.StrEnum):
    finding = "finding"
    engagement = "engagement"


class Conversation(Base, TimestampMixin):
    """Finding-scoped AI assistant thread.

    Phase 2 of the finding pane starts with one analyst-visible chat rail per
    finding. Conversations are persisted so the pane can reload prior turns and
    future phases can attach consent-gated action bubbles to assistant messages.
    """

    __tablename__ = "conversations"
    __table_args__ = (
        CheckConstraint(
            "(context_type = 'finding' AND finding_id IS NOT NULL) "
            "OR (context_type = 'engagement' AND finding_id IS NULL)",
            name="ck_conversations_context_consistency",
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
        ForeignKey("findings.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    context_type: Mapped[ConversationContextType] = mapped_column(
        Enum(ConversationContextType, name="conversation_context_type"),
        default=ConversationContextType.finding,
        nullable=False,
        index=True,
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)


class ConversationMessage(Base):
    """One chat bubble in a finding conversation.

    ``action_payload`` is intentionally inert in Phase 2. Phase 3 will render it
    as an approve-before-run bubble and route approvals through existing
    suggestion/task/finding APIs.
    """

    __tablename__ = "conversation_messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    action_payload: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    execution_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_executions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
