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
from app.models import (
    AgentExecutionStatus,
    AgentTrigger,
    CommandOutbox,
    Engagement,
    EngagementStatus,
    EngagementWorkState,
    Finding,
    FindingPhase,
    FindingStatus,
    Severity,
    Suggestion,
    User,
    UserRole,
)
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
