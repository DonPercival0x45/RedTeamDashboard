"""Methodology service tests — Track A step A1.

Covers the seed loader + selection + the two derivation helpers Track A hands
to A2:

- ``load_seed_catalog`` is idempotent (same seed, run twice, exactly one row per
  (slug, version)).
- ``select_for_engagement`` snapshots the tree onto the engagement, flips
  ``methodology_id`` + timestamp, and is idempotent on repeat calls with the
  same methodology.
- Selecting a *different* methodology overwrites the snapshot.
- The snapshot is **frozen**: mutating the live catalog after selection does
  NOT change the engagement's copy.
- ``derive_expected_triples`` computes A2's ``(node_id, asset_class,
  scope_key)`` list from snapshot + scope selection.
- ``derive_node_ttls`` yields the ``{node_id: timedelta}`` map A2's
  ``sweep_stale`` consumes; nodes without ``ttl_days`` are omitted.

Isolation: flush, not commit; the ``db`` fixture rolls back per test.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    ActorType,
    AuditLog,
    CoverageNodeTier,
    Engagement,
    EngagementStatus,
    EngagementWorkState,
    Methodology,
    MethodologyNode,
)
from app.services import coverage as cov
from app.services import methodology as meth


@pytest.fixture()
def engagement(db: Session) -> Engagement:
    eng = Engagement(
        name="Methodology Test",
        slug=f"meth-{uuid.uuid4().hex[:8]}",
        status=EngagementStatus.active,
        work_state=EngagementWorkState.active,
    )
    db.add(eng)
    db.flush()
    return eng


@pytest.fixture()
def catalog(db: Session) -> list[Methodology]:
    return meth.load_seed_catalog(db)


# ---------------------------------------------------------------------------
# Seed loader
# ---------------------------------------------------------------------------


def test_seed_catalog_installs_all_three_methodologies(
    db: Session, catalog: list[Methodology]
) -> None:
    slugs = {m.slug for m in catalog}
    assert slugs == {"ptes", "mitre-attack", "osint-minimal"}


def test_seed_catalog_is_idempotent(db: Session, catalog: list[Methodology]) -> None:
    """A second load must not duplicate rows — bumped versions go in as new
    rows, but same-version reloads are no-ops."""
    before = set(
        db.execute(
            select(Methodology.slug, Methodology.version)
        ).all()
    )
    meth.load_seed_catalog(db)
    after = set(
        db.execute(
            select(Methodology.slug, Methodology.version)
        ).all()
    )
    assert before == after


def test_seed_nodes_carry_tier_and_ttl(
    db: Session, catalog: list[Methodology]
) -> None:
    """Every node the loader wrote has a tier and either a positive ttl_days
    or None. Catches typos in the seed dicts."""
    for methodology in catalog:
        assert methodology.nodes, f"{methodology.slug} has no nodes"
        for node in methodology.nodes:
            assert node.tier in (
                CoverageNodeTier.baseline,
                CoverageNodeTier.exploration,
            )
            assert node.ttl_days is None or node.ttl_days > 0


# ---------------------------------------------------------------------------
# get_by_slug
# ---------------------------------------------------------------------------


def test_get_by_slug_returns_latest_by_default(
    db: Session, catalog: list[Methodology]
) -> None:
    picked = meth.get_by_slug(db, "ptes")
    assert picked is not None
    assert picked.slug == "ptes"
    # Only v1 seeded, so latest == 1.
    assert picked.version == 1


def test_get_by_slug_specific_version(
    db: Session, catalog: list[Methodology]
) -> None:
    picked = meth.get_by_slug(db, "ptes", version=1)
    assert picked is not None
    assert picked.version == 1
    absent = meth.get_by_slug(db, "ptes", version=99)
    assert absent is None


def test_get_by_slug_unknown_returns_none(
    db: Session, catalog: list[Methodology]
) -> None:
    assert meth.get_by_slug(db, "does-not-exist") is None


# ---------------------------------------------------------------------------
# select_for_engagement — snapshot mechanics
# ---------------------------------------------------------------------------


def test_select_snapshots_tree_onto_engagement(
    db: Session, engagement: Engagement, catalog: list[Methodology]
) -> None:
    eng = meth.select_for_engagement(
        db,
        engagement_id=engagement.id,
        slug="ptes",
        actor_id="tester",
        now=datetime(2026, 7, 23, tzinfo=UTC),
    )
    db.refresh(eng)
    assert eng.methodology_id is not None
    assert eng.methodology_snapshot is not None
    assert eng.methodology_snapshot["slug"] == "ptes"
    assert eng.methodology_selected_at == datetime(2026, 7, 23, tzinfo=UTC)
    # Snapshot carries the node list — the freeze the design promised.
    node_ids = {n["node_id"] for n in eng.methodology_snapshot["nodes"]}
    assert "recon.passive.whois" in node_ids


def test_select_is_idempotent_for_same_methodology(
    db: Session, engagement: Engagement, catalog: list[Methodology]
) -> None:
    first_ts = datetime(2026, 7, 23, tzinfo=UTC)
    meth.select_for_engagement(
        db, engagement_id=engagement.id, slug="ptes",
        actor_id="t1", now=first_ts,
    )
    # Second call, later timestamp — must NOT overwrite selected_at or
    # emit a second audit row.
    meth.select_for_engagement(
        db, engagement_id=engagement.id, slug="ptes",
        actor_id="t1", now=first_ts + timedelta(days=1),
    )
    db.refresh(engagement)
    assert engagement.methodology_selected_at == first_ts
    audit_rows = db.execute(
        select(AuditLog).where(
            AuditLog.engagement_id == engagement.id,
            AuditLog.event_type == "methodology.selected",
        )
    ).scalars().all()
    assert len(audit_rows) == 1


def test_select_different_methodology_overwrites_snapshot(
    db: Session, engagement: Engagement, catalog: list[Methodology]
) -> None:
    meth.select_for_engagement(
        db, engagement_id=engagement.id, slug="ptes", actor_id="t",
        now=datetime(2026, 7, 23, tzinfo=UTC),
    )
    meth.select_for_engagement(
        db, engagement_id=engagement.id, slug="osint-minimal", actor_id="t",
        now=datetime(2026, 7, 24, tzinfo=UTC),
    )
    db.refresh(engagement)
    assert engagement.methodology_snapshot["slug"] == "osint-minimal"
    assert engagement.methodology_selected_at == datetime(2026, 7, 24, tzinfo=UTC)


def test_select_snapshot_is_frozen_against_catalog_edits(
    db: Session, engagement: Engagement, catalog: list[Methodology]
) -> None:
    """The whole point of snapshotting — mutating the catalog after selection
    must NOT touch the engagement's copy (architecture-v2-plan §2a)."""
    meth.select_for_engagement(
        db, engagement_id=engagement.id, slug="ptes", actor_id="t",
        now=datetime(2026, 7, 23, tzinfo=UTC),
    )
    db.refresh(engagement)
    original_node_count = len(engagement.methodology_snapshot["nodes"])

    # Nuke a node in the live catalog.
    ptes = meth.get_by_slug(db, "ptes")
    assert ptes is not None
    live_node = db.execute(
        select(MethodologyNode).where(
            MethodologyNode.methodology_id == ptes.id,
            MethodologyNode.node_id == "recon.passive.whois",
        )
    ).scalar_one()
    db.delete(live_node)
    db.flush()

    db.refresh(engagement)
    assert (
        len(engagement.methodology_snapshot["nodes"]) == original_node_count
    )
    snapshot_ids = {
        n["node_id"] for n in engagement.methodology_snapshot["nodes"]
    }
    assert "recon.passive.whois" in snapshot_ids


