"""Coverage service tests — Track A step A2 (architecture-v3-tracker).

Proves the contract A2 exists to guarantee:
- ``record_coverage_attempt`` appends new rows (never updates) and stamps
  ``satisfied_at`` only when status=satisfied.
- ``latest_by_node`` returns the newest row per (node, asset_class, scope) —
  the view baseline-complete + the stale sweep both read.
- ``check_baseline_complete`` is a pure predicate over the latest view;
  ``stale`` counts as unsatisfied by design.
- ``mark_baseline_completed`` flips phase + stamps the engagement +
  enqueues the ``baseline.completed`` milestone through the shared outbox;
  a second call is a no-op.
- ``open_coverage_gap`` enqueues ``coverage.gap.opened`` with the right
  stream + payload shape (no DB row — gaps are a signal).
- ``sweep_stale`` APPENDS ``stale`` rows for TTL-lapsed satisfied nodes;
  freshly-satisfied ones are untouched; attempt history is preserved.

Isolation: tests flush; the ``db`` fixture rolls back so nothing persists.
Time is driven via the ``now`` parameter (no wall-clock dependency).
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engagement import milestones as ms
from app.models import (
    ActorType,
    AuditLog,
    CommandOutbox,
    CoverageNodeTier,
    CoverageRecord,
    CoverageRecordStatus,
    Engagement,
    EngagementPhase,
    EngagementStatus,
    EngagementWorkState,
)
from app.runs.streams import outbound_stream
from app.services import coverage as cov


@pytest.fixture()
def engagement(db: Session) -> Engagement:
    eng = Engagement(
        name="Coverage Test",
        slug=f"cov-{uuid.uuid4().hex[:8]}",
        status=EngagementStatus.active,
        work_state=EngagementWorkState.active,
    )
    db.add(eng)
    db.flush()
    return eng


def _at(days: int = 0, hours: int = 0) -> datetime:
    return datetime(2026, 7, 23, 12, 0, tzinfo=UTC) + timedelta(days=days, hours=hours)


# ---------------------------------------------------------------------------
# record_coverage_attempt
# ---------------------------------------------------------------------------


def test_record_stamps_satisfied_at_only_when_satisfied(
    db: Session, engagement: Engagement
) -> None:
    row = cov.record_coverage_attempt(
        db,
        engagement_id=engagement.id,
        node_id="recon.passive.subdomains",
        node_tier=CoverageNodeTier.baseline,
        asset_class="domain",
        scope_subset=["scope-1"],
        status=CoverageRecordStatus.satisfied,
        now=_at(),
    )
    assert row.satisfied_at == _at()
    assert row.status is CoverageRecordStatus.satisfied

    partial = cov.record_coverage_attempt(
        db,
        engagement_id=engagement.id,
        node_id="recon.passive.subdomains",
        node_tier=CoverageNodeTier.baseline,
        asset_class="domain",
        scope_subset=["scope-1"],
        status=CoverageRecordStatus.partial,
        now=_at(hours=1),
    )
    # partial / attempted / pending / failed / stale never stamp satisfied_at.
    assert partial.satisfied_at is None


def test_record_appends_never_updates(db: Session, engagement: Engagement) -> None:
    """Two attempts on the same (node, asset_class, scope) → two rows, not one
    row overwritten. Attempt history is the contract (architecture-answers Q3)."""
    scope = ["scope-1"]
    cov.record_coverage_attempt(
        db,
        engagement_id=engagement.id,
        node_id="recon.passive.subdomains",
        node_tier=CoverageNodeTier.baseline,
        asset_class="domain",
        scope_subset=scope,
        status=CoverageRecordStatus.attempted,
        now=_at(),
    )
    cov.record_coverage_attempt(
        db,
        engagement_id=engagement.id,
        node_id="recon.passive.subdomains",
        node_tier=CoverageNodeTier.baseline,
        asset_class="domain",
        scope_subset=scope,
        status=CoverageRecordStatus.satisfied,
        now=_at(hours=1),
    )
    rows = db.execute(
        select(CoverageRecord).where(CoverageRecord.engagement_id == engagement.id)
    ).scalars().all()
    assert len(rows) == 2


def test_record_writes_attributed_audit_log(db: Session, engagement: Engagement) -> None:
    cov.record_coverage_attempt(
        db,
        engagement_id=engagement.id,
        node_id="recon.passive.cert",
        node_tier=CoverageNodeTier.baseline,
        asset_class="domain",
        scope_subset=["scope-1"],
        status=CoverageRecordStatus.satisfied,
        actor_type=ActorType.agent,
        actor_id="playbook-runner",
        now=_at(),
    )
    log = db.execute(
        select(AuditLog).where(
            AuditLog.engagement_id == engagement.id,
            AuditLog.event_type == "coverage.recorded",
        )
    ).scalar_one()
    assert log.actor_type is ActorType.agent
    assert log.actor_id == "playbook-runner"
    assert log.payload["node_id"] == "recon.passive.cert"
    assert log.payload["status"] == "satisfied"


# ---------------------------------------------------------------------------
# scope_key
# ---------------------------------------------------------------------------


def test_scope_key_is_order_independent() -> None:
    assert cov.scope_key(["a", "b"]) == cov.scope_key(["b", "a"])


def test_scope_key_distinguishes_different_scopes() -> None:
    assert cov.scope_key(["a"]) != cov.scope_key(["a", "b"])
    assert cov.scope_key([]) == "[]"
    assert cov.scope_key(None) == "[]"


# ---------------------------------------------------------------------------
# latest_by_node
# ---------------------------------------------------------------------------


def test_latest_by_node_returns_newest_per_triple(
    db: Session, engagement: Engagement
) -> None:
    scope = ["s-1"]
    cov.record_coverage_attempt(
        db,
        engagement_id=engagement.id,
        node_id="n",
        node_tier=CoverageNodeTier.baseline,
        asset_class="domain",
        scope_subset=scope,
        status=CoverageRecordStatus.attempted,
        now=_at(),
    )
    later = cov.record_coverage_attempt(
        db,
        engagement_id=engagement.id,
        node_id="n",
        node_tier=CoverageNodeTier.baseline,
        asset_class="domain",
        scope_subset=scope,
        status=CoverageRecordStatus.satisfied,
        now=_at(hours=1),
    )
    latest = cov.latest_by_node(db, engagement_id=engagement.id)
    key = ("n", "domain", cov.scope_key(scope))
    assert latest[key].id == later.id
    assert latest[key].status is CoverageRecordStatus.satisfied


def test_latest_by_node_isolates_by_scope(db: Session, engagement: Engagement) -> None:
    """Different scope selections against the same node are independent."""
    cov.record_coverage_attempt(
        db, engagement_id=engagement.id, node_id="n", node_tier=CoverageNodeTier.baseline,
        asset_class="domain", scope_subset=["s-1"],
        status=CoverageRecordStatus.satisfied, now=_at(),
    )
    cov.record_coverage_attempt(
        db, engagement_id=engagement.id, node_id="n", node_tier=CoverageNodeTier.baseline,
        asset_class="domain", scope_subset=["s-2"],
        status=CoverageRecordStatus.pending, now=_at(),
    )
    latest = cov.latest_by_node(db, engagement_id=engagement.id)
    assert latest[("n", "domain", cov.scope_key(["s-1"]))].status is CoverageRecordStatus.satisfied
    assert latest[("n", "domain", cov.scope_key(["s-2"]))].status is CoverageRecordStatus.pending


def test_latest_by_node_filters_by_tier(db: Session, engagement: Engagement) -> None:
    cov.record_coverage_attempt(
        db, engagement_id=engagement.id, node_id="baseline-n",
        node_tier=CoverageNodeTier.baseline, asset_class="domain", scope_subset=["s"],
        status=CoverageRecordStatus.satisfied, now=_at(),
    )
    cov.record_coverage_attempt(
        db, engagement_id=engagement.id, node_id="exploration-n",
        node_tier=CoverageNodeTier.exploration, asset_class="domain", scope_subset=["s"],
        status=CoverageRecordStatus.attempted, now=_at(),
    )
    baseline_only = cov.latest_by_node(
        db, engagement_id=engagement.id, node_tier=CoverageNodeTier.baseline
    )
    assert set(baseline_only.keys()) == {("baseline-n", "domain", cov.scope_key(["s"]))}


# ---------------------------------------------------------------------------
# check_baseline_complete
# ---------------------------------------------------------------------------


def test_baseline_complete_when_every_expected_is_satisfied(
    db: Session, engagement: Engagement
) -> None:
    expected = [
        ("recon.passive.subdomains", "domain", cov.scope_key(["s-1"])),
        ("recon.passive.whois", "domain", cov.scope_key(["s-1"])),
    ]
    for node_id, _asset, _scope in expected:
        cov.record_coverage_attempt(
            db, engagement_id=engagement.id, node_id=node_id,
            node_tier=CoverageNodeTier.baseline, asset_class="domain",
            scope_subset=["s-1"], status=CoverageRecordStatus.satisfied, now=_at(),
        )
    is_complete, missing = cov.check_baseline_complete(
        db, engagement_id=engagement.id, expected=expected
    )
    assert is_complete is True
    assert missing == []


def test_baseline_incomplete_reports_unsatisfied(
    db: Session, engagement: Engagement
) -> None:
    expected = [
        ("recon.passive.subdomains", "domain", cov.scope_key(["s-1"])),
        ("recon.passive.whois", "domain", cov.scope_key(["s-1"])),
    ]
    cov.record_coverage_attempt(
        db, engagement_id=engagement.id, node_id="recon.passive.subdomains",
        node_tier=CoverageNodeTier.baseline, asset_class="domain",
        scope_subset=["s-1"], status=CoverageRecordStatus.satisfied, now=_at(),
    )
    is_complete, missing = cov.check_baseline_complete(
        db, engagement_id=engagement.id, expected=expected
    )
    assert is_complete is False
    assert missing == [("recon.passive.whois", "domain", cov.scope_key(["s-1"]))]


def test_baseline_stale_counts_as_unsatisfied(
    db: Session, engagement: Engagement
) -> None:
    """A satisfied row that later goes stale MUST re-open the baseline gate —
    that is the point of the stored lapse (architecture-answers Q3)."""
    expected = [("n", "domain", cov.scope_key(["s"]))]
    cov.record_coverage_attempt(
        db, engagement_id=engagement.id, node_id="n",
        node_tier=CoverageNodeTier.baseline, asset_class="domain",
        scope_subset=["s"], status=CoverageRecordStatus.satisfied, now=_at(),
    )
    cov.record_coverage_attempt(
        db, engagement_id=engagement.id, node_id="n",
        node_tier=CoverageNodeTier.baseline, asset_class="domain",
        scope_subset=["s"], status=CoverageRecordStatus.stale, now=_at(days=30),
    )
    is_complete, missing = cov.check_baseline_complete(
        db, engagement_id=engagement.id, expected=expected
    )
    assert is_complete is False
    assert missing == [("n", "domain", cov.scope_key(["s"]))]


# ---------------------------------------------------------------------------
# mark_baseline_completed
# ---------------------------------------------------------------------------


def test_mark_baseline_completed_flips_phase_and_enqueues_milestone(
    db: Session, engagement: Engagement
) -> None:
    assert engagement.phase is EngagementPhase.baseline
    assert engagement.baseline_completed_at is None
    methodology_id = uuid.uuid4()

    actor_id = str(uuid.uuid4())
    eng, entry = cov.mark_baseline_completed(
        db,
        engagement_id=engagement.id,
        methodology_id=methodology_id,
        now=_at(),
        actor_type=ActorType.user,
        actor_id=actor_id,
    )
    db.refresh(eng)
    assert eng.phase is EngagementPhase.exploration
    assert eng.baseline_completed_at == _at()
    # Milestone landed in the durable outbox on the engagement's stream.
    assert entry is not None
    assert entry.stream_name == outbound_stream(engagement.id)
    envelope = json.loads(entry.encoded_payload["data"])
    assert envelope["type"] == ms.BASELINE_COMPLETED
    assert envelope["engagement_id"] == str(engagement.id)
    assert envelope["methodology_id"] == str(methodology_id)
    assert envelope["baseline_completed_at"] == _at().isoformat()
    assert envelope["acting_user_id"] == actor_id


def test_mark_baseline_completed_is_idempotent(
    db: Session, engagement: Engagement
) -> None:
    """A second call on an already-completed engagement is a no-op — no
    duplicate milestone, no phase flip, no timestamp overwrite."""
    methodology_id = uuid.uuid4()
    cov.mark_baseline_completed(
        db, engagement_id=engagement.id, methodology_id=methodology_id, now=_at(),
    )
    eng2, entry2 = cov.mark_baseline_completed(
        db, engagement_id=engagement.id, methodology_id=methodology_id, now=_at(days=1),
    )
    db.refresh(eng2)
    assert entry2 is None
    assert eng2.baseline_completed_at == _at()  # not overwritten
    outbox_rows = db.execute(
        select(CommandOutbox).where(CommandOutbox.engagement_id == engagement.id)
    ).scalars().all()
    # exactly one baseline.completed row (the second call didn't stage another).
    baseline_rows = [
        r for r in outbox_rows if r.idempotency_key.startswith("baseline.completed:")
    ]
    assert len(baseline_rows) == 1


def test_mark_baseline_completed_missing_engagement_raises(db: Session) -> None:
    with pytest.raises(ValueError):
        cov.mark_baseline_completed(
            db, engagement_id=uuid.uuid4(), methodology_id=uuid.uuid4(), now=_at(),
        )


# ---------------------------------------------------------------------------
# open_coverage_gap
# ---------------------------------------------------------------------------


def test_open_coverage_gap_enqueues_milestone(db: Session, engagement: Engagement) -> None:
    entry = cov.open_coverage_gap(
        db,
        engagement_id=engagement.id,
        node_id="recon.active.portscan",
        node_tier=CoverageNodeTier.baseline,
        asset_class="ip",
        reason="unsatisfied for 10.0.0.0/24",
        acting_user_id=uuid.UUID("00000000-0000-0000-0000-000000000123"),
        dedupe_key="test-gap-1",
    )
    assert entry.stream_name == outbound_stream(engagement.id)
    envelope = json.loads(entry.encoded_payload["data"])
    assert envelope["type"] == ms.COVERAGE_GAP_OPENED
    assert envelope["node_id"] == "recon.active.portscan"
    assert envelope["node_tier"] == "baseline"
    assert envelope["asset_class"] == "ip"
    assert envelope["reason"] == "unsatisfied for 10.0.0.0/24"
    assert envelope["acting_user_id"] == "00000000-0000-0000-0000-000000000123"


def test_open_coverage_gap_dedupe_key_prevents_duplicates(
    db: Session, engagement: Engagement
) -> None:
    """Same dedupe_key = the outbox returns the existing row instead of a
    second one — the shared idempotency contract."""
    a = cov.open_coverage_gap(
        db, engagement_id=engagement.id, node_id="n",
        node_tier=CoverageNodeTier.baseline, asset_class="domain",
        reason="r", dedupe_key="fixed-key",
    )
    b = cov.open_coverage_gap(
        db, engagement_id=engagement.id, node_id="n",
        node_tier=CoverageNodeTier.baseline, asset_class="domain",
        reason="r", dedupe_key="fixed-key",
    )
    assert a.id == b.id


# ---------------------------------------------------------------------------
# sweep_stale
# ---------------------------------------------------------------------------


def test_sweep_stale_appends_new_row_for_lapsed_satisfied(
    db: Session, engagement: Engagement
) -> None:
    cov.record_coverage_attempt(
        db, engagement_id=engagement.id, node_id="short-ttl",
        node_tier=CoverageNodeTier.baseline, asset_class="domain",
        scope_subset=["s"], status=CoverageRecordStatus.satisfied, now=_at(),
    )
    lapsed = cov.sweep_stale(
        db,
        engagement_id=engagement.id,
        node_ttls={"short-ttl": timedelta(days=7)},
        now=_at(days=8),
    )
    assert len(lapsed) == 1
    assert lapsed[0].status is CoverageRecordStatus.stale
    # Original satisfied row is untouched — attempt history preserved.
    all_rows = db.execute(
        select(CoverageRecord).where(
            CoverageRecord.engagement_id == engagement.id,
            CoverageRecord.node_id == "short-ttl",
        )
    ).scalars().all()
    assert len(all_rows) == 2
    statuses = sorted(r.status.value for r in all_rows)
    assert statuses == ["satisfied", "stale"]


def test_sweep_stale_skips_fresh_satisfied(db: Session, engagement: Engagement) -> None:
    cov.record_coverage_attempt(
        db, engagement_id=engagement.id, node_id="fresh",
        node_tier=CoverageNodeTier.baseline, asset_class="domain",
        scope_subset=["s"], status=CoverageRecordStatus.satisfied, now=_at(),
    )
    lapsed = cov.sweep_stale(
        db,
        engagement_id=engagement.id,
        node_ttls={"fresh": timedelta(days=7)},
        now=_at(days=1),
    )
    assert lapsed == []


def test_sweep_stale_skips_nodes_without_ttl(
    db: Session, engagement: Engagement
) -> None:
    """A node not present in ``node_ttls`` never lapses — no TTL configured
    means the technique isn't time-sensitive."""
    cov.record_coverage_attempt(
        db, engagement_id=engagement.id, node_id="no-ttl",
        node_tier=CoverageNodeTier.baseline, asset_class="domain",
        scope_subset=["s"], status=CoverageRecordStatus.satisfied, now=_at(),
    )
    lapsed = cov.sweep_stale(
        db,
        engagement_id=engagement.id,
        node_ttls={},
        now=_at(days=365),
    )
    assert lapsed == []


