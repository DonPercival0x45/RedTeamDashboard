"""Coverage records — the shared coverage contract between the collection and
intelligence planes (architecture-v3-tracker PR 0).

Track A (A2) writes these on playbook completion; Track B (B1/B2) reads them
for the strategy projection's coverage rollup and the "baseline complete"
display. This file owns the *schema* only — the writing logic, the
baseline-complete computation, and staleness/TTL decay land in A2.

A ``CoverageRecord`` says: "methodology node ``node_id`` (of tier
``node_tier``) was attempted against ``asset_class`` over ``scope_subset`` by
``playbook_run_id``, reaching ``status``." ``node_id`` and ``asset_class`` are
strings (Track A owns the methodology vocabulary at A1; B references it
read-only), so this table has no FK to a methodology table yet —
``methodology_id`` is a plain UUID refined to a real FK when A1 lands.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, uuid7


class CoverageRecordStatus(enum.StrEnum):
    """Lifecycle of a coverage attempt. ``satisfied`` includes a clean
    "found nothing" result — you looked. (architecture-answers Q3.)"""

    pending = "pending"
    attempted = "attempted"
    satisfied = "satisfied"
    partial = "partial"
    failed = "failed"


class CoverageNodeTier(enum.StrEnum):
    """Which phase a coverage node belongs to. ``baseline`` nodes must all be
    ``satisfied`` for the hard phase gate; ``exploration`` nodes are off the
    beaten path and never block the gate."""

    baseline = "baseline"
    exploration = "exploration"


class CoverageRecord(Base, TimestampMixin):
    """One attempt to satisfy a methodology coverage node over a scope subset."""

    __tablename__ = "coverage_records"
    __table_args__ = (
        Index("ix_coverage_records_eng_node", "engagement_id", "node_id"),
        Index("ix_coverage_records_eng_tier_status", "engagement_id", "node_tier", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    engagement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("engagements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Plain UUID until A1 (methodology catalog) lands; refined to a real FK then.
    methodology_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # Methodology coverage-node id (Track A vocabulary) + the asset class it
    # was run against (domain / ip / cidr / url / …). Strings, not FKs, so this
    # table is decoupled from A1's methodology schema.
    node_id: Mapped[str] = mapped_column(String(200), nullable=False)
    node_tier: Mapped[CoverageNodeTier] = mapped_column(
        Enum(CoverageNodeTier, name="coverage_node_tier"), nullable=False
    )
    asset_class: Mapped[str] = mapped_column(String(80), nullable=False)
    # What this record covers — list of scope_item_ids / entity refs. JSONB so
    # the (node, asset_class, scope) tuple is queryable without a join table.
    scope_subset: Mapped[dict[str, Any] | list[Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    status: Mapped[CoverageRecordStatus] = mapped_column(
        Enum(CoverageRecordStatus, name="coverage_record_status"),
        nullable=False,
        default=CoverageRecordStatus.pending,
    )
    # Plain UUID until A3 (playbook runner) lands; refined to a real FK then.
    playbook_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # When it became ``satisfied``; A2 compares this against the methodology
    # node's TTL to lapse time-sensitive techniques back to ``stale``-eligible.
    satisfied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
