"""Playbook catalog + execution records — Track A step A3a (v3).

A **playbook** is a declarative recipe: an ordered list of tool steps + a
scope-selection rule + the coverage nodes each step satisfies. No LLM decides
which tool runs — the playbook does. The runner (``services/playbook/runner``)
takes a playbook + a scope subset and executes deterministically, writing a
``CoverageRecord`` per step completion + emitting a ``collection.job.completed``
milestone when the run finishes (architecture-v2-plan §2b).

Three tables:

* ``playbooks`` — catalog entry keyed by ``(slug, version)`` like the
  methodology catalog.
* ``playbook_steps`` — ordered steps, each carrying the tool slug it invokes,
  a JSONB args template, and the list of methodology node_ids it satisfies
  on success.
* ``playbook_runs`` — one execution instance: which playbook, which scope
  subset, which engagement, status + counts + timestamps.

Not modeled in A3a: per-step-run rows. The ``CoverageRecord`` A2 already
writes IS the per-step receipt (with tier + status + timestamps + scope);
duplicating that as a ``PlaybookStepRun`` table would be a second source of
truth for the same event. If per-step audit ever needs richer fields than a
CoverageRecord carries, that's a separate model — for A3a the coverage log
suffices.
"""
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


class PlaybookExecutorKind(enum.StrEnum):
    """Which executor implementation drives the run.

    * ``internal`` — in-process ``InternalExecutor`` (A3b tools + stubs).
    * ``mcp`` — out-of-process ``MCPExecutor`` (A4 — hits the MCP server
      for tool execution).

    Chosen at run creation. Per-step dispatch via ``work_items.disposition``
    is a later convergence step.
    """

    internal = "internal"
    mcp = "mcp"


class PlaybookRunStatus(enum.StrEnum):
    """Lifecycle of one playbook execution.

    * ``awaiting_approval`` — created against an ``active=True`` playbook;
      waits for POST ``/playbook-runs/{id}/approve`` before the worker
      will claim it (A5).
    * ``pending`` — created, not started. Ready for the worker to claim.
    * ``running`` — runner is stepping through.
    * ``completed`` — every step reported ok.
    * ``partial`` — at least one step ok, at least one failed. Baseline
      coverage still gets what succeeded; the failures show up as
      ``CoverageRecord.status=failed``.
    * ``failed`` — zero steps ok; a hard fault (executor unavailable,
      scope-selection empty, playbook malformed).
    * ``cancelled`` — analyst aborted before completion (or rejected an
      awaiting_approval run).
    """

    awaiting_approval = "awaiting_approval"
    pending = "pending"
    running = "running"
    completed = "completed"
    partial = "partial"
    failed = "failed"
    cancelled = "cancelled"


class Playbook(Base, TimestampMixin):
    """A catalog entry — an ordered recipe of tool steps.

    ``applies_to_asset_class`` = which entity class the playbook targets
    (``domain`` / ``ip`` / ``url`` / …); the runner uses it to filter which
    scope items in a selection are eligible. ``active`` = whether the whole
    playbook needs analyst pre-authorization (approve-before-run — A5 wires
    the enforcement; A3a stores the flag).
    """

    __tablename__ = "playbooks"
    __table_args__ = (
        UniqueConstraint("slug", "version", name="uq_playbooks_slug_version"),
        Index("ix_playbooks_slug", "slug"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    slug: Mapped[str] = mapped_column(String(120), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    applies_to_asset_class: Mapped[str] = mapped_column(String(80), nullable=False)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    steps: Mapped[list[PlaybookStep]] = relationship(
        "PlaybookStep",
        back_populates="playbook",
        cascade="all, delete-orphan",
        order_by="PlaybookStep.sort_order, PlaybookStep.id",
    )


class PlaybookStep(Base, TimestampMixin):
    """One step of a playbook.

    ``tool_slug`` = the executor-facing identifier the ``PlaybookExecutor``
    knows how to run (matches the existing tool registry's naming).
    ``args_template`` = JSONB dict with placeholders the runner substitutes
    at execution time (e.g. ``{"domain": "{{scope_item}}"}``). Substitution
    rules stay minimal in A3a — string ``{{scope_item}}`` fills; anything
    richer lands with A4's MCP executor or A3b's queue.

    ``satisfies_node_ids`` = list of methodology node_ids this step satisfies
    on successful completion. The runner writes one ``CoverageRecord`` per
    node_id per scope item (architecture-answers Q3: per-attempt).
    """

    __tablename__ = "playbook_steps"
    __table_args__ = (
        Index("ix_playbook_steps_playbook", "playbook_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    playbook_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("playbooks.id", ondelete="CASCADE"),
        nullable=False,
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    tool_slug: Mapped[str] = mapped_column(String(120), nullable=False)
    args_template: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    # JSONB list of methodology node_ids this step satisfies on success.
    satisfies_node_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    description: Mapped[str | None] = mapped_column(Text)

    playbook: Mapped[Playbook] = relationship("Playbook", back_populates="steps")


class PlaybookRun(Base, TimestampMixin):
    """One execution of a playbook against a scope subset.

    ``findings_summary`` fields are counts the runner accumulates as it steps;
    at completion they populate the ``collection.job.completed`` milestone
    payload (matches the ``FindingsSummary`` shape declared in
    ``app.engagement.milestones``). Deterministic — no LLM decides these.

    ``scope_subset`` = the analyst-declared scope_item_ids the run touched;
    same grain A2's ``CoverageRecord.scope_subset`` carries so the coverage
    rollup collapses cleanly.
    """

    __tablename__ = "playbook_runs"
    __table_args__ = (
        Index("ix_playbook_runs_engagement", "engagement_id"),
        Index(
            "ix_playbook_runs_engagement_status", "engagement_id", "status"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    engagement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("engagements.id", ondelete="CASCADE"),
        nullable=False,
    )
    playbook_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("playbooks.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[PlaybookRunStatus] = mapped_column(
        Enum(PlaybookRunStatus, name="playbook_run_status"),
        nullable=False,
        default=PlaybookRunStatus.pending,
        server_default="pending",
    )
    scope_subset: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    steps_total: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    steps_succeeded: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    steps_failed: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    findings_new: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    findings_unvalidated: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    findings_high_severity: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    findings_total: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    last_error: Mapped[str | None] = mapped_column(Text)
    # v3 A5 — approve-before-run attribution. Populated when an analyst
    # releases an ``awaiting_approval`` run into ``pending``. Nullable
    # because pre-A5 and inactive-playbook runs never go through the gate.
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approval_reason: Mapped[str | None] = mapped_column(Text)
    rejected_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
    )
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    # v3 A4: which executor drives this run. Existing rows backfill to
    # ``internal`` (the only option pre-A4).
    executor_kind: Mapped[PlaybookExecutorKind] = mapped_column(
        Enum(PlaybookExecutorKind, name="playbook_executor_kind"),
        nullable=False,
        default=PlaybookExecutorKind.internal,
        server_default="internal",
    )

    playbook: Mapped[Playbook] = relationship("Playbook")