def test_sweep_stale_ignores_non_satisfied_latest(
    db: Session, engagement: Engagement
) -> None:
    """Only ``satisfied`` rows lapse — a ``pending`` / ``failed`` / already-
    ``stale`` latest doesn't get a second stale row appended."""
    cov.record_coverage_attempt(
        db, engagement_id=engagement.id, node_id="failed-n",
        node_tier=CoverageNodeTier.baseline, asset_class="domain",
        scope_subset=["s"], status=CoverageRecordStatus.failed, now=_at(),
    )
    cov.record_coverage_attempt(
        db, engagement_id=engagement.id, node_id="already-stale",
        node_tier=CoverageNodeTier.baseline, asset_class="domain",
        scope_subset=["s"], status=CoverageRecordStatus.stale, now=_at(),
    )
    lapsed = cov.sweep_stale(
        db,
        engagement_id=engagement.id,
        node_ttls={"failed-n": timedelta(days=1), "already-stale": timedelta(days=1)},
        now=_at(days=30),
    )
    assert lapsed == []


def test_sweep_stale_writes_audit_log_only_when_something_lapsed(
    db: Session, engagement: Engagement
) -> None:
    cov.record_coverage_attempt(
        db, engagement_id=engagement.id, node_id="n",
        node_tier=CoverageNodeTier.baseline, asset_class="domain",
        scope_subset=["s"], status=CoverageRecordStatus.satisfied, now=_at(),
    )
    # No lapse yet → no audit row.
    cov.sweep_stale(
        db, engagement_id=engagement.id,
        node_ttls={"n": timedelta(days=7)}, now=_at(days=1),
    )
    logs_before = db.execute(
        select(AuditLog).where(
            AuditLog.engagement_id == engagement.id,
            AuditLog.event_type == "coverage.stale_sweep",
        )
    ).scalars().all()
    assert logs_before == []

    # Time advances past TTL → lapse + audit row with the count.
    cov.sweep_stale(
        db, engagement_id=engagement.id,
        node_ttls={"n": timedelta(days=7)}, now=_at(days=30),
    )
    log = db.execute(
        select(AuditLog).where(
            AuditLog.engagement_id == engagement.id,
            AuditLog.event_type == "coverage.stale_sweep",
        )
    ).scalar_one()
    assert log.payload["count"] == 1
