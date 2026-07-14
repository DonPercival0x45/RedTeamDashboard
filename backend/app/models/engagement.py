from __future__ import annotations

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, uuid7


class EngagementStatus(enum.StrEnum):
    active = "active"
    archived = "archived"
    flushed = "flushed"


class EngagementTimeFrame(enum.StrEnum):
    repeatable = "repeatable"
    point_in_time_continuous = "point_in_time_continuous"
    point_in_time = "point_in_time"
    custom = "custom"


class EngagementWorkState(enum.StrEnum):
    active = "active"
    completion_review = "completion_review"
    completed = "completed"


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
    time_frame: Mapped[EngagementTimeFrame] = mapped_column(
        Enum(EngagementTimeFrame, name="engagement_time_frame"),
        default=EngagementTimeFrame.point_in_time,
        nullable=False,
        server_default="point_in_time",
    )
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    flushed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Engagement Strategist foundation: work completion is independent from
    # archive/flush visibility, with a version for optimistic updates.
    work_state: Mapped[EngagementWorkState] = mapped_column(
        Enum(EngagementWorkState, name="engagement_work_state"),
        default=EngagementWorkState.active,
        nullable=False,
        server_default="active",
        index=True,
    )
    work_state_version: Mapped[int] = mapped_column(
        Integer, default=1, nullable=False, server_default="1"
    )
