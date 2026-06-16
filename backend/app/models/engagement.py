from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, uuid7


class EngagementStatus(enum.StrEnum):
    active = "active"
    archived = "archived"
    flushed = "flushed"


class Engagement(Base, TimestampMixin):
    __tablename__ = "engagements"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(200), unique=True, nullable=False, index=True)
    # Free-text engagement details set on the setup page (rules of engagement,
    # objectives, notes). Optional.
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[EngagementStatus] = mapped_column(
        Enum(EngagementStatus, name="engagement_status"),
        default=EngagementStatus.active,
        nullable=False,
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    flushed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
