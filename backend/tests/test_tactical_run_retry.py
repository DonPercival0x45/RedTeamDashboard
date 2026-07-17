"""Tactical run retry — a failed run dispatched from a task is re-dispatched
via its source task (TacticalAgent.dispatch), instead of being a dead end.

Only Triage run-retry was wired previously; Tactical returned 501. Now a
failed Tactical run whose source task is retryable (failed/deferred scan or
enum, agent-eligible) is re-run by delegating to the task's re-dispatch —
TacticalAgent.dispatch re-derives the prompt (the run's own prompt isn't
durable). A Tactical run with no source task 400s cleanly.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.main import app
from app.models import (
    AgentExecution,
    AgentExecutionStatus,
    AgentName,
    AgentTrigger,
    Engagement,
    EngagementStatus,
    OwnerEligibility,
    Task,
    TaskKind,
    TaskStatus,
)

HDR = {"X-User-Id": "tactical-retry@example.com"}


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def engagement(db: Session) -> Iterator[Engagement]:
    row = Engagement(
        name="Tactical Retry",
        slug=f"tactical-retry-{uuid.uuid4().hex[:8]}",
        status=EngagementStatus.active,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    try:
        yield row
    finally:
        db.execute(text("SELECT flush_engagement(:id)"), {"id": row.id})
        db.commit()


def _failed_tactical_run(db: Session, engagement: Engagement) -> AgentExecution:
    execution = AgentExecution(
        engagement_id=engagement.id,
        agent=AgentName.tactical,
        trigger=AgentTrigger.manual,
        status=AgentExecutionStatus.failed,
        started_at=datetime.now(tz=UTC),
    )
    db.add(execution)
    db.commit()
    db.refresh(execution)
    return execution


def test_retry_failed_tactical_run_redispatches_source_task(
    client: TestClient,
    db: Session,
    engagement: Engagement,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.agents import TacticalAgent

    execution = _failed_tactical_run(db, engagement)
    task = Task(
        engagement_id=engagement.id,
        title="Port scan 203.0.113.10",
        kind=TaskKind.scan,
        owner_eligibility=OwnerEligibility.agent,
        status=TaskStatus.failed,
        payload={"tool": "portscan", "target": "203.0.113.10"},
        run_id=execution.id,
    )
    db.add(task)
    db.commit()

    dispatched: list[uuid.UUID] = []

    def fake_dispatch(
        _self: TacticalAgent,
        _session: Session,
        *,
        task: Task,
        acting_user_id: uuid.UUID,  # noqa: ARG001
        trigger: object,  # noqa: ARG001
    ) -> None:
        dispatched.append(task.id)

    monkeypatch.setattr(TacticalAgent, "dispatch", fake_dispatch)

    response = client.post(f"/agent-executions/{execution.id}/retry", headers=HDR)

    assert response.status_code == 200
    # TacticalAgent.dispatch was called with the source task → the run is re-dispatched.
    assert dispatched == [task.id]
    # The task left its failed terminal state (reset to pending for dispatch).
    db.refresh(task)
    assert task.status == TaskStatus.pending


def test_retry_failed_tactical_run_without_source_task_is_400(
    client: TestClient,
    db: Session,
    engagement: Engagement,
) -> None:
    execution = _failed_tactical_run(db, engagement)
    # No Task with run_id == execution.id → can't re-derive a run.
    response = client.post(f"/agent-executions/{execution.id}/retry", headers=HDR)
    assert response.status_code == 400
    assert "wasn't dispatched from a task" in response.json()["detail"]
