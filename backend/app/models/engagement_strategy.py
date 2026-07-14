from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, uuid7
from app.models.engagement import EngagementWorkState


class StrategyRevisionState(enum.StrEnum):
    draft = "draft"
    proposed = "proposed"
    current = "current"
    rejected = "rejected"
    superseded = "superseded"


class ObjectiveStatus(enum.StrEnum):
    planned = "planned"
    active = "active"
    blocked = "blocked"
    completed = "completed"
    deferred = "deferred"
    cancelled = "cancelled"


class ObjectivePriority(enum.StrEnum):
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"


class WorkItemStatus(enum.StrEnum):
    ready = "ready"
    in_progress = "in_progress"
    blocked = "blocked"
    completed = "completed"
    deferred = "deferred"
    cancelled = "cancelled"


class WorkItemPriority(enum.StrEnum):
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"


class WorkItemExecutor(enum.StrEnum):
    analyst = "analyst"
    finding_agent = "finding_agent"
    engagement_strategist = "engagement_strategist"
    tactical = "tactical"
    unassigned = "unassigned"


class WorkItemResolution(enum.StrEnum):
    completed = "completed"
    disproved = "disproved"
    not_applicable = "not_applicable"
    duplicate = "duplicate"
    superseded = "superseded"
    unable_to_complete = "unable_to_complete"


class WorkItemFindingRelationship(enum.StrEnum):
    primary = "primary"
    related = "related"
    produced_by = "produced_by"
    blocks = "blocks"


class WorkItemResultState(enum.StrEnum):
    proposed = "proposed"
    accepted = "accepted"
    rejected = "rejected"
    superseded = "superseded"


class StrategySignalStatus(enum.StrEnum):
    open = "open"
    incorporated = "incorporated"
    dismissed = "dismissed"
    superseded = "superseded"


class CoverageCategory(enum.StrEnum):
    scope_review = "scope_review"
    asset_discovery = "asset_discovery"
    service_identification = "service_identification"
    scanner_coverage = "scanner_coverage"
    finding_review = "finding_review"
    evidence_collection = "evidence_collection"
    reporting = "reporting"


class CoverageStatus(enum.StrEnum):
    not_started = "not_started"
    planned = "planned"
    active = "active"
    covered = "covered"
    blocked = "blocked"
    deferred = "deferred"
    accepted_gap = "accepted_gap"
    not_applicable = "not_applicable"


class EngagementCompletionAction(enum.StrEnum):
    review_started = "review_started"
    approved = "approved"
    reopened = "reopened"


