"""Playbook runner tests — Track A step A3a.

Covers the runner's core contract:

- Happy path — all steps succeed → ``PlaybookRun.status=completed``,
  ``CoverageRecord`` per (satisfies_node, scope_item) at ``satisfied``,
  ``collection.job.completed`` milestone emitted with correct
  ``FindingsSummary`` totals and node_ids.
- Partial — mixed success/failure → ``status=partial``, failed steps write
  ``CoverageRecord.status=failed`` + notes, milestone still fires.
- All-fail — every step errors → ``status=failed``, all coverage records
  failed, milestone still fires (B3 decides whether to analyze).
- Executor exception — a thrown tool is a step failure, not a broken run
  (runner catches + converts to StepResult(ok=False)).
- Empty scope subset — ``status=failed`` + ``last_error='empty scope'``.
- No methodology selected — runner skips milestone emission (B3 has
  nothing to hang off).
- Scope substitution — ``{{scope_item}}`` in args_template gets replaced
  per invocation.
- Baseline-complete integration — a fully-successful run against every
  seeded OSINT node flips A2's baseline-complete check.

Uses a ``MockExecutor`` so we don't need real tools wired (that's A3b).
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engagement import milestones as ms
from app.models import (
    CommandOutbox,
    CoverageNodeTier,
    CoverageRecord,
    CoverageRecordStatus,
    Engagement,
    EngagementStatus,
    EngagementWorkState,
    Playbook,
    PlaybookRunStatus,
)
from app.services import coverage as cov
from app.services import methodology as meth
from app.services.playbook import (
    StepResult,
    catalog,
    load_seed_playbooks,
    start_run,
)
from app.services.playbook.executor import substitute_scope


class MockExecutor:
    """Deterministic executor for tests.

    Returns either a canned ``StepResult`` per ``tool_slug`` or a default
    success. Records every invocation for assertions.
    """

    def __init__(
        self,
        *,
        results: dict[str, StepResult] | None = None,
        default: StepResult | None = None,
        raise_for: set[str] | None = None,
    ) -> None:
        self.results = results or {}
        self.default = default or StepResult(
            ok=True,
            findings_new=1,
            findings_total=1,
        )
        self.raise_for = raise_for or set()
        self.calls: list[dict[str, Any]] = []

    def run_step(
        self,
        *,
        tool_slug: str,
        args_template: Mapping[str, Any],
        scope_context: str,
    ) -> StepResult:
        self.calls.append(
            {
                "tool_slug": tool_slug,
                "args": substitute_scope(args_template, scope_context),
                "scope_context": scope_context,
            }
        )
        if tool_slug in self.raise_for:
            raise RuntimeError(f"boom: {tool_slug}")
        return self.results.get(tool_slug, self.default)


@pytest.fixture()
def engagement_with_methodology(db: Session) -> Engagement:
    eng = Engagement(
        name="Runner Test",
        slug=f"run-{uuid.uuid4().hex[:8]}",
        status=EngagementStatus.active,
        work_state=EngagementWorkState.active,
    )
    db.add(eng)
    db.flush()
    meth.load_seed_catalog(db)
    meth.select_for_engagement(
        db,
        engagement_id=eng.id,
        slug="osint-minimal",
        now=datetime(2026, 7, 23, tzinfo=UTC),
    )
    db.refresh(eng)
    return eng


@pytest.fixture()
def osint_playbook(db: Session) -> Playbook:
    load_seed_playbooks(db)
    pb = catalog.get_by_slug(db, "osint-passive-domain")
    assert pb is not None
    return pb


# ---------------------------------------------------------------------------
# scope substitution
# ---------------------------------------------------------------------------


def test_substitute_scope_replaces_placeholder() -> None:
    args = {"domain": "{{scope_item}}", "flag": True, "count": 5}
    out = substitute_scope(args, "example.com")
    assert out == {"domain": "example.com", "flag": True, "count": 5}


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_happy_path_completes_and_writes_coverage(
    db: Session, engagement_with_methodology: Engagement, osint_playbook: Playbook
) -> None:
    ex = MockExecutor(
        default=StepResult(
            ok=True,
            findings_new=2,
            findings_unvalidated=1,
            findings_high_severity=1,
            findings_total=3,
        )
    )
    run = start_run(
        db,
        engagement=engagement_with_methodology,
        playbook=osint_playbook,
        scope_subset=["foo.com"],
        executor=ex,
        now=datetime(2026, 7, 23, tzinfo=UTC),
    )
    assert run.status is PlaybookRunStatus.completed
    assert run.steps_total == 5  # 5 steps × 1 scope item
    assert run.steps_succeeded == 5
    assert run.steps_failed == 0
    # FindingsSummary accumulates.
    assert run.findings_new == 10  # 5 steps × 2
    assert run.findings_total == 15
    # One CoverageRecord per satisfies_node per scope item.
    records = db.execute(
        select(CoverageRecord).where(
            CoverageRecord.playbook_run_id == run.id
        )
    ).scalars().all()
    assert len(records) == 5
    for rec in records:
        assert rec.status is CoverageRecordStatus.satisfied
        assert rec.node_tier is CoverageNodeTier.baseline
        assert rec.methodology_id == engagement_with_methodology.methodology_id
        assert rec.asset_class == "domain"


def test_happy_path_emits_collection_completed_milestone(
    db: Session, engagement_with_methodology: Engagement, osint_playbook: Playbook
) -> None:
    ex = MockExecutor(
        default=StepResult(
            ok=True,
            findings_new=2,
            findings_high_severity=1,
            findings_total=3,
        )
    )
    run = start_run(
        db,
        engagement=engagement_with_methodology,
        playbook=osint_playbook,
        scope_subset=["foo.com", "bar.com"],
        executor=ex,
        now=datetime(2026, 7, 23, tzinfo=UTC),
    )
    entry = db.execute(
        select(CommandOutbox).where(
            CommandOutbox.engagement_id == engagement_with_methodology.id,
            CommandOutbox.idempotency_key.startswith("collection.job.completed:"),
        )
    ).scalar_one()
    envelope = json.loads(entry.encoded_payload["data"])
    assert envelope["type"] == ms.COLLECTION_JOB_COMPLETED
    assert envelope["playbook_run_id"] == str(run.id)
    assert envelope["methodology_id"] == str(
        engagement_with_methodology.methodology_id
    )
    assert envelope["asset_class"] == "domain"
    assert envelope["scope_subset"] == ["foo.com", "bar.com"]
    # node_ids across all steps, deduped + sorted.
    assert envelope["node_ids"] == [
        "osint.domain.breach",
        "osint.domain.cert",
        "osint.domain.dns",
        "osint.domain.enum",
        "osint.domain.whois",
    ]
    # 5 steps × 2 scope items × counts per step.
    assert envelope["findings_summary"] == {
        "new": 20,
        "unvalidated": 0,
        "high_severity": 10,
        "total": 30,
    }


# ---------------------------------------------------------------------------
# Partial + failure paths
# ---------------------------------------------------------------------------


def test_partial_status_when_some_steps_fail(
    db: Session, engagement_with_methodology: Engagement, osint_playbook: Playbook
) -> None:
    ex = MockExecutor(
        results={
            "whois": StepResult(ok=False, error="whois timeout"),
        },
        default=StepResult(ok=True, findings_total=1, findings_new=1),
    )
    run = start_run(
        db,
        engagement=engagement_with_methodology,
        playbook=osint_playbook,
        scope_subset=["foo.com"],
        executor=ex,
        now=datetime(2026, 7, 23, tzinfo=UTC),
    )
    assert run.status is PlaybookRunStatus.partial
    assert run.steps_succeeded == 4
    assert run.steps_failed == 1
    assert run.last_error == "whois timeout"
    # WHOIS coverage record is failed with the error note.
    whois_rec = db.execute(
        select(CoverageRecord).where(
            CoverageRecord.playbook_run_id == run.id,
            CoverageRecord.node_id == "osint.domain.whois",
        )
    ).scalar_one()
    assert whois_rec.status is CoverageRecordStatus.failed
    assert whois_rec.notes == "whois timeout"
    # Other four coverage records are satisfied.
    satisfied = db.execute(
        select(CoverageRecord).where(
            CoverageRecord.playbook_run_id == run.id,
            CoverageRecord.status == CoverageRecordStatus.satisfied,
        )
    ).scalars().all()
    assert len(satisfied) == 4


def test_all_fail_yields_failed_status(
    db: Session, engagement_with_methodology: Engagement, osint_playbook: Playbook
) -> None:
    ex = MockExecutor(default=StepResult(ok=False, error="tool down"))
    run = start_run(
        db,
        engagement=engagement_with_methodology,
        playbook=osint_playbook,
        scope_subset=["foo.com"],
        executor=ex,
        now=datetime(2026, 7, 23, tzinfo=UTC),
    )
    assert run.status is PlaybookRunStatus.failed
    assert run.steps_succeeded == 0
    assert run.steps_failed == 5
    # Milestone still emitted (B3 decides analysis).
    entry = db.execute(
        select(CommandOutbox).where(
            CommandOutbox.idempotency_key == f"collection.job.completed:{run.id}"
        )
    ).scalar_one_or_none()
    assert entry is not None


def test_executor_exception_becomes_step_failure(
    db: Session, engagement_with_methodology: Engagement, osint_playbook: Playbook
) -> None:
    """A thrown tool must NOT crash the run — the runner converts it to
    ``StepResult(ok=False)`` and continues."""
    ex = MockExecutor(
        raise_for={"crtsh"},
        default=StepResult(ok=True, findings_total=1),
    )
    run = start_run(
        db,
        engagement=engagement_with_methodology,
        playbook=osint_playbook,
        scope_subset=["foo.com"],
        executor=ex,
        now=datetime(2026, 7, 23, tzinfo=UTC),
    )
    assert run.status is PlaybookRunStatus.partial
    assert run.steps_failed == 1
    assert run.last_error is not None
    assert "RuntimeError" in run.last_error
    # The other 4 steps still succeeded.
    assert run.steps_succeeded == 4


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_scope_subset_yields_failed_run(
    db: Session, engagement_with_methodology: Engagement, osint_playbook: Playbook
) -> None:
    ex = MockExecutor()
    run = start_run(
        db,
        engagement=engagement_with_methodology,
        playbook=osint_playbook,
        scope_subset=[],
        executor=ex,
        now=datetime(2026, 7, 23, tzinfo=UTC),
    )
    assert run.status is PlaybookRunStatus.failed
    assert run.last_error == "empty scope"
    assert ex.calls == []


def test_no_methodology_skips_milestone(
    db: Session, osint_playbook: Playbook
) -> None:
    """When the engagement has no methodology selected, we don't emit
    ``collection.job.completed`` — B3's milestone payload requires a
    methodology_id."""
    eng = Engagement(
        name="No methodology",
        slug=f"nom-{uuid.uuid4().hex[:8]}",
        status=EngagementStatus.active,
        work_state=EngagementWorkState.active,
    )
    db.add(eng)
    db.flush()
    ex = MockExecutor()
    run = start_run(
        db,
        engagement=eng,
        playbook=osint_playbook,
        scope_subset=["foo.com"],
        executor=ex,
        now=datetime(2026, 7, 23, tzinfo=UTC),
    )
    # Coverage records still get written — they don't require methodology_id.
    records = db.execute(
        select(CoverageRecord).where(
            CoverageRecord.playbook_run_id == run.id
        )
    ).scalars().all()
    assert records != []
    # No milestone in the outbox.
    entry = db.execute(
        select(CommandOutbox).where(
            CommandOutbox.idempotency_key == f"collection.job.completed:{run.id}"
        )
    ).scalar_one_or_none()
    assert entry is None


def test_scope_substitution_reaches_executor(
    db: Session, engagement_with_methodology: Engagement, osint_playbook: Playbook
) -> None:
    ex = MockExecutor()
    start_run(
        db,
        engagement=engagement_with_methodology,
        playbook=osint_playbook,
        scope_subset=["example.com"],
        executor=ex,
        now=datetime(2026, 7, 23, tzinfo=UTC),
    )
    # Every step should have example.com substituted into args["domain"].
    domains = {call["args"]["domain"] for call in ex.calls}
    assert domains == {"example.com"}


# ---------------------------------------------------------------------------
# End-to-end: playbook run → A2 baseline-complete flips
# ---------------------------------------------------------------------------


def test_playbook_run_flips_baseline_complete(
    db: Session, engagement_with_methodology: Engagement, osint_playbook: Playbook
) -> None:
    """The whole point: a successful playbook run against the selected
    methodology's baseline nodes flips A2's baseline-complete check."""
    ex = MockExecutor(default=StepResult(ok=True, findings_total=1))
    start_run(
        db,
        engagement=engagement_with_methodology,
        playbook=osint_playbook,
        scope_subset=["foo.com"],
        executor=ex,
        now=datetime(2026, 7, 23, tzinfo=UTC),
    )
    db.refresh(engagement_with_methodology)
    expected = meth.derive_expected_triples(
        engagement_with_methodology,
        scope_item_ids_by_asset_class={"domain": ["foo.com"]},
    )
    is_complete, missing = cov.check_baseline_complete(
        db,
        engagement_id=engagement_with_methodology.id,
        expected=expected,
    )
    assert is_complete is True
    assert missing == []


# ---------------------------------------------------------------------------
# Catalog seed loader
# ---------------------------------------------------------------------------


def test_seed_playbooks_load_idempotently(db: Session) -> None:
    first = load_seed_playbooks(db)
    second = load_seed_playbooks(db)
    assert [p.id for p in first] == [p.id for p in second]


def test_step_result_is_immutable_frozen_dataclass() -> None:
    """``StepResult`` is used as the executor's outward-facing type; keep it
    frozen so tests + executor authors don't mutate returned results."""
    from dataclasses import FrozenInstanceError

    r = StepResult(ok=True, findings_total=3)
    with pytest.raises(FrozenInstanceError):
        r.findings_total = 5  # type: ignore[misc]
    # replace() still works (immutable update).
    r2 = replace(r, findings_total=5)
    assert r2.findings_total == 5
