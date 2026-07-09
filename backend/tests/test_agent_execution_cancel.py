"""Agent-execution cancellation endpoint tests."""
from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
import redis as redis_lib
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.main import app
from app.models import (
    AgentExecution,
    AgentExecutionStatus,
    AgentName,
    AgentTrigger,
    AuditLog,
    Engagement,
    EngagementStatus,
)

HDR = {"X-User-Id": "agent-cancel@example.com"}


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def redis_client() -> Iterator[redis_lib.Redis]:
    client = redis_lib.Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        yield client
    finally:
        client.close()


@pytest.fixture()
def engagement(db: Session) -> Iterator[Engagement]:
    eng = Engagement(
        name="Agent Cancel",
        slug=f"agent-cancel-{uuid.uuid4().hex[:8]}",
        status=EngagementStatus.active,
    )
    db.add(eng)
    db.commit()
    db.refresh(eng)
    try:
        yield eng
    finally:
        db.execute(text("SELECT flush_engagement(:id)"), {"id": eng.id})
        db.commit()


def test_cancel_running_agent_execution(
    client: TestClient,
    db: Session,
    engagement: Engagement,
) -> None:
    thread_id = uuid.uuid4()
    row = AgentExecution(
        engagement_id=engagement.id,
        agent=AgentName.tactical,
        trigger=AgentTrigger.manual,
        input={"thread_id": str(thread_id), "prompt": "scan"},
        status=AgentExecutionStatus.running,
        started_at=datetime.now(tz=UTC),
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    resp = client.post(f"/agent-executions/{row.id}/cancel", headers=HDR)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["raw_status"] == "cancelled"
    db.refresh(row)
    assert row.status == AgentExecutionStatus.cancelled
    assert row.completed_at is not None
    assert row.error == "Cancelled by user"

    audit = db.execute(
        select(AuditLog).where(
            AuditLog.engagement_id == engagement.id,
            AuditLog.event_type == "agent_execution.cancelled",
        )
    ).scalar_one()
    assert audit.payload["execution_id"] == str(row.id)
    assert audit.payload["agent"] == "tactical"


def test_cancel_only_running_allowed(
    client: TestClient,
    db: Session,
    engagement: Engagement,
) -> None:
    row = AgentExecution(
        engagement_id=engagement.id,
        agent=AgentName.triage,
        trigger=AgentTrigger.manual,
        input={},
        status=AgentExecutionStatus.completed,
        started_at=datetime.now(tz=UTC),
        completed_at=datetime.now(tz=UTC),
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    resp = client.post(f"/agent-executions/{row.id}/cancel", headers=HDR)
    assert resp.status_code == 400
    assert "only running" in resp.json()["detail"].lower()


def test_cancel_removes_queued_run_start(
    client: TestClient,
    db: Session,
    redis_client: redis_lib.Redis,
    engagement: Engagement,
) -> None:
    import json

    from app.runs.events import encode_command
    from app.runs.streams import inbound_stream

    thread_id = uuid.uuid4()
    row = AgentExecution(
        engagement_id=engagement.id,
        agent=AgentName.tactical,
        trigger=AgentTrigger.manual,
        input={"thread_id": str(thread_id), "prompt": "scan"},
        status=AgentExecutionStatus.running,
        started_at=datetime.now(tz=UTC),
    )
    db.add(row)
    db.commit()

    stream = inbound_stream(engagement.id)
    redis_client.xadd(
        stream,
        encode_command(
            {
                "type": "run.start",
                "thread_id": str(thread_id),
                "prompt": "scan",
            }
        ),
    )
    redis_client.set(f"run:model:{thread_id}", json.dumps({"provider": "test"}))
    assert redis_client.xlen(stream) == 1

    resp = client.post(f"/agent-executions/{row.id}/cancel", headers=HDR)
    assert resp.status_code == 200, resp.text
    assert redis_client.xlen(stream) == 0
    assert redis_client.get(f"run:model:{thread_id}") is None
