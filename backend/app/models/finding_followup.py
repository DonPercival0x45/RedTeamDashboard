from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, uuid7


class FindingRemediationStatus(enum.StrEnum):
    acknowledged = "acknowledged"
    in_progress = "in_progress"
    ready_for_retest = "ready_for_retest"
    client_reports_fixed = "client_reports_fixed"
    accepted_risk = "accepted_risk"


class FindingRetestOutcome(enum.StrEnum):
    fixed = "fixed"
    partially_fixed = "partially_fixed"
    not_fixed = "not_fixed"
    inconclusive = "inconclusive"


class FindingRemediationUpdate(Base, TimestampMixin):
    """Append-only client remediation update for a finding."""

    __tablename__ = "finding_remediation_updates"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    finding_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("findings.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[FindingRemediationStatus] = mapped_column(
        Enum(FindingRemediationStatus, name="finding_remediation_status"),
        nullable=False,
    )
    note: Mapped[str | None] = mapped_column(Text)
    reported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(tz=UTC), nullable=False
    )
    recorded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )


class FindingRetest(Base, TimestampMixin):
    """Append-only analyst retest result; recording one never executes a tool."""

    __tablename__ = "finding_retests"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    finding_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("findings.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    outcome: Mapped[FindingRetestOutcome] = mapped_column(
        Enum(FindingRetestOutcome, name="finding_retest_outcome"), nullable=False
    )
    note: Mapped[str | None] = mapped_column(Text)
    tested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(tz=UTC), nullable=False
    )
    performed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
