"""Methodology service — Track A step A1 (v3).

Handles the catalog side (load seeds, look up by slug), the selection side
(freeze a methodology into the engagement's snapshot), and the two derivation
helpers Track A calls into A2's coverage service with:

* ``derive_expected_triples`` — given an engagement + its scope selection,
  return the ``(node_id, asset_class, scope_key)`` triples A2's
  ``check_baseline_complete`` needs. Reads the frozen snapshot (never the
  live catalog) so in-flight coverage tracking stays coherent.
* ``derive_node_ttls`` — given an engagement, return the ``{node_id: ttl}``
  map A2's ``sweep_stale`` needs. Same rule: snapshot, not live catalog.

Selection is idempotent + reversible: selecting the same methodology twice
returns the existing snapshot; selecting a *different* methodology overwrites
the snapshot (with an ``AuditLog`` row). No coverage records get retroactively
matched or dropped — that's outside A1's job; A2 keeps writing what it writes.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.data.methodology_seeds import SEED_METHODOLOGIES
from app.models import (
    ActorType,
    AuditLog,
    CoverageNodeTier,
    Engagement,
    Methodology,
    MethodologyNode,
    snapshot_payload,
)
from app.services.coverage import ExpectedNode, scope_key

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


def load_seed_catalog(session: Session) -> list[Methodology]:
    """Idempotently upsert the seed methodologies into the catalog.

    Idempotence is per ``(slug, version)`` — running twice does not create
    duplicates. Existing rows with matching (slug, version) are left alone
    (bump the version in the seed to publish a new tree). Node changes on an
    already-installed version are NOT applied here; ship a new version instead.
    """
    installed: list[Methodology] = []
    for seed in SEED_METHODOLOGIES:
        existing = session.execute(
            select(Methodology).where(
                Methodology.slug == seed["slug"],
                Methodology.version == seed["version"],
            )
        ).scalar_one_or_none()
        if existing is not None:
            installed.append(existing)
            continue
        methodology = Methodology(
            slug=seed["slug"],
            version=seed["version"],
            name=seed["name"],
            description=seed.get("description"),
            source_url=seed.get("source_url"),
        )
        session.add(methodology)
        session.flush()
        for node in seed["nodes"]:
            session.add(
                MethodologyNode(
                    methodology_id=methodology.id,
                    node_id=node["node_id"],
                    parent_node_id=node.get("parent_node_id"),
                    title=node["title"],
                    description=node.get("description"),
                    tier=CoverageNodeTier(node["tier"]),
                    asset_class=node["asset_class"],
                    ttl_days=node.get("ttl_days"),
                    sort_order=node.get("sort_order", 0),
                )
            )
        session.flush()
        installed.append(methodology)
        logger.info(
            "methodology.seed_installed",
            slug=methodology.slug,
            version=methodology.version,
            node_count=len(seed["nodes"]),
        )
    return installed


def list_catalog(session: Session) -> list[Methodology]:
    """All catalog entries, newest-version-first within each slug."""
    return list(
        session.execute(
            select(Methodology).order_by(Methodology.slug, Methodology.version.desc())
        ).scalars()
    )


def get_by_slug(
    session: Session, slug: str, version: int | None = None
) -> Methodology | None:
    """Look up a catalog entry. When ``version`` is ``None`` returns the
    highest-numbered version for that slug — the analyst-facing default."""
    stmt = select(Methodology).where(Methodology.slug == slug)
    if version is not None:
        stmt = stmt.where(Methodology.version == version)
    stmt = stmt.order_by(Methodology.version.desc()).limit(1)
    return session.execute(stmt).scalar_one_or_none()


# ---------------------------------------------------------------------------
# Selection (freeze into engagement)
# ---------------------------------------------------------------------------


def select_for_engagement(
    session: Session,
    *,
    engagement_id: uuid.UUID,
    slug: str,
    version: int | None = None,
    actor_type: ActorType = ActorType.user,
    actor_id: str | None = None,
    now: datetime | None = None,
) -> Engagement:
    """Freeze the given methodology into the engagement.

    Idempotent for the (engagement, methodology) pair: selecting the same
    methodology twice is a no-op (no re-snapshot, no timestamp bump). Selecting
    a *different* methodology overwrites the snapshot and stamps a new
    ``methodology_selected_at`` — that's a real event, worth the write, and
    is audit-logged. Existing coverage records are NOT touched; they may or
    may not match the new methodology's node vocabulary.
    """
    eng = session.get(Engagement, engagement_id)
    if eng is None:
        raise ValueError(f"engagement {engagement_id} not found")
    methodology = get_by_slug(session, slug, version)
    if methodology is None:
        raise ValueError(
            f"methodology {slug!r} version {version!r} not found in catalog"
        )
    if (
        eng.methodology_id == methodology.id
        and eng.methodology_snapshot is not None
    ):
        return eng
    ts = now if now is not None else datetime.now(tz=UTC)
    previous_id = eng.methodology_id
    eng.methodology_id = methodology.id
    eng.methodology_snapshot = snapshot_payload(methodology)
    eng.methodology_selected_at = ts
    session.add(
        AuditLog(
            engagement_id=engagement_id,
            actor_type=actor_type,
            actor_id=actor_id,
            event_type="methodology.selected",
            payload={
                "methodology_id": str(methodology.id),
                "slug": methodology.slug,
                "version": methodology.version,
                "previous_methodology_id": (
                    str(previous_id) if previous_id else None
                ),
            },
        )
    )
    session.flush()
    return eng


# ---------------------------------------------------------------------------
# Snapshot readers — the A2 wiring
# ---------------------------------------------------------------------------


def _snapshot_nodes(
    engagement: Engagement,
    *,
    tier: CoverageNodeTier | None = None,
    asset_class: str | None = None,
) -> list[dict[str, Any]]:
    """Read the frozen node list off the engagement, optionally filtered.

    Falls back to an empty list when no methodology has been selected — the
    caller decides whether that's fatal (baseline-complete against nothing
    trivially succeeds; the API-level ``select_for_engagement`` guard is
    where absence gets surfaced to the analyst).
    """
    snapshot = engagement.methodology_snapshot or {}
    nodes = snapshot.get("nodes", [])
    if tier is not None:
        nodes = [n for n in nodes if n.get("tier") == tier.value]
    if asset_class is not None:
        nodes = [n for n in nodes if n.get("asset_class") == asset_class]
    return list(nodes)


def derive_expected_triples(
    engagement: Engagement,
    *,
    scope_item_ids_by_asset_class: dict[str, Iterable[str]],
) -> list[ExpectedNode]:
    """Compute the expected ``(node_id, asset_class, scope_key)`` triples for
    A2's ``check_baseline_complete``.

    Cartesian product of (baseline nodes) × (scope selection for the matching
    asset class). Each asset class in the snapshot pulls its own scope_item_ids
    from the caller-supplied map — A1 doesn't own scope selection (that's the
    engagement's scope tab), so the caller resolves which analyst-declared
    scope items apply to which asset class and hands it in.

    Empty scope selection for a given asset class → no triples for that class
    (which means the engagement never expected coverage there, and
    baseline-complete doesn't wait on it). One shared scope_key per class:
    the whole class's scope items grouped together, matching how the playbook
    runner will invoke against them (A3 lands playbook granularity later).
    """
    triples: list[ExpectedNode] = []
    baseline_nodes = _snapshot_nodes(engagement, tier=CoverageNodeTier.baseline)
    for node in baseline_nodes:
        asset_class = node["asset_class"]
        scope_items = list(scope_item_ids_by_asset_class.get(asset_class, ()))
        if not scope_items:
            continue
        triples.append((node["node_id"], asset_class, scope_key(scope_items)))
    return triples


def derive_node_ttls(engagement: Engagement) -> dict[str, timedelta]:
    """Compute the ``{node_id: ttl}`` map for A2's ``sweep_stale``.

    Nodes with ``ttl_days is None`` in the snapshot are omitted — sweep_stale
    already skips nodes without an entry, so absence = "never lapses" without
    a sentinel. Covers both baseline and exploration nodes; sweep decides
    whether to lapse either.
    """
    ttls: dict[str, timedelta] = {}
    for node in _snapshot_nodes(engagement):
        days = node.get("ttl_days")
        if days is None:
            continue
        ttls[node["node_id"]] = timedelta(days=int(days))
    return ttls
