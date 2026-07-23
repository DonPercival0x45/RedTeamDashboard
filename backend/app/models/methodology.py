"""Methodology catalog + coverage-tree nodes — Track A step A1 (v3).

A methodology is a *coverage tree* of techniques, tiered into baseline (the
deterministic must-run part) and exploration (off-the-beaten-path). Analysts
select one per engagement at creation. Selection **snapshots** the tree into
``engagements.methodology_snapshot`` so later catalog edits can't break
in-flight coverage tracking (architecture-v2-plan §2a).

Two tables, no self-referential FK on nodes:

* ``methodologies`` — catalog entry: slug + version + display metadata.
* ``methodology_nodes`` — the tree, expressed as (node_id, parent_node_id)
  string pairs. Not a self-referential FK because ``node_id`` is a stable
  string identifier that A2's ``CoverageRecord.node_id`` already stores as
  text (not a FK) — the tree is read into memory once at snapshot time; the
  DB just needs to fetch a methodology's nodes.

Tier + TTL live on the node — A2's ``sweep_stale`` reads ``ttl_days`` per
node to lapse satisfied → stale; ``check_baseline_complete`` reads
``tier=baseline`` nodes as the "expected" set.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Enum, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, uuid7
from app.models.coverage import CoverageNodeTier


class Methodology(Base, TimestampMixin):
    """A catalog entry — one selectable methodology (PTES, MITRE ATT&CK, etc).

    ``slug`` + ``version`` is the natural key; ``(slug, version)`` is unique so
    the catalog can carry side-by-side revisions of the same methodology and
    engagements can pin to a specific one. The catalog is *append-mostly* —
    edits typically mean a version bump, not an in-place mutation, so
    snapshotted engagements stay coherent even without the freeze.
    """

    __tablename__ = "methodologies"
    __table_args__ = (
        UniqueConstraint("slug", "version", name="uq_methodologies_slug_version"),
        Index("ix_methodologies_slug", "slug"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    # Where this catalog entry came from — the public spec URL, an internal
    # doc, or NULL for custom analyst-authored methodologies.
    source_url: Mapped[str | None] = mapped_column(String(500))

    nodes: Mapped[list[MethodologyNode]] = relationship(
        "MethodologyNode",
        back_populates="methodology",
        cascade="all, delete-orphan",
        order_by="MethodologyNode.sort_order, MethodologyNode.node_id",
    )


class MethodologyNode(Base, TimestampMixin):
    """One technique / coverage node in a methodology's tree.

    Nodes are identified by a stable **string** ``node_id`` (e.g.
    ``recon.passive.subdomains``) — the same identifier A2's
    ``CoverageRecord.node_id`` stores. The (methodology_id, node_id) pair is
    unique so two nodes in the same methodology can't collide.

    ``parent_node_id`` is a plain string reference to another node's
    ``node_id`` within the same methodology — no self-FK, because trees are
    read as one query and assembled in memory. The DB doesn't enforce tree
    integrity; the seed loader + editor do.

    ``asset_class`` = which entity class this node is expected to run against
    (``domain`` / ``ip`` / ``url`` / ``organization`` / …). A2's
    ``check_baseline_complete`` crosses baseline nodes with in-scope assets of
    the matching class to derive the expected coverage triples.

    ``ttl_days`` = how long a ``satisfied`` record stays fresh before the
    stale sweep re-qualifies it. NULL = never lapses (technique isn't
    time-sensitive; e.g. static domain metadata).
    """

    __tablename__ = "methodology_nodes"
    __table_args__ = (
        UniqueConstraint("methodology_id", "node_id", name="uq_methodology_nodes_ident"),
        Index("ix_methodology_nodes_methodology", "methodology_id"),
        Index(
            "ix_methodology_nodes_tier_asset",
            "methodology_id",
            "tier",
            "asset_class",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    methodology_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("methodologies.id", ondelete="CASCADE"),
        nullable=False,
    )
    node_id: Mapped[str] = mapped_column(String(200), nullable=False)
    parent_node_id: Mapped[str | None] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    tier: Mapped[CoverageNodeTier] = mapped_column(
        Enum(CoverageNodeTier, name="coverage_node_tier", create_type=False),
        nullable=False,
    )
    asset_class: Mapped[str] = mapped_column(String(80), nullable=False)
    # NULL = never lapses. A2's sweep_stale skips nodes without a TTL.
    ttl_days: Mapped[int | None] = mapped_column(Integer)
    # Display ordering within a methodology. Ties broken by ``node_id`` for
    # deterministic output.
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    methodology: Mapped[Methodology] = relationship("Methodology", back_populates="nodes")


def snapshot_payload(methodology: Methodology) -> dict[str, Any]:
    """Freeze a methodology + its nodes into a plain-dict payload suitable for
    ``engagements.methodology_snapshot`` (JSONB).

    Owned here (not in the service) so the snapshot format is co-located with
    the model. Consumers (``derive_expected_triples`` / ``derive_node_ttls`` /
    the frontend detail endpoint) read the same shape.
    """
    return {
        "id": str(methodology.id),
        "slug": methodology.slug,
        "version": methodology.version,
        "name": methodology.name,
        "description": methodology.description,
        "source_url": methodology.source_url,
        "nodes": [
            {
                "node_id": n.node_id,
                "parent_node_id": n.parent_node_id,
                "title": n.title,
                "description": n.description,
                "tier": n.tier.value,
                "asset_class": n.asset_class,
                "ttl_days": n.ttl_days,
                "sort_order": n.sort_order,
            }
            for n in methodology.nodes
        ],
    }
