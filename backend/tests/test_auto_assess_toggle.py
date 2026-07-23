"""auto_assess_enabled — token-saving kill-switch for background generation.

When an engagement has auto-assess disabled, the strategic watcher (finding
trigger) skips the LLM run entirely (no suggestions, no tokens), and
auto-reassess on work-item resolve is a no-op. The manual Analyze button is
unaffected.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.agents import StrategicAgent
from app.api.strategy import resolve_work_item
from app.core.config import settings
from app.models import (
    AgentExecutionStatus,
    AgentTrigger,
    CommandOutbox,
    Engagement,
    EngagementArchitecture,
    EngagementStatus,
    EngagementWorkState,
    Finding,
    FindingPhase,
    FindingStatus,
    Severity,
    Suggestion,
    User,
    UserRole,
    WorkItem,
    WorkItemExecutor,
    WorkItemPriority,
    WorkItemResolution,
    WorkItemStatus,
)
from app.schemas.strategy import WorkItemResolve
from app.services.engagement_strategist import stage_auto_reassess


@pytest.fixture()
def engagement(db: Session):
    row = Engagement(
        name="AutoAssess",
        slug=f"aa-{uuid.uuid4().hex[:8]}",
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


def _user(db: Session) -> User:
    row = User(email=f"aa-{uuid.uuid4().hex[:8]}@example.com", role=UserRole.user)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _finding(db: Session, engagement: Engagement) -> Finding:
    f = Finding(
        engagement_id=engagement.id,
        title="f",
        severity=Severity.info,
        status=FindingStatus.validated,
        phase=FindingPhase.osint,
    )
    db.add(f)
    db.commit()
    db.refresh(f)
    return f


def test_watcher_skipped_when_auto_assess_disabled(
    db: Session, engagement: Engagement
) -> None:
    """finding-trigger analyze_finding with auto-assess off -> no LLM, no suggestions."""
    user = _user(db)
    engagement.auto_assess_enabled = False
    db.commit()
    finding = _finding(db, engagement)

    agent = StrategicAgent(redis_client=None)
    execution, suggestions = agent.analyze_finding(
        db,
        finding=finding,
        trigger=AgentTrigger.finding,
        acting_user_id=user.id,
    )
    db.commit()

    assert execution.status == AgentExecutionStatus.cancelled
    assert suggestions == []
    # No suggestions persisted.
    assert (
        db.execute(
            select(Suggestion).where(Suggestion.engagement_id == engagement.id)
        ).scalars().all()
        == []
    )


def test_disabled_auto_reassess_is_still_durably_staged(
    db: Session, engagement: Engagement
) -> None:
    """The consumer, not the producer, applies the execution-time kill switch."""
    engagement.auto_assess_enabled = False
    db.commit()
    user = _user(db)

    event = stage_auto_reassess(
        db,
        work_item_id=uuid.uuid4(),
        resolution_version=2,
        engagement_id=engagement.id,
        acting_user_id=user.id,
    )
    db.commit()

    assert db.get(CommandOutbox, event.id) is not None


def test_v3_resolution_commits_without_staging_auto_reassess(
    db: Session,
    engagement: Engagement,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "v3_intelligence_enabled", True)
    engagement.intelligence_architecture = EngagementArchitecture.v3
    db.commit()
    user = _user(db)
    work = WorkItem(
        engagement_id=engagement.id,
        title="Resolve under v3",
        status=WorkItemStatus.ready,
        priority=WorkItemPriority.medium,
        executor_type=WorkItemExecutor.analyst,
    )
    db.add(work)
    db.commit()
    db.refresh(work)

    def unexpected_stage(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("v3 resolution must not stage auto-reassess")

    monkeypatch.setattr("app.api.strategy.stage_auto_reassess", unexpected_stage)
    result = resolve_work_item(
        work.id,
        WorkItemResolve(
            expected_row_version=work.row_version,
            outcome=WorkItemResolution.completed,
            note="done",
        ),
        db,
        user,
        object(),  # type: ignore[arg-type] — v3 path never touches Redis
    )

    assert result.status == WorkItemStatus.completed
    db.expire_all()
    persisted = db.get(WorkItem, work.id)
    assert persisted is not None
    assert persisted.status == WorkItemStatus.completed
    assert db.execute(
        select(CommandOutbox).where(CommandOutbox.engagement_id == engagement.id)
    ).scalars().all() == []
