from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, uuid7


class CommandOutboxStatus(enum.StrEnum):
    pending = "pending"
    published = "published"
    cancelled = "cancelled"
    failed = "failed"


class CommandOutbox(Base, TimestampMixin):
    """A command or domain event committed with the state that caused it.

    Publication is deliberately at-least-once. Commands carry a stable command
    id and outbound domain events carry stable event/feedback ids; consumers
    claim durable processing receipts before non-idempotent effects.
    """

    __tablename__ = "command_outbox"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    idempotency_key: Mapped[str] = mapped_column(
        String(255), nullable=False, unique=True
    )
    engagement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("engagements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), index=True
    )
    thread_id: Mapped[str | None] = mapped_column(String(200), index=True)
    delivery_kind: Mapped[str] = mapped_column(String(30), nullable=False)
    stream_name: Mapped[str] = mapped_column(String(300), nullable=False)
    encoded_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[CommandOutboxStatus] = mapped_column(
        Enum(CommandOutboxStatus, name="command_outbox_status"),
        default=CommandOutboxStatus.pending,
        nullable=False,
        index=True,
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
