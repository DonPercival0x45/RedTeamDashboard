"""Engagement Memory — the shared structured brain (architecture v3, step 1).

One row per memory *element* (fact / hypothesis / open_question / thread /
decision), not a JSONB document, so every operation the design needs is a
per-element one: attribution, hot/cold tiering, compaction as auditable state
transitions, concurrent analyst+agent writes, and token-budget accounting.

See ``docs`` / ``architecture-memory-schema.md`` for the full rationale. The
agent reads the *hot* set into each invocation; *cold* elements are fetched on
reference; *archived* elements are excluded but never deleted, so a bad
compaction pass is always reversible via ``services.memory.restore``.

Coverage is NOT stored here — it is owned by ``CoverageRecord`` (playbook
runner). Raw entities/findings are NOT copied here — they are referenced via
``MemoryLink``. The strategy screen renders a *live projection* of the hot set,
never a second copy.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, uuid7
from app.models.audit_log import ActorType


class MemoryKind(enum.StrEnum):
    """The five element types. Shared columns live on the row; kind-specific
    fields live in ``body`` (validated per-kind in the service layer)."""

    fact = "fact"
    hypothesis = "hypothesis"
    open_question = "open_question"
    thread = "thread"
    decision = "decision"


class MemoryTier(enum.StrEnum):
    """Load state. ``hot`` is serialized into every agent invocation; ``cold``
    is excluded from the default prompt but fetched when referenced; ``archived``
    is excluded entirely but retained (never hard-deleted) so compaction is
    reversible."""

    hot = "hot"
    cold = "cold"
    archived = "archived"


class MemoryStatus(enum.StrEnum):
    """Lifecycle for hypotheses/questions; facts/threads/decisions stay ``open``
    until superseded."""

    open = "open"
    resolved = "resolved"
    dismissed = "dismissed"
    superseded = "superseded"


class MemoryLinkRelation(enum.StrEnum):
    supports = "supports"        # hypothesis -> fact/finding
    refutes = "refutes"          # hypothesis -> fact/finding
    evidence = "evidence"        # fact/thread -> finding/entity
    folds_into = "folds_into"    # hypothesis/question -> decision (compaction)
    supersedes = "supersedes"    # element -> element (compaction lineage)


class MemoryLinkTargetType(enum.StrEnum):
    memory_element = "memory_element"
    finding = "finding"
    entity = "entity"


class MemoryElement(Base, TimestampMixin):
    """A single fact, hypothesis, open question, thread, or decision."""

    __tablename__ = "memory_elements"
    __table_args__ = (
        Index("ix_memory_elements_eng_tier", "engagement_id", "tier"),
        Index("ix_memory_elements_eng_kind", "engagement_id", "kind"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    engagement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("engagements.id", ondelete="CASCADE"),
        nullable=False,
    )

    kind: Mapped[MemoryKind] = mapped_column(
        Enum(MemoryKind, name="memory_kind"), nullable=False, index=True
    )
    tier: Mapped[MemoryTier] = mapped_column(
        Enum(MemoryTier, name="memory_tier"),
        nullable=False,
        default=MemoryTier.hot,
        index=True,
    )
    status: Mapped[MemoryStatus] = mapped_column(
        Enum(MemoryStatus, name="memory_status"),
        nullable=False,
        default=MemoryStatus.open,
    )

    # The always-loaded one-liner (claim / statement / question / topic /
    # decision). ``body`` carries the kind-specific extras, pulled only when
    # the agent drills in — keeps the hot serialization compact.
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    confidence: Mapped[float | None] = mapped_column(Float)  # 0..1 (facts/hypotheses)
    # Written on every mutation so the budget check is SUM(token_estimate)
    # WHERE tier='hot' rather than a re-tokenize. Character-based estimate
    # (len//4); swap for a real tokenizer if the ceiling ever gets tight.
    token_estimate: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Optimistic-lock counter for concurrent same-element edits. ``edit_element``
    # takes ``expected_version`` and guards the UPDATE with WHERE version=?; a
    # mismatch means another writer got there first. Internal tier transitions
    # (compaction) run under the per-engagement lock and don't use it.
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # Attribution — analyst OR agent, reusing the shared ActorType vocabulary.
    author_type: Mapped[ActorType] = mapped_column(
        Enum(ActorType, name="actor_type", create_type=False), nullable=False
    )
    author_id: Mapped[str] = mapped_column(Text, nullable=False)

    # Compaction never deletes; it supersedes. This FK is the lineage that
    # makes ``restore`` possible.
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("memory_elements.id", ondelete="SET NULL")
    )
    last_referenced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )


class MemoryLink(Base):
    """Typed edge: element -> element, or element -> finding/entity evidence.

    Polymorphic target (like ``entity_finding_link``); the target FK is
    enforced in the service layer, not the DB, because it spans tables."""

    __tablename__ = "memory_links"
    __table_args__ = (
        Index("ix_memory_links_from", "from_element_id"),
        Index("ix_memory_links_target", "target_type", "target_id"),
        Index("ix_memory_links_engagement", "engagement_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    # Denormalized from ``from_element.engagement_id`` — a DB-level guard
    # against cross-engagement links and a scoping key for per-engagement
    # queries (matters at 100k entities). The service sets it from the source
    # element; it is never edited independently.
    engagement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("engagements.id", ondelete="CASCADE"),
        nullable=False,
    )
    from_element_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("memory_elements.id", ondelete="CASCADE"),
        nullable=False,
    )
    relation: Mapped[MemoryLinkRelation] = mapped_column(
        Enum(MemoryLinkRelation, name="memory_link_relation"), nullable=False
    )
    target_type: Mapped[MemoryLinkTargetType] = mapped_column(
        Enum(MemoryLinkTargetType, name="memory_link_target_type"), nullable=False
    )
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
