from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
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


class EngagementArchitecture(enum.StrEnum):
    legacy = "legacy"
    v3 = "v3"


class EngagementPhase(enum.StrEnum):
    """v3 lifecycle mode (architecture-v3-tracker PR 0). Orthogonal to
    ``EngagementStatus`` (alive?) and ``EngagementWorkState`` (how-close-to-done?):
    ``phase`` is which-mode-of-work — deterministic baseline coverage, then
    AI-guided exploration. Baseline-complete flips ``phase`` only, never
    ``work_state``."""

    baseline = "baseline"
    exploration = "exploration"


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
    # Token-saving kill-switch for automatic background strategic generation
    # (the finding watcher + auto-reassess on work-item resolve). Default on;
    # analysts flip it off while evaluating so no LLM tokens are spent on
    # auto-generated suggestions. The manual Analyze button is unaffected.
    auto_assess_enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False, server_default="true"
    )
    # Track B rollout axis. Existing engagements stay legacy; explicit creation
    # or one-way conversion opts an engagement into the v3 intelligence plane.
    # The process setting remains an emergency kill switch for automatic runs.
    intelligence_architecture: Mapped[EngagementArchitecture] = mapped_column(
        Enum(
            EngagementArchitecture,
            name="engagement_intelligence_architecture",
        ),
        default=EngagementArchitecture.legacy,
        nullable=False,
        server_default="legacy",
        index=True,
    )
    converted_to_v3_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    # v3 shared contract (PR 0): which mode of work the engagement is in.
    # Orthogonal to status/work_state; baseline-complete flips this to
    # ``exploration`` and stamps ``baseline_completed_at``. The detour override
    # (architecture-answers §C.1) lets an analyst chase a hot lead mid-baseline
    # without satisfying baseline items.
    phase: Mapped[EngagementPhase] = mapped_column(
        Enum(EngagementPhase, name="engagement_phase"),
        default=EngagementPhase.baseline,
        nullable=False,
        server_default="baseline",
        index=True,
    )
    baseline_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    # v3 A1 — the methodology this engagement selected. FK is nullable for
    # legacy engagements that pre-date A1 + for engagements still on the
    # v1/pre-methodology setup wizard. Once selected, the tree is frozen into
    # ``methodology_snapshot`` — later catalog edits can't shift coverage
    # under an in-flight engagement (architecture-v2-plan §2a).
    methodology_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("methodologies.id", ondelete="SET NULL"),
        index=True,
    )
    methodology_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    methodology_selected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