def test_select_missing_engagement_raises(
    db: Session, catalog: list[Methodology]
) -> None:
    with pytest.raises(ValueError):
        meth.select_for_engagement(
            db, engagement_id=uuid.uuid4(), slug="ptes",
            now=datetime(2026, 7, 23, tzinfo=UTC),
        )


def test_select_missing_methodology_raises(
    db: Session, engagement: Engagement, catalog: list[Methodology]
) -> None:
    with pytest.raises(ValueError):
        meth.select_for_engagement(
            db, engagement_id=engagement.id, slug="not-real",
            now=datetime(2026, 7, 23, tzinfo=UTC),
        )


def test_select_writes_attributed_audit_log(
    db: Session, engagement: Engagement, catalog: list[Methodology]
) -> None:
    meth.select_for_engagement(
        db, engagement_id=engagement.id, slug="ptes",
        actor_type=ActorType.user, actor_id="alice",
        now=datetime(2026, 7, 23, tzinfo=UTC),
    )
    log = db.execute(
        select(AuditLog).where(
            AuditLog.engagement_id == engagement.id,
            AuditLog.event_type == "methodology.selected",
        )
    ).scalar_one()
    assert log.actor_type is ActorType.user
    assert log.actor_id == "alice"
    assert log.payload["slug"] == "ptes"


# ---------------------------------------------------------------------------
# derive_expected_triples — the A2 wiring
# ---------------------------------------------------------------------------


