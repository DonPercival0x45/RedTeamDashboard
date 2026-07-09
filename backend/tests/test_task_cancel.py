"""Task cancellation endpoint tests."""
from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
import redis as redis_lib
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.main import app
from app.models import (
    AuditLog,
    Engagement,
    EngagementStatus,
    MCPLease,
    MCPLeaseStatus,
    OwnerEligibility,
    Task,
    TaskKind,
    TaskStatus,
)
from app.runs.events import encode_command
from app.runs.streams import inbound_stream

HDR = {"X-User-Id": "task-cancel@example.com"}


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
        name="Task Cancel",
        slug=f"task-cancel-{uuid.uuid4().hex[:8]}",
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


def test_cancel_task_marks_cancelled_removes_queue_and_releases_lease(
    client: TestClient,
    db: Session,
    redis_client,
    engagement: Engagement,
) -> None:
    run_id = uuid.uuid4()
    task = Task(
        engagement_id=engagement.id,
        title="Port scan",
        kind=TaskKind.enum,
        owner_eligibility=OwnerEligibility.agent,
        status=TaskStatus.dispatched,
        payload={"tool": "portscan", "target": "203.0.113.10"},
        run_id=run_id,
        dispatched_at=datetime.now(tz=UTC),
    )
    db.add(task)
    db.flush()
    lease = MCPLease(
        task_id=task.id,
        engagement_id=engagement.id,
        allowed_tools=["portscan"],
        context={},
        prompt_keys=[],
        status=MCPLeaseStatus.active.value,
        expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
        created_at=datetime.now(tz=UTC),
    )
    db.add(lease)
    db.commit()
    db.refresh(task)

    stream = inbound_stream(engagement.id)
    redis_client.xadd(
        stream,
        encode_command(
            {
                "type": "run.start",
                "thread_id": str(run_id),
                "prompt": "run",
            }
        ),
    )
    redis_client.set(f"run:model:{run_id}", json.dumps({"provider": "test"}))

    resp = client.post(f"/tasks/{task.id}/cancel", headers=HDR)

    assert resp.status_code == 200, resp.text
    assert resp.json()["raw_status"] == "cancelled"
    db.refresh(task)
    db.refresh(lease)
    assert task.status == TaskStatus.cancelled
    assert lease.status == MCPLeaseStatus.released.value
    assert redis_client.xlen(stream) == 0
    assert redis_client.get(f"run:model:{run_id}") is None

    audit = db.execute(
        select(AuditLog).where(
            AuditLog.engagement_id == engagement.id,
            AuditLog.event_type == "task.cancelled",
        )
    ).scalar_one()
    assert audit.payload["queued_commands_removed"] == 1
    assert audit.payload["leases_released"] == 1
