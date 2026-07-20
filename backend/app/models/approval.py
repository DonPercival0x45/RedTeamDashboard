from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Enum, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, uuid7


class RiskLevel(enum.StrEnum):
    passive = "passive"
    active = "active"
    destructive = "destructive"


class ApprovalStatus(enum.StrEnum):
    pending = "pending"
    approved = "approved"
    denied = "denied"
    edited = "edited"
    auto = "auto"


class Approval(Base, TimestampMixin):
    __tablename__ = "approvals"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    engagement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("engagements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    thread_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    node: Mapped[str | None] = mapped_column(String(120))
    tool_name: Mapped[str] = mapped_column(String(200), nullable=False)
    tool_call_id: Mapped[str | None] = mapped_column(String(200))
    tool_args: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    risk: Mapped[RiskLevel] = mapped_column(Enum(RiskLevel, name="risk_level"), nullable=False)
    scope_check: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    status: Mapped[ApprovalStatus] = mapped_column(
        Enum(ApprovalStatus, name="approval_status"),
        default=ApprovalStatus.pending,
        nullable=False,
    )
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    decision_args: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # Durable run lineage for resumes. Redis's run:model cache is only an
    # optimization and can expire while a human approval remains pending.
    run_model: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    run_context: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    acting_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    authorization_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
