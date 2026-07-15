"""Task cancellation endpoint tests."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
import redis as redis_lib
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.agents.strategic import StrategicAgent
from app.api import status as status_api
from app.core.config import settings
from app.db.session import SessionLocal
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
from app.runs.events import decode_envelope, encode_command
from app.runs.streams import inbound_stream, run_model_key

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


def test_mutable_task_lock_uses_engagement_then_task_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_id = uuid.uuid4()
    engagement_id = uuid.uuid4()
    task = SimpleNamespace(id=task_id, engagement_id=engagement_id)
    events: list[tuple[str, bool, bool] | tuple[str, uuid.UUID]] = []

    class Result:
        def __init__(self, value: object) -> None:
            self.value = value

        def scalar_one_or_none(self) -> object:
            return self.value

    class RecordingSession:
        def __init__(self) -> None:
            self.values = iter((engagement_id, task))

        def execute(self, statement: object) -> Result:
            options = statement.get_execution_options()  # type: ignore[attr-defined]
            events.append(
                (
                    "select",
                    statement._for_update_arg is not None,  # type: ignore[attr-defined]
                    options.get("populate_existing") is True,
                )
            )
            return Result(next(self.values))

    monkeypatch.setattr(
        status_api,
        "_ensure_mutable_engagement",
        lambda _session, selected_id: events.append(("engagement", selected_id)),
    )

    locked = status_api._lock_mutable_task(RecordingSession(), task_id)  # type: ignore[arg-type]

    assert locked is task
    assert events == [
        ("select", False, False),
        ("engagement", engagement_id),
        ("select", True, True),
    ]


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


def test_cancel_deferred_task_resolves_it(
    client: TestClient,
    db: Session,
    engagement: Engagement,
) -> None:
    task = Task(
        engagement_id=engagement.id,
        title="Deferred enumeration",
        kind=TaskKind.enum,
        owner_eligibility=OwnerEligibility.agent,
        status=TaskStatus.deferred,
        payload={"tool": "portscan", "target": "203.0.113.11"},
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    response = client.post(f"/tasks/{task.id}/cancel", headers=HDR)

    assert response.status_code == 200, response.text
    assert response.json()["raw_status"] == "cancelled"
    assert response.json()["outcome"] is None
    assert response.json()["synopsis"].startswith("Cancelled by analyst")
    db.refresh(task)
    assert task.status == TaskStatus.cancelled
    assert task.completed_at is not None
    audit = db.execute(
        select(AuditLog).where(
            AuditLog.engagement_id == engagement.id,
            AuditLog.event_type == "task.cancelled",
        )
    ).scalar_one()
    assert audit.payload["previous_status"] == "deferred"


@pytest.mark.parametrize(
    "initial_status",
    [TaskStatus.failed, TaskStatus.deferred],
)
def test_retry_task_redispatches_worker_command(
    client: TestClient,
    db: Session,
    redis_client,
    engagement: Engagement,
    initial_status: TaskStatus,
) -> None:
    task = Task(
        engagement_id=engagement.id,
        title="Retry enumeration",
        kind=TaskKind.enum,
        owner_eligibility=OwnerEligibility.agent,
        status=initial_status,
        payload={"tool": "portscan", "target": "203.0.113.12"},
        run_id=uuid.uuid4(),
        completed_at=datetime.now(tz=UTC),
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    previous_run_id = task.run_id
    assert previous_run_id is not None
    stream = inbound_stream(engagement.id)
    redis_client.delete(stream)
    redis_client.hset(
        run_model_key(previous_run_id),
        mapping={"provider": "test", "name": "old-model"},
    )

    try:
        response = client.post(f"/tasks/{task.id}/retry", headers=HDR)

        assert response.status_code == 200, response.text
        assert response.json()["raw_status"] == "dispatched"
        db.refresh(task)
        assert task.status == TaskStatus.dispatched
        assert task.run_id is not None
        assert task.run_id != previous_run_id
        assert task.dispatched_at is not None
        assert task.completed_at is None
        assert not redis_client.exists(run_model_key(previous_run_id))

        queued = redis_client.xrange(stream)
        assert len(queued) == 1
        envelope = decode_envelope(queued[0][1])
        assert envelope["type"] == "run.start"
        assert envelope["thread_id"] == str(task.run_id)
        assert envelope["acting_user_id"]

        audit = db.execute(
            select(AuditLog).where(
                AuditLog.engagement_id == engagement.id,
                AuditLog.event_type == "task.retried",
            )
        ).scalar_one()
        assert audit.payload["previous_status"] == initial_status.value
        assert audit.payload["run_id"] == str(task.run_id)
        assert audit.payload["old_queued_commands_removed"] == 0
    finally:
        redis_client.delete(stream)
        if task.run_id:
            redis_client.delete(run_model_key(task.run_id))


def test_retry_rejects_archived_engagement_without_queueing(
    client: TestClient,
    db: Session,
    redis_client,
    engagement: Engagement,
) -> None:
    engagement.status = EngagementStatus.archived
    task = Task(
        engagement_id=engagement.id,
        title="Archived retry",
        kind=TaskKind.enum,
        owner_eligibility=OwnerEligibility.agent,
        status=TaskStatus.failed,
        payload={"tool": "portscan", "target": "203.0.113.13"},
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    stream = inbound_stream(engagement.id)
    redis_client.delete(stream)

    response = client.post(f"/tasks/{task.id}/retry", headers=HDR)

    assert response.status_code == 409, response.text
    db.refresh(task)
    assert task.status == TaskStatus.failed
    assert redis_client.xlen(stream) == 0


def test_retry_rejects_cancelled_task_without_queueing(
    client: TestClient,
    db: Session,
    redis_client,
    engagement: Engagement,
) -> None:
    task = Task(
        engagement_id=engagement.id,
        title="Cancelled retry",
        kind=TaskKind.enum,
        owner_eligibility=OwnerEligibility.agent,
        status=TaskStatus.cancelled,
        payload={"tool": "portscan", "target": "203.0.113.15"},
        completed_at=datetime.now(tz=UTC),
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    stream = inbound_stream(engagement.id)
    redis_client.delete(stream)

    response = client.post(f"/tasks/{task.id}/retry", headers=HDR)

    assert response.status_code == 400, response.text
    db.refresh(task)
    assert task.status == TaskStatus.cancelled
    assert redis_client.xlen(stream) == 0


def test_cancelled_state_wins_policy_commit_race(
    client: TestClient,
    db: Session,
    redis_client,
    engagement: Engagement,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    previous_run_id = uuid.uuid4()
    task = Task(
        engagement_id=engagement.id,
        title="Deferred race",
        kind=TaskKind.enum,
        owner_eligibility=OwnerEligibility.agent,
        status=TaskStatus.deferred,
        payload={"tool": "portscan", "target": "203.0.113.16"},
        run_id=previous_run_id,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    stream = inbound_stream(engagement.id)
    redis_client.delete(stream)
    run_model_keys_before = set(redis_client.keys("run:model:*"))
    original_provision = StrategicAgent.provision_lease

    def cancel_during_policy_commit(
        self,
        session,
        *,
        task,
        acting_user_id,
        **_kwargs,
    ):
        # Mirrors Strategic's visibility commit, which releases the row lock.
        session.commit()
        with SessionLocal() as concurrent:
            current = concurrent.get(Task, task.id)
            assert current is not None
            assert current.status == TaskStatus.pending
            current.status = TaskStatus.cancelled
            current.completed_at = datetime.now(tz=UTC)
            concurrent.commit()
        return original_provision(
            self,
            session,
            task=task,
            acting_user_id=acting_user_id,
            requires_container=False,
        )

    monkeypatch.setattr(
        StrategicAgent,
        "provision_lease",
        cancel_during_policy_commit,
    )

    response = client.post(f"/tasks/{task.id}/retry", headers=HDR)

    assert response.status_code == 409, response.text
    db.expire_all()
    cancelled = db.get(Task, task.id)
    assert cancelled is not None
    assert cancelled.status == TaskStatus.cancelled
    assert cancelled.run_id == previous_run_id
    assert redis_client.xlen(stream) == 0
    assert set(redis_client.keys("run:model:*")) == run_model_keys_before
    audit = db.execute(
        select(AuditLog).where(
            AuditLog.engagement_id == engagement.id,
            AuditLog.event_type == "task.retry_superseded",
        )
    ).scalar_one()
    assert audit.payload["winning_status"] == "cancelled"


def test_retry_enqueue_failure_restores_previous_state_and_audits(
    client: TestClient,
    db: Session,
    redis_client,
    engagement: Engagement,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    previous_run_id = uuid.uuid4()
    previous_completed_at = datetime.now(tz=UTC)
    task = Task(
        engagement_id=engagement.id,
        title="Failed retry",
        kind=TaskKind.enum,
        owner_eligibility=OwnerEligibility.agent,
        status=TaskStatus.failed,
        payload={"tool": "portscan", "target": "203.0.113.14"},
        run_id=previous_run_id,
        completed_at=previous_completed_at,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    failed_run_id = uuid.uuid4()

    def fail_after_dispatch_commit(self, session, *, task, **_kwargs) -> None:
        task.status = TaskStatus.dispatched
        task.run_id = failed_run_id
        task.dispatched_at = datetime.now(tz=UTC)
        session.add(
            MCPLease(
                task_id=task.id,
                engagement_id=task.engagement_id,
                allowed_tools=["portscan"],
                context={},
                prompt_keys=[],
                status=MCPLeaseStatus.active.value,
                expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
                created_at=datetime.now(tz=UTC),
            )
        )
        self._redis.hset(
            run_model_key(failed_run_id),
            mapping={"provider": "test", "name": "failed-model"},
        )
        session.commit()
        raise ConnectionError("redis unavailable")

    from app.api import status as status_api

    monkeypatch.setattr(
        status_api.TacticalAgent,
        "dispatch",
        fail_after_dispatch_commit,
    )

    response = client.post(f"/tasks/{task.id}/retry", headers=HDR)

    assert response.status_code == 502, response.text
    db.expire_all()
    restored = db.get(Task, task.id)
    assert restored is not None
    assert restored.status == TaskStatus.failed
    assert restored.run_id == previous_run_id
    assert restored.completed_at == previous_completed_at
    lease = db.execute(select(MCPLease).where(MCPLease.task_id == task.id)).scalar_one()
    assert lease.status == MCPLeaseStatus.released.value
    assert not redis_client.exists(run_model_key(failed_run_id))
    audit = db.execute(
        select(AuditLog).where(
            AuditLog.engagement_id == engagement.id,
            AuditLog.event_type == "task.retry_failed",
        )
    ).scalar_one()
    assert audit.payload["failed_run_id"] == str(failed_run_id)
    assert "redis unavailable" in audit.payload["error"]
