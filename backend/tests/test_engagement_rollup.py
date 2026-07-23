"""Engagement rollup tests (v3 B2).

The deterministic aggregations the intelligence plane consumes. Everything
here is SQL counts — no LLM. Covers the milestone FindingsSummary (significance
trigger), the significant-finding gather B3 batches, grouped counts, the
coverage rollup, and soft-delete exclusion.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.models import (
    CoverageNodeTier,
    CoverageRecord,
    CoverageRecordStatus,
    Engagement,
    EngagementStatus,
    EngagementWorkState,
    Finding,
    FindingOrigin,
    FindingPhase,
    FindingStatus,
    Severity,
)
from app.services import engagement_rollup as rollup


@pytest.fixture()
def engagement(db: Session) -> Engagement:
    eng = Engagement(
        name="Rollup Test",
        slug=f"roll-{uuid.uuid4().hex[:8]}",
        status=EngagementStatus.active,
        work_state=EngagementWorkState.active,
    )
    db.add(eng)
    db.flush()
    return eng


def _finding(
    db: Session,
    engagement: Engagement,
    *,
    severity: Severity = Severity.info,
    status: FindingStatus = FindingStatus.validated,
    phase: FindingPhase = FindingPhase.osint,
    created_at: datetime | None = None,
    deleted: bool = False,
    summary: str | None = None,
) -> Finding:
    f = Finding(
        engagement_id=engagement.id,
        title=f"finding-{uuid.uuid4().hex[:6]}",
        summary=summary,
        target="example.com",
        severity=severity,
        status=status,
        phase=phase,
    )
    if created_at is not None:
        f.created_at = created_at
    if deleted:
        f.deleted_at = datetime.now(tz=UTC)
    db.add(f)
    db.flush()
    return f


# ---------------------------------------------------------------------------
# findings_summary — the milestone trigger (counts-only)
# ---------------------------------------------------------------------------


def test_findings_summary_counts_predicate_classes(db: Session, engagement: Engagement) -> None:
    now = datetime.now(tz=UTC)
    _finding(
        db, engagement,
        severity=Severity.high,
        status=FindingStatus.pending_validation,
        created_at=now,
    )
    _finding(
        db, engagement, severity=Severity.info,
        status=FindingStatus.validated, created_at=now,
    )
    _finding(
        db, engagement, severity=Severity.critical,
        status=FindingStatus.needs_review, created_at=now,
    )
    _finding(
        db, engagement, severity=Severity.low,
        status=FindingStatus.validated,
        created_at=now - timedelta(days=5),
    )

    summary = rollup.findings_summary(db, engagement_id=engagement.id)

    assert summary["total"] == 4
    # new = all (no `since`) → every finding is "new" on first analysis
    assert summary["new"] == 4
    # unvalidated = pending_validation + needs_review (rejected/false_positive
    # are disposed, not unvalidated)
    assert summary["unvalidated"] == 2
    # high_severity = high + critical
    assert summary["high_severity"] == 2


def test_findings_summary_respects_since_window(db: Session, engagement: Engagement) -> None:
    cutoff = datetime.now(tz=UTC) - timedelta(hours=1)
    _finding(db, engagement, created_at=datetime.now(tz=UTC) - timedelta(days=2))  # old
    _finding(db, engagement, created_at=datetime.now(tz=UTC))  # new

    summary = rollup.findings_summary(db, engagement_id=engagement.id, since=cutoff)

    assert summary["total"] == 2
    assert summary["new"] == 1  # only the recent one


def test_findings_summary_excludes_soft_deleted(db: Session, engagement: Engagement) -> None:
    _finding(db, engagement)
    _finding(db, engagement, deleted=True)

    summary = rollup.findings_summary(db, engagement_id=engagement.id)

    assert summary["total"] == 1


# ---------------------------------------------------------------------------
# significant_finding_ids — the gather set B3 batches (deduped)
# ---------------------------------------------------------------------------


def test_significant_finding_ids_dedupes_across_predicates(
    db: Session, engagement: Engagement
) -> None:
    # One finding matching all three predicates (high + unvalidated + new) → counted once.
    triple = _finding(
        db, engagement,
        severity=Severity.critical,
        status=FindingStatus.pending_validation,
    )
    # Low + validated + old → not significant.
    _finding(
        db, engagement,
        severity=Severity.low,
        status=FindingStatus.validated,
        created_at=datetime.now(tz=UTC) - timedelta(days=10),
    )
    # High but validated + old → significant via high_severity only.
    high_only = _finding(
        db, engagement,
        severity=Severity.high,
        status=FindingStatus.validated,
        created_at=datetime.now(tz=UTC) - timedelta(days=10),
    )

    ids = rollup.significant_finding_ids(
        db, engagement_id=engagement.id, since=datetime.now(tz=UTC) - timedelta(days=1)
    )

    assert set(ids) == {triple.id, high_only.id}
    assert len(ids) == 2  # no dupes


def test_significant_finding_ids_uses_run_lineage_for_new_set(
    db: Session, engagement: Engagement
) -> None:
    old = datetime.now(tz=UTC) - timedelta(days=10)
    produced = _finding(
        db,
        engagement,
        severity=Severity.low,
        status=FindingStatus.validated,
        created_at=old,
    )
    _finding(
        db,
        engagement,
        severity=Severity.low,
        status=FindingStatus.validated,
        created_at=old,
    )
    thread_id = uuid.uuid4()
    db.add(
        FindingOrigin(
            finding_id=produced.id,
            thread_id=thread_id,
            source_tool="test",
        )
    )
    db.flush()

    ids = rollup.significant_finding_ids(
        db,
        engagement_id=engagement.id,
        thread_id=thread_id,
    )

    assert ids == [produced.id]
    assert rollup.findings_summary(
        db,
        engagement_id=engagement.id,
        thread_id=thread_id,
    )["new"] == 1



def test_significant_finding_ids_excludes_soft_deleted(db: Session, engagement: Engagement) -> None:
    live = _finding(db, engagement, severity=Severity.high)
    _finding(db, engagement, severity=Severity.critical, deleted=True)

    ids = rollup.significant_finding_ids(db, engagement_id=engagement.id)

    assert ids == [live.id]


def test_significant_finding_batch_is_bounded_prioritized_and_fingerprinted(
    db: Session, engagement: Engagement
) -> None:
    low = _finding(
        db,
        engagement,
        severity=Severity.low,
        status=FindingStatus.pending_validation,
        summary="low evidence " * 100,
    )
    critical = _finding(
        db,
        engagement,
        severity=Severity.critical,
        status=FindingStatus.pending_validation,
        summary="critical evidence " * 100,
    )
    _finding(
        db,
        engagement,
        severity=Severity.high,
        status=FindingStatus.pending_validation,
        summary="high evidence " * 100,
    )

    first = rollup.significant_finding_batch(
        db,
        engagement_id=engagement.id,
        token_budget=250,
        max_items=2,
    )

    assert first["total"] == 3
    assert first["included"] < first["total"]
    assert first["capped"] is True
    assert first["items"][0]["id"] == str(critical.id)
    assert first["token_estimate"] <= 250 or first["included"] == 1
    assert "details" not in first["items"][0]
    assert len(first["items"][0]["summary"]) <= 600

    low.severity = Severity.critical
    db.flush()
    changed = rollup.significant_finding_batch(
        db,
        engagement_id=engagement.id,
        token_budget=250,
        max_items=2,
    )
    assert changed["fingerprint"] != first["fingerprint"]


def test_significant_finding_batch_is_engagement_scoped(
    db: Session, engagement: Engagement
) -> None:
    other = Engagement(
        name="Other Rollup",
        slug=f"other-roll-{uuid.uuid4().hex[:8]}",
        status=EngagementStatus.active,
        work_state=EngagementWorkState.active,
    )
    db.add(other)
    db.flush()
    own = _finding(db, engagement, severity=Severity.high)
    _finding(db, other, severity=Severity.critical)

    batch = rollup.significant_finding_batch(
        db, engagement_id=engagement.id
    )

    assert batch["total"] == 1
    assert [item["id"] for item in batch["items"]] == [str(own.id)]


# ---------------------------------------------------------------------------
# finding_counts — grouped for display
# ---------------------------------------------------------------------------


def test_finding_counts_groups_by_severity_status_phase(
    db: Session, engagement: Engagement
) -> None:
    _finding(
        db, engagement, severity=Severity.high,
        status=FindingStatus.validated, phase=FindingPhase.osint,
    )
    _finding(
        db, engagement, severity=Severity.high,
        status=FindingStatus.pending_validation,
        phase=FindingPhase.vuln_scan,
    )
    _finding(
        db, engagement, severity=Severity.low,
        status=FindingStatus.validated, phase=FindingPhase.osint,
    )

    counts = rollup.finding_counts(db, engagement_id=engagement.id)

    assert counts["by_severity"]["high"] == 2
    assert counts["by_severity"]["low"] == 1
    assert counts["by_status"]["validated"] == 2
    assert counts["by_status"]["pending_validation"] == 1
    assert counts["by_phase"]["osint"] == 2
    assert counts["by_phase"]["vuln_scan"] == 1


# ---------------------------------------------------------------------------
# coverage_rollup — status counts per tier (read-side; A2 owns baseline-complete)
# ---------------------------------------------------------------------------


def _coverage(
    db: Session,
    engagement: Engagement,
    *,
    tier: CoverageNodeTier,
    status: CoverageRecordStatus,
    node_id: str,
) -> CoverageRecord:
    rec = CoverageRecord(
        engagement_id=engagement.id,
        node_id=node_id,
        node_tier=tier,
        asset_class="domain",
        status=status,
    )
    db.add(rec)
    db.flush()
    return rec


def test_coverage_rollup_counts_by_tier_and_status(
    db: Session, engagement: Engagement
) -> None:
    _coverage(
        db, engagement, tier=CoverageNodeTier.baseline,
        status=CoverageRecordStatus.satisfied, node_id="a",
    )
    _coverage(
        db, engagement, tier=CoverageNodeTier.baseline,
        status=CoverageRecordStatus.satisfied, node_id="b",
    )
    _coverage(
        db, engagement, tier=CoverageNodeTier.baseline,
        status=CoverageRecordStatus.stale, node_id="c",
    )
    _coverage(
        db, engagement, tier=CoverageNodeTier.exploration,
        status=CoverageRecordStatus.pending, node_id="d",
    )

    roll = rollup.coverage_rollup(db, engagement_id=engagement.id)

    assert roll["baseline"]["satisfied"] == 2
    assert roll["baseline"]["stale"] == 1
    assert roll["exploration"]["pending"] == 1
    # "satisfied includes a clean found-nothing result" still counts as satisfied,
    # never auto-decays in the rollup (decay is A2's stored sweep).
    assert roll["baseline"].get("pending", 0) == 0
