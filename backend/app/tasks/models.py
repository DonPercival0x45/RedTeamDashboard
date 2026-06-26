from __future__ import annotations

import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, uuid7


class TaskKind(enum.StrEnum):
    """What kind of work the task is. Agents may run scan/enum only;
    exploit is analyst-owned (CHARTER invariant — enforced in
    ``TacticalAgent.dispatch``)."""

    scan = "scan"
    enum = "enum"
    exploit = "exploit"


class OwnerEligibility(enum.StrEnum):
    agent = "agent"
    analyst = "analyst"
    either = "either"


class TaskStatus(enum.StrEnum):
    pending = "pending"
    dispatched = "dispatched"
    running = "running"
    completed = "completed"
    failed = "failed"
    deferred = "deferred"
    cancelled = "cancelled"


class Task(Base, TimestampMixin):
    """A unit of orchestrator-emitted work tied to an Project.

    Tasks may originate from an accepted ``Suggestion`` (Strategic) or be
    minted directly by analyst action. ``payload`` carries the tool name +
    args Tactical needs to launch a worker run.
    """

    __tablename__ = "tasks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    finding_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("findings.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    kind: Mapped[TaskKind] = mapped_column(
        Enum(TaskKind, name="task_kind"), nullable=False
    )
    owner_eligibility: Mapped[OwnerEligibility] = mapped_column(
        Enum(OwnerEligibility, name="task_owner_eligibility"), nullable=False
    )
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus, name="task_status"),
        default=TaskStatus.pending,
        nullable=False,
        index=True,
    )
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, nullable=False
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SuggestionKind(enum.StrEnum):
    """``task`` → analyst accepts to mint a Task. ``ephemeral`` → recommends
    spinning up a scan_box / attack_box. ``note`` → freeform observation
    Strategic wants surfaced."""

    task = "task"
    ephemeral = "ephemeral"
    note = "note"


class SuggestionStatus(enum.StrEnum):
    open = "open"
    accepted = "accepted"
    dismissed = "dismissed"


class AgentName(enum.StrEnum):
    """The orchestrator agent that produced the row. Mirrored on
    ``AgentExecution`` so a Suggestion can be traced back to the run."""

    strategic = "strategic"
    tactical = "tactical"


class Suggestion(Base, TimestampMixin):
    """A recommendation surfaced by Strategic (or Tactical) for analyst review.

    Pure-watcher invariant: nothing happens until the analyst accepts. On
    accept, a kind=``task`` suggestion becomes a ``Task`` (``task_id`` back-
    reference is set).
    """

    __tablename__ = "suggestions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    finding_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("findings.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    kind: Mapped[SuggestionKind] = mapped_column(
        Enum(SuggestionKind, name="suggestion_kind"), nullable=False
    )
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, nullable=False
    )
    status: Mapped[SuggestionStatus] = mapped_column(
        Enum(SuggestionStatus, name="suggestion_status"),
        default=SuggestionStatus.open,
        nullable=False,
        index=True,
    )
    created_by_agent: Mapped[AgentName] = mapped_column(
        Enum(AgentName, name="agent_name"), nullable=False
    )
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="SET NULL")
    )


class AgentTrigger(enum.StrEnum):
    """Why the orchestrator agent fired. ``finding`` = a new finding
    landed; ``task`` = a task completed and Strategic wants to re-plan;
    ``manual`` = analyst clicked the slide-over button; ``tick`` = periodic
    watcher cadence (Phase 10)."""

    finding = "finding"
    task = "task"
    manual = "manual"
    tick = "tick"


class AgentExecutionStatus(enum.StrEnum):
    running = "running"
    completed = "completed"
    failed = "failed"


class AgentExecution(Base):
    """One Strategic or Tactical LLM call. Used for trace + Costs tab roll-up."""

    __tablename__ = "agent_executions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    agent: Mapped[AgentName] = mapped_column(
        Enum(AgentName, name="agent_name"), nullable=False, index=True
    )
    trigger: Mapped[AgentTrigger] = mapped_column(
        Enum(AgentTrigger, name="agent_trigger"), nullable=False
    )
    input: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, nullable=False
    )
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    model_provider: Mapped[str | None] = mapped_column(String(40), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    tokens_in: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_out: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 6), nullable=True)
    status: Mapped[AgentExecutionStatus] = mapped_column(
        Enum(AgentExecutionStatus, name="agent_execution_status"),
        default=AgentExecutionStatus.running,
        nullable=False,
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