def test_derive_expected_triples_products_over_scope(
    db: Session, engagement: Engagement, catalog: list[Methodology]
) -> None:
    meth.select_for_engagement(
        db, engagement_id=engagement.id, slug="ptes",
        now=datetime(2026, 7, 23, tzinfo=UTC),
    )
    db.refresh(engagement)
    triples = meth.derive_expected_triples(
        engagement,
        scope_item_ids_by_asset_class={
            "domain": ["scope-1", "scope-2"],
            "ip": ["scope-3"],
        },
    )
    # One triple per (baseline node, individual scope item) — the per-item
    # grain matches how the playbook runner records coverage per scope item.
    domain_triples = [t for t in triples if t[1] == "domain"]
    ip_triples = [t for t in triples if t[1] == "ip"]
    # PTES has 4 baseline domain nodes × 2 domain scope items = 8 triples.
    assert len(domain_triples) == 8
    # 3 baseline ip nodes × 1 ip scope item = 3 triples.
    assert len(ip_triples) == 3
    domain_keys = sorted({sk for _n, _a, sk in domain_triples})
    assert domain_keys == [cov.scope_key(["scope-1"]), cov.scope_key(["scope-2"])]
    for _n, _a, sk in ip_triples:
        assert sk == cov.scope_key(["scope-3"])


def test_derive_expected_triples_skips_empty_scope(
    db: Session, engagement: Engagement, catalog: list[Methodology]
) -> None:
    """No scope items for an asset class → no expected triples for it. That's
    a real engagement shape (e.g. domain-only OSINT with no IPs)."""
    meth.select_for_engagement(
        db, engagement_id=engagement.id, slug="ptes",
        now=datetime(2026, 7, 23, tzinfo=UTC),
    )
    db.refresh(engagement)
    triples = meth.derive_expected_triples(
        engagement,
        scope_item_ids_by_asset_class={"domain": ["scope-1"]},
    )
    assert all(t[1] == "domain" for t in triples)


def test_derive_expected_triples_empty_snapshot(
    db: Session, engagement: Engagement
) -> None:
    """No methodology selected → no expected triples. Not an error; just no
    baseline to complete yet."""
    triples = meth.derive_expected_triples(
        engagement,
        scope_item_ids_by_asset_class={"domain": ["scope-1"]},
    )
    assert triples == []


# ---------------------------------------------------------------------------
# derive_node_ttls
# ---------------------------------------------------------------------------


def test_derive_node_ttls_maps_days_to_timedelta(
    db: Session, engagement: Engagement, catalog: list[Methodology]
) -> None:
    meth.select_for_engagement(
        db, engagement_id=engagement.id, slug="ptes",
        now=datetime(2026, 7, 23, tzinfo=UTC),
    )
    db.refresh(engagement)
    ttls = meth.derive_node_ttls(engagement)
    # Passive subdomain enum in the seed has ttl_days=14.
    assert ttls["recon.passive.subdomains"] == timedelta(days=14)


def test_derive_node_ttls_omits_ttl_none_nodes(
    db: Session, engagement: Engagement, catalog: list[Methodology]
) -> None:
    """WHOIS carries ttl_days=None in the seed (registration metadata is
    stable). It must not appear in the ttl map — its absence means
    ``sweep_stale`` skips it."""
    meth.select_for_engagement(
        db, engagement_id=engagement.id, slug="ptes",
        now=datetime(2026, 7, 23, tzinfo=UTC),
    )
    db.refresh(engagement)
    ttls = meth.derive_node_ttls(engagement)
    assert "recon.passive.whois" not in ttls


def test_derive_node_ttls_empty_snapshot(
    db: Session, engagement: Engagement
) -> None:
    ttls = meth.derive_node_ttls(engagement)
    assert ttls == {}


# ---------------------------------------------------------------------------
# End-to-end: A1 → A2 baseline-complete
# ---------------------------------------------------------------------------


def test_a1_and_a2_agree_on_baseline_complete(
    db: Session, engagement: Engagement, catalog: list[Methodology]
) -> None:
    """The whole point: A1 derives A2's ``expected`` and baseline flips."""
    meth.select_for_engagement(
        db, engagement_id=engagement.id, slug="osint-minimal",
        now=datetime(2026, 7, 23, tzinfo=UTC),
    )
    db.refresh(engagement)
    expected = meth.derive_expected_triples(
        engagement,
        scope_item_ids_by_asset_class={"domain": ["s-1"]},
    )
    # Nothing satisfied yet.
    is_complete, missing = cov.check_baseline_complete(
        db, engagement_id=engagement.id, expected=expected
    )
    assert is_complete is False
    assert len(missing) == len(expected)
    # Satisfy every OSINT baseline node.
    for node_id, asset_class, _sk in expected:
        cov.record_coverage_attempt(
            db,
            engagement_id=engagement.id,
            node_id=node_id,
            node_tier=CoverageNodeTier.baseline,
            asset_class=asset_class,
            scope_subset=["s-1"],
            status=cov.CoverageRecordStatus.satisfied,
            now=datetime(2026, 7, 23, tzinfo=UTC),
        )
    is_complete, missing = cov.check_baseline_complete(
        db, engagement_id=engagement.id, expected=expected
    )
    assert is_complete is True
    assert missing == []
