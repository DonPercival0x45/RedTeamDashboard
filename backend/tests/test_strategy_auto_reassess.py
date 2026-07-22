"""Durable auto-reassess events emitted when work items resolve."""
from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.agents import StrategicAgent
from app.db.session import SessionLocal
from app.models import (
    AgentExecution,
    AgentExecutionStatus,
    AgentName,
    AgentTrigger,
    CommandOutbox,
    CommandOutboxStatus,
    Engagement,
    EngagementStatus,
    ProcessingReceipt,
    ProcessingReceiptStatus,
    User,
    UserRole,
    WorkItem,
    WorkItemResolution,
    WorkItemStatus,
)
from app.runs.streams import outbound_stream
from app.services.command_outbox import publish_entry
from app.services.engagement_strategist import acquire_auto_reassess_cooldown, stage_auto_reassess
from app.worker.strategic_consumer import StrategicConsumer


@pytest.fixture()
def engagement(db: Session) -> Iterator[Engagement]:
    row = Engagement(
        name="Durable auto reassess",
        slug=f"durable-auto-reassess-{uuid.uuid4().hex[:8]}",
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


class FakeRedis:
    def __init__(self, *, fail_xadd: bool = False) -> None:
        self.store: dict[str, str] = {}
        self.fail_xadd = fail_xadd
        self.added: list[tuple[str, dict[str, str]]] = []
        self.acked: list[tuple[str, str, str]] = []

    def get(self, key: str) -> str | None:
        return self.store.get(key)

    def set(
        self,
        key: str,
        value: str,
        *,
        nx: bool = False,
        ex: int | None = None,
    ) -> bool:
        del ex
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True

    def eval(self, _script: str, _keys: int, key: str, token: str) -> int:
        if self.store.get(key) != token:
            return 0
        del self.store[key]
        return 1

    def xadd(self, stream: str, fields: dict[str, str]) -> str:
        if self.fail_xadd:
            raise ConnectionError("injected redis outage")
        self.added.append((stream, fields))
        return f"{len(self.added)}-0"

    def xack(self, stream: str, group: str, message_id: str) -> int:
        self.acked.append((stream, group, message_id))
        return 1

    def xpending_range(self, *_args: Any, **_kwargs: Any) -> list[dict[str, int]]:
        return [{"times_delivered": 1}]


def _event_fields(
    *, event_id: str, engagement_id: uuid.UUID, acting_user_id: uuid.UUID
) -> dict[str, str]:
    return {
        "data": json.dumps(
            {
                "type": "strategy.reassess.requested",
                "event_id": event_id,
                "engagement_id": str(engagement_id),
                "work_item_id": str(uuid.uuid4()),
                "acting_user_id": str(acting_user_id),
            }
        )
    }


def _consumer(redis_client: FakeRedis) -> StrategicConsumer:
    return StrategicConsumer(
        agent=StrategicAgent(redis_client=redis_client),
        redis_client=redis_client,
        session_factory=SessionLocal,
    )


def test_resolution_commits_event_when_immediate_redis_publish_fails(
    db: Session, engagement: Engagement
) -> None:
    """Resolution stages the durable event; post-commit Redis failure leaves
    a relayable pending row but does not break the resolution transaction."""
    user = User(
        email=f"auto-reassess-{uuid.uuid4().hex[:8]}@example.com",
        role=UserRole.user,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    work = WorkItem(
        engagement_id=engagement.id,
        title="Resolve durably",
        status=WorkItemStatus.ready,
    )
    db.add(work)
    db.commit()
    db.refresh(work)

    # Simulate the resolve endpoint body: mark completed, bump version,
    # stage the event, commit, then attempt immediate publish.
    work.status = WorkItemStatus.completed
    work.resolution_outcome = WorkItemResolution.completed
    work.completed_by_user_id = user.id
    work.row_version += 1
    event = stage_auto_reassess(
        db,
        work_item_id=work.id,
        resolution_version=work.row_version,
        engagement_id=engagement.id,
        acting_user_id=user.id,
    )
    db.commit()

    # Immediate publish with a broken Redis mirrors the API try/except path.
    broken_redis = FakeRedis(fail_xadd=True)
    try:
        publish_entry(db, broken_redis, event.id)
    except Exception:  # noqa: BLE001 - API swallows publish failures
        db.rollback()

    event_key = f"strategy.reassess:{work.id}:{work.row_version}"
    db.expire_all()
    event = db.execute(
        select(CommandOutbox).where(CommandOutbox.idempotency_key == event_key)
    ).scalar_one()
    assert event.delivery_kind == "event"
    assert event.status == CommandOutboxStatus.pending
    assert event.attempts == 1
    assert "injected redis outage" in (event.last_error or "")

    healthy_redis = FakeRedis()
    event.next_attempt_at = None
    db.commit()
    assert publish_entry(db, healthy_redis, event.id) is True
    assert healthy_redis.added[0][0] == outbound_stream(engagement.id)
    envelope = json.loads(healthy_redis.added[0][1]["data"])
    assert envelope["event_id"] == event_key
    assert envelope["acting_user_id"]


def test_consumer_rechecks_disabled_flag(
    db: Session, engagement: Engagement, monkeypatch: pytest.MonkeyPatch
) -> None:
    engagement.auto_assess_enabled = False
    db.commit()
    redis_client = FakeRedis()

    def unexpected_run(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("disabled reassess must not run")

    monkeypatch.setattr(
        "app.worker.strategic_consumer.run_engagement_strategist", unexpected_run
    )
    fields = _event_fields(
        event_id=f"strategy.reassess:{uuid.uuid4()}:2",
        engagement_id=engagement.id,
        acting_user_id=uuid.uuid4(),
    )
    _consumer(redis_client)._process_one(
        outbound_stream(engagement.id), "1-0", fields
    )

    assert redis_client.store == {}
    assert len(redis_client.acked) == 1
    receipt = db.execute(select(ProcessingReceipt)).scalars().one()
    assert receipt.status == ProcessingReceiptStatus.completed


def test_failure_releases_cooldown_and_retry_keeps_one_execution(
    db: Session, engagement: Engagement, monkeypatch: pytest.MonkeyPatch
) -> None:
    redis_client = FakeRedis()
    event_id = f"strategy.reassess:{uuid.uuid4()}:2"
    fields = _event_fields(
        event_id=event_id,
        engagement_id=engagement.id,
        acting_user_id=uuid.uuid4(),
    )
    stream = outbound_stream(engagement.id)
    calls: list[uuid.UUID] = []

    def failing_run(*_args: Any, execution_id: uuid.UUID, **_kwargs: Any) -> None:
        calls.append(execution_id)
        raise RuntimeError("injected strategist failure")

    monkeypatch.setattr(
        "app.worker.strategic_consumer.run_engagement_strategist", failing_run
    )
    consumer = _consumer(redis_client)
    consumer._process_one(stream, "2-0", fields)

    assert redis_client.store == {}
    assert redis_client.acked == []
    db.expire_all()
    receipt = db.get(ProcessingReceipt, f"event:{event_id}")
    assert receipt is not None
    assert receipt.status == ProcessingReceiptStatus.processing
    assert "injected strategist failure" in (receipt.last_error or "")
    execution_id = receipt.agent_execution_id
    assert execution_id is not None

    def successful_run(
        session: Session, *_args: Any, execution_id: uuid.UUID, **_kwargs: Any
    ) -> tuple[AgentExecution, None, str, list[Any]]:
        calls.append(execution_id)
        execution = session.get(AgentExecution, execution_id)
        if execution is None:
            execution = AgentExecution(
                id=execution_id,
                engagement_id=engagement.id,
                agent=AgentName.engagement_strategist,
                trigger=AgentTrigger.manual,
                input={"mode": "reassess"},
                model_provider="test",
                model_name="test",
                status=AgentExecutionStatus.completed,
                started_at=datetime.now(tz=UTC),
                completed_at=datetime.now(tz=UTC),
                output={},
            )
            session.add(execution)
        session.commit()
        session.refresh(execution)
        return execution, None, "context", []

    monkeypatch.setattr(
        "app.worker.strategic_consumer.run_engagement_strategist", successful_run
    )
    consumer._process_one(stream, "2-0", fields)
    # A duplicate Redis delivery with the same logical event is receipt-deduped.
    consumer._process_one(stream, "3-0", fields)

    db.expire_all()
    receipt = db.get(ProcessingReceipt, f"event:{event_id}")
    assert receipt is not None
    assert receipt.status == ProcessingReceiptStatus.completed
    assert calls == [execution_id, execution_id]
    assert db.get(AgentExecution, execution_id) is not None
    assert len(redis_client.acked) == 2
    assert redis_client.store[f"auto-reassess:{engagement.id}"] == str(execution_id)


def test_cooldown_same_event_can_resume_but_distinct_event_is_suppressed() -> None:
    redis_client = FakeRedis()
    engagement_id = uuid.uuid4()
    owner = str(uuid.uuid4())

    assert (
        acquire_auto_reassess_cooldown(
            redis_client, engagement_id, owner_token=owner
        )
        == owner
    )
    assert (
        acquire_auto_reassess_cooldown(
            redis_client, engagement_id, owner_token=owner
        )
        == owner
    )
    later_owner = str(uuid.uuid4())
    assert (
        acquire_auto_reassess_cooldown(
            redis_client, engagement_id, owner_token=later_owner
        )
        is None
    )
    # Simulate TTL expiry: a later resolution owns a distinct event and can run.
    redis_client.store.clear()
    assert (
        acquire_auto_reassess_cooldown(
            redis_client, engagement_id, owner_token=later_owner
        )
        == later_owner
    )
