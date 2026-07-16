"""Seed-provenance for strategy workspace reset (is_bootstrap flag).

The reset endpoint must delete only starter rows created by the
accepted-initial-strategy bootstrap, identified by ``is_bootstrap`` rather
than magic title/rationale/reason strings. These tests prove:
  - reset still deletes seeded rows after an analyst edits a seeded row's
    title/rationale (so the old string-match would have orphaned it);
  - an analyst-created row that happens to share a seed rationale SURVIVES
    reset (so the old string-match would have wrongly deleted it);
  - reset is a no-op on the workspace of a never-bootstrapped engagement.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.main import app
from app.models import (
    AgentName,
    AuditLog,
    CoverageItem,
    Engagement,
    EngagementObjective,
    EngagementStatus,
    EngagementWorkState,
    ObjectivePriority,
    ObjectiveStatus,
    Suggestion,
    SuggestionKind,
    SuggestionStatus,
    User,
    UserRole,
    WorkItem,
    WorkItemExecutor,
    WorkItemPriority,
    WorkItemStatus,
)
from app.services.suggestion_router import _bootstrap_workspace_from_initial_strategy

HDR = {"X-User-Id": "strategy-reset@example.com"}


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def engagement(db: Session) -> Iterator[Engagement]:
    row = Engagement(
        name="Strategy Reset",
        slug=f"strategy-reset-{uuid.uuid4().hex[:8]}",
        status=EngagementStatus.active,
        work_state=EngagementWorkState.active,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    try:
        yield row
    finally:
        db.execute(text("SELECT flush_engagement(:id)"), {"id": row.id})
        db.commit()


def _bootstrap(db: Session, engagement: Engagement) -> dict[str, int]:
    """Run the initial-strategy bootstrap directly and commit."""
    user = User(email=f"seeder-{uuid.uuid4().hex[:8]}@example.com", role=UserRole.user)
    db.add(user)
    db.flush()
    suggestion = Suggestion(
        engagement_id=engagement.id,
        title="Initial strategy",
        kind=SuggestionKind.strategy_revision,
        status=SuggestionStatus.open,
        created_by_agent=AgentName.strategic,
        payload={},
    )
    db.add(suggestion)
    db.flush()
    counts = _bootstrap_workspace_from_initial_strategy(db, suggestion, user_id=user.id)
    db.commit()
    return counts


def test_reset_deletes_seeded_rows_even_after_analyst_edits(
    client: TestClient, db: Session, engagement: Engagement
) -> None:
    counts = _bootstrap(db, engagement)
    assert counts["objectives"] == 3
    seeded_objective = db.execute(
        select(EngagementObjective).where(
            EngagementObjective.engagement_id == engagement.id,
            EngagementObjective.is_bootstrap.is_(True),
        )
    ).scalars().first()
    assert seeded_objective is not None
    seeded_work = db.execute(
        select(WorkItem).where(
            WorkItem.engagement_id == engagement.id,
            WorkItem.is_bootstrap.is_(True),
        )
    ).scalars().first()
    assert seeded_work is not None

    # Analyst renames the seeded objective and rewrites a seeded work item's
    # rationale. Under the old magic-string match these edits would orphan the
    # rows (the strings no longer match); under is_bootstrap they still delete.
    seeded_objective.title = "Analyst-renamed objective"
    seeded_work.rationale = "Analyst rewrote the rationale entirely"
    db.commit()

    response = client.post(
        f"/engagements/{engagement.slug}/strategy/reset", headers=HDR
    )
    assert response.status_code == 204, response.text

    db.expire_all()
    assert (
        db.execute(
            select(EngagementObjective).where(
                EngagementObjective.engagement_id == engagement.id
            )
        ).scalars().all()
        == []
    )
    assert (
        db.execute(
            select(WorkItem).where(WorkItem.engagement_id == engagement.id)
        ).scalars().all()
        == []
    )
    assert (
        db.execute(
            select(CoverageItem).where(CoverageItem.engagement_id == engagement.id)
        ).scalars().all()
        == []
    )
    assert (
        db.execute(
            select(AuditLog).where(
                AuditLog.engagement_id == engagement.id,
                AuditLog.event_type == "strategy.reset",
            )
        ).scalars().first()
        is not None
    )


def test_analyst_created_row_with_seed_text_survives_reset(
    client: TestClient, db: Session, engagement: Engagement
) -> None:
    _bootstrap(db, engagement)
    # An analyst-created work item that happens to reuse the exact seed
    # rationale string must NOT be deleted by reset (is_bootstrap is False).
    analyst_work = WorkItem(
        engagement_id=engagement.id,
        title="Analyst's own follow-up",
        rationale="Seeded from the accepted initial strategy.",
        acceptance_criteria=[],
        status=WorkItemStatus.ready,
        priority=WorkItemPriority.medium,
        executor_type=WorkItemExecutor.analyst,
    )
    db.add(analyst_work)
    db.commit()
    db.refresh(analyst_work)

    response = client.post(
        f"/engagements/{engagement.slug}/strategy/reset", headers=HDR
    )
    assert response.status_code == 204, response.text

    db.expire_all()
    survivor = db.get(WorkItem, analyst_work.id)
    assert survivor is not None
    assert survivor.title == "Analyst's own follow-up"
    # Every bootstrap-seeded row is gone; only the analyst row remains.
    remaining = db.execute(
        select(WorkItem).where(WorkItem.engagement_id == engagement.id)
    ).scalars().all()
    assert [row.id for row in remaining] == [analyst_work.id]


def test_reset_is_noop_on_non_bootstrapped_engagement(
    client: TestClient, db: Session, engagement: Engagement
) -> None:
    # No bootstrap. Analyst-created objective + work must survive reset.
    objective = EngagementObjective(
        engagement_id=engagement.id,
        title="Validate highest-risk findings",  # exact seed title, but not seeded
        status=ObjectiveStatus.active,
        priority=ObjectivePriority.critical,
        display_order=10,
    )
    work = WorkItem(
        engagement_id=engagement.id,
        title="Analyst task",
        rationale="Seeded from the accepted initial strategy.",  # exact seed text
        acceptance_criteria=[],
        status=WorkItemStatus.ready,
        priority=WorkItemPriority.medium,
        executor_type=WorkItemExecutor.analyst,
    )
    db.add_all([objective, work])
    db.commit()
    db.refresh(objective)
    db.refresh(work)

    response = client.post(
        f"/engagements/{engagement.slug}/strategy/reset", headers=HDR
    )
    assert response.status_code == 204, response.text

    db.expire_all()
    assert db.get(EngagementObjective, objective.id) is not None
    assert db.get(WorkItem, work.id) is not None
