"""Tactical dispatch run-level dedup.

A (tool, target) a completed run already covered within the dedup window is
NOT re-dispatched — the guardrail against "the same stuff over and over" the
5q-partners dump showed (dns_lookup secrets.5qpartners.com dispatched twice
8s apart). dispatch raises TacticalAlreadyScanned so callers mark the task
done against the prior run.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.agents import TacticalAgent, TacticalAlreadyScanned
from app.models import (
    AgentExecution,
    AgentExecutionStatus,
    AgentName,
    AgentTrigger,
    Engagement,
    EngagementStatus,
    EngagementWorkState,
    OwnerEligibility,
    Task,
    TaskKind,
    TaskStatus,
)


@pytest.fixture()
def engagement(db: Session):
    row = Engagement(
        name="TacDedup",
        slug=f"tacdedup-{uuid.uuid4().hex[:8]}",
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


def _prior_run(
    db: Session, engagement: Engagement, tool: str = "dns_lookup", target: str = "cwa.example"
) -> AgentExecution:
    run = AgentExecution(
        engagement_id=engagement.id,
        agent=AgentName.tactical,
        trigger=AgentTrigger.manual,
        status=AgentExecutionStatus.completed,
        input={"task_id": str(uuid.uuid4()), "tool": tool, "target": target},
        output={"thread_id": str(uuid.uuid4()), "prompt": "..."},
        started_at=datetime.now(tz=UTC),
        completed_at=datetime.now(tz=UTC),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def _task(
    db: Session, engagement: Engagement, tool: str = "dns_lookup", target: str = "cwa.example"
) -> Task:
    t = Task(
        engagement_id=engagement.id,
        title=f"Resolve {target}",
        kind=TaskKind.enum,
        owner_eligibility=OwnerEligibility.either,
        status=TaskStatus.pending,
        payload={"tool": tool, "target": target, "task_kind": "enum"},
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def test_dispatch_refuses_recently_scanned_target(db: Session, engagement: Engagement) -> None:
    prior = _prior_run(db, engagement, "dns_lookup", "cwa.example")
    task = _task(db, engagement, "dns_lookup", "cwa.example")

    agent = TacticalAgent(redis_client=None)
    with pytest.raises(TacticalAlreadyScanned) as ei:
        agent.dispatch(db, task=task, acting_user_id=uuid.uuid4())

    assert ei.value.prior_execution_id == prior.id


def test_dispatch_allows_distinct_target(db: Session, engagement: Engagement) -> None:
    """A completed run for cwa does NOT block dispatching a different target."""
    _prior_run(db, engagement, "dns_lookup", "cwa.example")
    task = _task(db, engagement, "dns_lookup", "other.example")

    agent = TacticalAgent(redis_client=None)
    # Guard does not fire — dispatch proceeds past it (then fails later on
    # lease/redis, but NOT with TacticalAlreadyScanned).
    with pytest.raises(Exception) as ei:  # noqa: PT011 — any later failure is fine
        agent.dispatch(db, task=task, acting_user_id=uuid.uuid4())
    assert not isinstance(ei.value, TacticalAlreadyScanned)


def test_dispatch_allows_old_run_outside_window(db: Session, engagement: Engagement) -> None:
    """A completed run older than the 24h window does NOT block re-dispatch."""
    old = datetime.now(tz=UTC) - timedelta(hours=25)
    run = AgentExecution(
        engagement_id=engagement.id,
        agent=AgentName.tactical,
        trigger=AgentTrigger.manual,
        status=AgentExecutionStatus.completed,
        input={"tool": "dns_lookup", "target": "cwa.example"},
        output={"thread_id": str(uuid.uuid4())},
        started_at=old,
        completed_at=old,
    )
    db.add(run)
    db.commit()
    task = _task(db, engagement, "dns_lookup", "cwa.example")

    agent = TacticalAgent(redis_client=None)
    with pytest.raises(Exception) as ei:  # noqa: PT011
        agent.dispatch(db, task=task, acting_user_id=uuid.uuid4())
    assert not isinstance(ei.value, TacticalAlreadyScanned)
