"""v3 shared contract tests (architecture-v3-tracker PR 0).

Covers the three pieces both tracks code against:
- ``WorkItem.disposition`` (new nullable how/where axis, backfilled from
  ``executor_type`` by the migration).
- ``Engagement.phase`` + ``baseline_completed_at`` (orthogonal lifecycle axis;
  defaults to ``baseline``).
- ``CoverageRecord`` (schema both tracks touch — A writes, B reads).
- ``app.engagement.milestones`` event contract (names + payload shapes).

The disposition backfill itself is a one-shot migration step (straightforward
CASE SQL) and is exercised by the ``alembic upgrade head`` check, not here —
same treatment the Memory PR gave its migration. Time is wall-clock-free.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engagement import milestones as ms
from app.models import (
    CoverageNodeTier,
    CoverageRecord,
    CoverageRecordStatus,
    Engagement,
    EngagementPhase,
    EngagementStatus,
    EngagementWorkState,
    WorkItem,
    WorkItemDisposition,
    WorkItemExecutor,
    WorkItemPriority,
    WorkItemStatus,
)


@pytest.fixture()
def engagement(db: Session) -> Engagement:
    eng = Engagement(
        name="Contract Test",
        slug=f"pr0-{uuid.uuid4().hex[:8]}",
        status=EngagementStatus.active,
        work_state=EngagementWorkState.active,
    )
    db.add(eng)
    db.flush()
    return eng


def _work_item(db: Session, engagement: Engagement, **overrides) -> WorkItem:
    wi = WorkItem(
        engagement_id=engagement.id,
        title=overrides.pop("title", "t"),
        status=overrides.pop("status", WorkItemStatus.ready),
        priority=overrides.pop("priority", WorkItemPriority.medium),
        executor_type=overrides.pop("executor_type", WorkItemExecutor.unassigned),
        **overrides,
    )
    db.add(wi)
    db.flush()
    return wi


# ---------------------------------------------------------------------------
# Engagement phase — orthogonal lifecycle axis, defaults to baseline
# ---------------------------------------------------------------------------


def test_engagement_defaults_to_baseline_phase(db: Session, engagement: Engagement) -> None:
    db.refresh(engagement)
    assert engagement.phase == EngagementPhase.baseline
    assert engagement.baseline_completed_at is None


def test_phase_is_independent_of_status_and_work_state(db: Session) -> None:
    """phase flips on baseline-complete only; status/work_state move on their own."""
    eng = Engagement(
        name="Orthogonal",
        slug=f"ortho-{uuid.uuid4().hex[:8]}",
        status=EngagementStatus.active,
        work_state=EngagementWorkState.active,
        phase=EngagementPhase.exploration,
        baseline_completed_at=datetime.now(tz=UTC),
    )
    db.add(eng)
    db.flush()
    db.refresh(eng)
    # All three axes coexist without contradiction.
    assert eng.status == EngagementStatus.active
    assert eng.work_state == EngagementWorkState.active
    assert eng.phase == EngagementPhase.exploration


# ---------------------------------------------------------------------------
# WorkItem disposition — new nullable how/where axis
# ---------------------------------------------------------------------------


def test_disposition_is_nullable_and_round_trips(db: Session, engagement: Engagement) -> None:
    wi = _work_item(db, engagement)  # no disposition set
    assert wi.disposition is None
    wi.disposition = WorkItemDisposition.tool_backed
    db.flush()
    db.refresh(wi)
    assert wi.disposition == WorkItemDisposition.tool_backed


def test_disposition_values_are_string_enum() -> None:
    """The hyphenated wire values match what the migration enum + the
    feat/needs-decision-redesign UX branch expect."""
    assert WorkItemDisposition.tool_backed == "tool-backed"
    assert WorkItemDisposition.tool_backed_mcp == "tool-backed-mcp"
    assert WorkItemDisposition.manual_local == "manual-local"
    assert WorkItemDisposition.build == "build"
    assert WorkItemDisposition.blocked == "blocked"
    assert WorkItemDisposition.needs_decision == "needs-decision"
    assert WorkItemDisposition.out_of_scope == "out-of-scope"


def test_disposition_coexists_with_executor_type(db: Session, engagement: Engagement) -> None:
    """The two axes are independent on the row — executor_type is NOT dropped
    until Convergence C5, so both must persist together."""
    wi = _work_item(
        db, engagement,
        executor_type=WorkItemExecutor.tactical,
        disposition=WorkItemDisposition.tool_backed,
    )
    db.flush()
    db.refresh(wi)
    assert wi.executor_type == WorkItemExecutor.tactical
    assert wi.disposition == WorkItemDisposition.tool_backed


# ---------------------------------------------------------------------------
# CoverageRecord — schema both tracks touch (A writes, B reads)
# ---------------------------------------------------------------------------


def test_coverage_record_defaults_and_round_trip(db: Session, engagement: Engagement) -> None:
    rec = CoverageRecord(
        engagement_id=engagement.id,
        node_id="recon.passive.subdomains",
        node_tier=CoverageNodeTier.baseline,
        asset_class="domain",
    )
    db.add(rec)
    db.flush()
    db.refresh(rec)
    assert rec.status == CoverageRecordStatus.pending  # default
    assert rec.scope_subset == {}  # JSONB default
    assert rec.methodology_id is None  # plain UUID until A1
    assert rec.playbook_run_id is None  # plain UUID until A3
    assert rec.satisfied_at is None


def test_coverage_satisfied_includes_clean_finds(
    db: Session, engagement: Engagement
) -> None:
    """Per architecture-answers Q3: 'satisfied' includes a clean 'found nothing'
    result — coverage is about whether the technique ran, not whether it found."""
    rec = CoverageRecord(
        engagement_id=engagement.id,
        node_id="recon.passive.whois",
        node_tier=CoverageNodeTier.baseline,
        asset_class="domain",
        status=CoverageRecordStatus.satisfied,
        scope_subset={"scope_item_ids": [str(uuid.uuid4())]},
        satisfied_at=datetime.now(tz=UTC),
        notes="ran clean; nothing returned",
    )
    db.add(rec)
    db.flush()
    found = db.execute(
        select(CoverageRecord).where(
            CoverageRecord.engagement_id == engagement.id,
            CoverageRecord.node_id == "recon.passive.whois",
        )
    ).scalar_one()
    assert found.status == CoverageRecordStatus.satisfied
    assert found.scope_subset["scope_item_ids"]


# ---------------------------------------------------------------------------
# Milestone event contract — names + payload shapes (no DB, no stream)
# ---------------------------------------------------------------------------


def test_milestone_names_are_canonical() -> None:
    assert ms.COLLECTION_JOB_COMPLETED == "collection.job.completed"
    assert ms.COVERAGE_GAP_OPENED == "coverage.gap.opened"
    assert ms.BASELINE_COMPLETED == "baseline.completed"
    assert frozenset(
        {"collection.job.completed", "coverage.gap.opened", "baseline.completed"}
    ) == ms.MILESTONE_EVENT_TYPES


def test_collection_job_completed_builder_shape() -> None:
    env = ms.collection_job_completed(
        engagement_id="eng-1",
        playbook_run_id="run-1",
        methodology_id="meth-1",
        node_ids=["recon.passive.subdomains", "recon.passive.cert"],
        asset_class="domain",
        scope_subset=["scope-1", "scope-2"],
        findings_summary={"new": 3, "unvalidated": 1, "high_severity": 1, "total": 5},
    )
    assert env["type"] == ms.COLLECTION_JOB_COMPLETED
    assert env["engagement_id"] == "eng-1"
    assert env["node_ids"] == ["recon.passive.subdomains", "recon.passive.cert"]
    assert env["findings_summary"]["new"] == 3
    # No free text in the rollup — counts only, so B3 decides significance cheaply.
    assert set(env["findings_summary"]) == {"new", "unvalidated", "high_severity", "total"}


def test_coverage_gap_opened_builder_shape() -> None:
    env = ms.coverage_gap_opened(
        engagement_id="eng-1",
        node_id="recon.active.portscan",
        node_tier="baseline",
        asset_class="ip",
        reason="unsatisfied for 10.0.0.0/24",
    )
    assert env["type"] == ms.COVERAGE_GAP_OPENED
    assert env["node_tier"] == "baseline"


def test_baseline_completed_builder_shape() -> None:
    env = ms.baseline_completed(
        engagement_id="eng-1",
        methodology_id="meth-1",
        baseline_completed_at="2026-07-22T00:00:00Z",
    )
    assert env["type"] == ms.BASELINE_COMPLETED
    assert env["baseline_completed_at"] == "2026-07-22T00:00:00Z"