class EngagementStrategyRevision(Base, TimestampMixin):
    """One versioned strategy narrative for an engagement."""

    __tablename__ = "engagement_strategy_revisions"
    __table_args__ = (
        UniqueConstraint(
            "engagement_id",
            "version",
            name="uq_strategy_revisions_engagement_version",
        ),
        Index("ix_strategy_revisions_engagement_version", "engagement_id", "version"),
        Index("ix_strategy_revisions_engagement_state", "engagement_id", "state"),
        Index(
            "uq_strategy_revisions_current_per_engagement",
            "engagement_id",
            unique=True,
            postgresql_where=text("state = 'current'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    engagement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("engagements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[StrategyRevisionState] = mapped_column(
        Enum(StrategyRevisionState, name="strategy_revision_state"),
        nullable=False,
        index=True,
    )
    based_on_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("engagement_strategy_revisions.id", ondelete="SET NULL"),
        nullable=True,
    )
    summary: Mapped[str | None] = mapped_column(String(300), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    structured: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    proposed_by_execution_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_executions.id", ondelete="SET NULL")
    )
    proposal_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EngagementObjective(Base, TimestampMixin):
    __tablename__ = "engagement_objectives"
    __table_args__ = (
        Index("ix_engagement_objectives_engagement_status", "engagement_id", "status"),
        Index("ix_engagement_objectives_engagement_order", "engagement_id", "display_order"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    engagement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("engagements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    success_criteria: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[ObjectiveStatus] = mapped_column(
        Enum(ObjectiveStatus, name="objective_status"), nullable=False, index=True
    )
    priority: Mapped[ObjectivePriority] = mapped_column(
        Enum(ObjectivePriority, name="objective_priority"), nullable=False, index=True
    )
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    target_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    completed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class WorkItem(Base, TimestampMixin):
    __tablename__ = "work_items"
    __table_args__ = (
        Index("ix_work_items_engagement_status", "engagement_id", "status"),
        Index("ix_work_items_engagement_priority", "engagement_id", "priority"),
        Index("ix_work_items_engagement_updated", "engagement_id", "updated_at"),
        Index("ix_work_items_objective_id", "objective_id"),
        Index("ix_work_items_assigned_user_id", "assigned_user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    engagement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("engagements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    objective_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("engagement_objectives.id", ondelete="SET NULL"),
        nullable=True,
    )
    parent_work_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("work_items.id", ondelete="SET NULL")
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    acceptance_criteria: Mapped[list[Any]] = mapped_column(JSONB, default=list, nullable=False)
    status: Mapped[WorkItemStatus] = mapped_column(
        Enum(WorkItemStatus, name="work_item_status"), nullable=False, index=True
    )
    priority: Mapped[WorkItemPriority] = mapped_column(
        Enum(WorkItemPriority, name="work_item_priority"), nullable=False, index=True
    )
    executor_type: Mapped[WorkItemExecutor] = mapped_column(
        Enum(WorkItemExecutor, name="work_item_executor"), nullable=False, index=True
    )
    assigned_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_by_execution_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_executions.id", ondelete="SET NULL")
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    blocked_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_outcome: Mapped[WorkItemResolution | None] = mapped_column(
        Enum(WorkItemResolution, name="work_item_resolution"), nullable=True
    )
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class WorkItemFinding(Base):
    __tablename__ = "work_item_findings"
    __table_args__ = (
        Index("ix_work_item_findings_work_item_id", "work_item_id"),
        Index("ix_work_item_findings_finding_id", "finding_id"),
    )

    work_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("work_items.id", ondelete="CASCADE"),
        primary_key=True,
    )
    finding_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("findings.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    relationship: Mapped[WorkItemFindingRelationship] = mapped_column(
        Enum(WorkItemFindingRelationship, name="work_item_finding_relationship"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class WorkItemResult(Base):
    __tablename__ = "work_item_results"
    __table_args__ = (
        UniqueConstraint(
            "work_item_id",
            "revision",
            name="uq_work_item_results_work_item_revision",
        ),
        Index("ix_work_item_results_work_item_state", "work_item_id", "state"),
        Index(
            "uq_work_item_results_accepted_per_work_item",
            "work_item_id",
            unique=True,
            postgresql_where=text("state = 'accepted'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    work_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("work_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[WorkItemResultState] = mapped_column(
        Enum(WorkItemResultState, name="work_item_result_state"),
        nullable=False,
        index=True,
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    structured: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    evidence_refs: Mapped[list[Any]] = mapped_column(JSONB, default=list, nullable=False)
    proposed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    proposed_by_execution_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_executions.id", ondelete="SET NULL")
    )
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class StrategySignal(Base, TimestampMixin):
    __tablename__ = "strategy_signals"
    __table_args__ = (
        CheckConstraint(
            "source_finding_id IS NOT NULL OR source_work_item_id IS NOT NULL "
            "OR source_work_item_result_id IS NOT NULL OR source_execution_id IS NOT NULL",
            name="ck_strategy_signals_has_source",
        ),
        Index("ix_strategy_signals_engagement_status", "engagement_id", "status"),
        Index("ix_strategy_signals_engagement_dedup", "engagement_id", "dedup_key"),
        Index(
            "uq_strategy_signals_active_result_type",
            "source_work_item_result_id",
            "signal_type",
            unique=True,
            postgresql_where=text("source_work_item_result_id IS NOT NULL AND status = 'open'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    engagement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("engagements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_finding_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("findings.id", ondelete="SET NULL")
    )
    source_work_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("work_items.id", ondelete="SET NULL")
    )
    source_work_item_result_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("work_item_results.id", ondelete="SET NULL")
    )
    source_execution_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_executions.id", ondelete="SET NULL")
    )
    signal_type: Mapped[str] = mapped_column(String(80), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[str] = mapped_column(String(20), nullable=False)
    evidence_refs: Mapped[list[Any]] = mapped_column(JSONB, default=list, nullable=False)
    suggested_effect: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    dedup_key: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[StrategySignalStatus] = mapped_column(
        Enum(StrategySignalStatus, name="strategy_signal_status"),
        nullable=False,
        index=True,
    )
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CoverageItem(Base, TimestampMixin):
    __tablename__ = "coverage_items"
    __table_args__ = (
        Index("ix_coverage_items_engagement_status", "engagement_id", "status"),
        Index("ix_coverage_items_engagement_category", "engagement_id", "activity_category"),
        Index("ix_coverage_items_engagement_target", "engagement_id", "target_key"),
        Index(
            "uq_coverage_items_engagement_target_category",
            "engagement_id",
            "target_kind",
            "target_key",
            "activity_category",
            unique=True,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    engagement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("engagements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    objective_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("engagement_objectives.id", ondelete="SET NULL")
    )
    scope_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scope_items.id", ondelete="SET NULL")
    )
    target_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    target_key: Mapped[str] = mapped_column(String(500), nullable=False)
    activity_category: Mapped[CoverageCategory] = mapped_column(
        Enum(CoverageCategory, name="coverage_category"), nullable=False, index=True
    )
    status: Mapped[CoverageStatus] = mapped_column(
        Enum(CoverageStatus, name="coverage_status"), nullable=False, index=True
    )
    supporting_refs: Mapped[list[Any]] = mapped_column(JSONB, default=list, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    accepted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class EngagementCheckpoint(Base):
    __tablename__ = "engagement_checkpoints"
    __table_args__ = (
        Index("ix_engagement_checkpoints_engagement_created", "engagement_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    engagement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("engagements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    strategy_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("engagement_strategy_revisions.id", ondelete="SET NULL"),
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_by_execution_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_executions.id", ondelete="SET NULL")
    )
    material_event_cursor: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    facts: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    narrative: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class EngagementCompletionDecision(Base):
    __tablename__ = "engagement_completion_decisions"
    __table_args__ = (
        UniqueConstraint(
            "engagement_id",
            "idempotency_key",
            name="uq_completion_decisions_engagement_idempotency",
        ),
        CheckConstraint(
            "(action IN ('review_started', 'approved') AND readiness_hash IS NOT NULL "
            "AND readiness_snapshot IS NOT NULL AND prior_completion_decision_id IS NULL) "
            "OR (action = 'reopened' AND readiness_hash IS NULL "
            "AND readiness_snapshot IS NULL AND prior_completion_decision_id IS NOT NULL "
            "AND reason IS NOT NULL AND length(btrim(reason)) > 0)",
            name="ck_completion_decisions_action_fields",
        ),
        Index(
            "ix_completion_decisions_engagement_created",
            "engagement_id",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    engagement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("engagements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    action: Mapped[EngagementCompletionAction] = mapped_column(
        Enum(EngagementCompletionAction, name="engagement_completion_action"),
        nullable=False,
        index=True,
    )
    from_work_state: Mapped[EngagementWorkState] = mapped_column(
        Enum(EngagementWorkState, name="engagement_work_state"), nullable=False
    )
    to_work_state: Mapped[EngagementWorkState] = mapped_column(
        Enum(EngagementWorkState, name="engagement_work_state"), nullable=False
    )
    readiness_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    readiness_snapshot: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    accepted_exceptions: Mapped[list[Any]] = mapped_column(JSONB, default=list, nullable=False)
    strategy_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("engagement_strategy_revisions.id", ondelete="SET NULL"),
    )
    prior_completion_decision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("engagement_completion_decisions.id", ondelete="SET NULL"),
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(100), nullable=False)
    decided_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
