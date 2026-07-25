"""Durable execution receipts and evidence for playbook step attempts."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, uuid7


class PlaybookStepExecutionStatus(enum.StrEnum):
    """Outcome of one concrete playbook step/target attempt."""

    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    stub = "stub"
    cancelled = "cancelled"


class PlaybookStepExecution(Base, TimestampMixin):
    """One durable receipt for a catalog step executed against one target.

    Catalog steps describe the recipe and coverage records describe methodology
    satisfaction. This row records the actual invocation: target, transport,
    redacted arguments, timing, and outcome. ``attempt`` leaves room for an
    explicit targeted-retry workflow without overwriting history.
    """

    __tablename__ = "playbook_step_executions"
    __table_args__ = (
        UniqueConstraint(
            "playbook_run_id",
            "playbook_step_id",
            "target",
            "attempt",
            name="uq_playbook_step_execution_attempt",
        ),
        Index("ix_playbook_step_executions_run", "playbook_run_id"),
        Index(
            "ix_playbook_step_executions_run_status",
            "playbook_run_id",
            "status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    playbook_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("playbook_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    playbook_step_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("playbook_steps.id", ondelete="SET NULL"),
        nullable=True,
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    tool_slug: Mapped[str] = mapped_column(String(120), nullable=False)
    target: Mapped[str] = mapped_column(Text, nullable=False)
    transport: Mapped[str] = mapped_column(String(20), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    status: Mapped[PlaybookStepExecutionStatus] = mapped_column(
        Enum(
            PlaybookStepExecutionStatus,
            name="playbook_step_execution_status",
        ),
        nullable=False,
        default=PlaybookStepExecutionStatus.running,
        server_default="running",
    )
    # Target-bound arguments only. Secret-like keys are redacted before write.
    arguments: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)

    evidence_artifact: Mapped[EvidenceArtifact | None] = relationship(
        "EvidenceArtifact",
        back_populates="step_execution",
        uselist=False,
        cascade="all, delete-orphan",
    )


class EvidenceArtifact(Base, TimestampMixin):
    """Engagement-owned JSON evidence captured from a tool invocation.

    Evidence is not a Finding. It can support a canonical Finding while also
    preserving successful/clean output that should not clutter the Findings
    workspace. Payloads are redacted and bounded by the persistence service;
    the digest records the complete redacted payload identity when the stored
    representation is truncated.
    """

    __tablename__ = "evidence_artifacts"
    __table_args__ = (
        UniqueConstraint(
            "playbook_step_execution_id",
            name="uq_evidence_artifacts_step_execution",
        ),
        Index("ix_evidence_artifacts_engagement", "engagement_id"),
        Index("ix_evidence_artifacts_run", "playbook_run_id"),
        Index("ix_evidence_artifacts_finding", "finding_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    engagement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("engagements.id", ondelete="CASCADE"),
        nullable=False,
    )
    playbook_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("playbook_runs.id", ondelete="CASCADE"),
        nullable=True,
    )
    playbook_step_execution_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("playbook_step_executions.id", ondelete="CASCADE"),
        nullable=True,
    )
    finding_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("findings.id", ondelete="SET NULL"),
        nullable=True,
    )
    kind: Mapped[str] = mapped_column(
        String(40), nullable=False, default="tool_output", server_default="tool_output"
    )
    source_tool: Mapped[str] = mapped_column(String(120), nullable=False)
    target: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    truncated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    redacted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    step_execution: Mapped[PlaybookStepExecution | None] = relationship(
        "PlaybookStepExecution",
        back_populates="evidence_artifact",
    )
