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
from app.core.config import settings
from app.db.session import SessionLocal
from app.models import (
    AgentExecution,
    AgentExecutionStatus,
    AgentName,
    AgentPromptMode,
    AgentTrigger,
    CommandOutbox,
    CommandOutboxStatus,
    Engagement,
    EngagementArchitecture,
    EngagementStatus,
    ProcessingReceipt,
    ProcessingReceiptStatus,
    User,
    UserRole,
    WorkItem,
    WorkItemExecutor,
    WorkItemPriority,
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


def _enable_v3(db: Session, engagement: Engagement) -> None:
    engagement.intelligence_architecture = EngagementArchitecture.v3
    db.commit()


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
        priority=WorkItemPriority.medium,
        executor_type=WorkItemExecutor.analyst,
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


def test_global_cutover_preserves_legacy_engagement_finding_path(
    engagement: Engagement, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "v3_intelligence_enabled", True)
    redis_client = FakeRedis()
    consumer = _consumer(redis_client)
    calls: list[uuid.UUID] = []
    finding_id = uuid.uuid4()

    def record(
        _session: Session,
        seen_finding_id: uuid.UUID,
        **_kwargs: Any,
    ) -> None:
        calls.append(seen_finding_id)

    monkeypatch.setattr(consumer, "_analyze", record)
    consumer._process_one(
        outbound_stream(engagement.id),
        "legacy-1",
        {
            "data": json.dumps(
                {
                    "type": "finding.created",
                    "event_id": str(uuid.uuid4()),
                    "finding_id": str(finding_id),
                    "acting_user_id": str(uuid.uuid4()),
                }
            )
        },
    )

    assert calls == [finding_id]
    assert len(redis_client.acked) == 1


def test_v3_engagement_does_not_fall_back_when_global_automation_is_off(
    db: Session, engagement: Engagement, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "v3_intelligence_enabled", False)
    _enable_v3(db, engagement)
    redis_client = FakeRedis()
    consumer = _consumer(redis_client)

    def unexpected_legacy_run(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("v3 engagement must never fall back to v1 intelligence")

    monkeypatch.setattr(consumer, "_analyze", unexpected_legacy_run)
    consumer._process_one(
        outbound_stream(engagement.id),
        "v3-disabled-1",
        {
            "data": json.dumps(
                {
                    "type": "finding.created",
                    "event_id": str(uuid.uuid4()),
                    "finding_id": str(uuid.uuid4()),
                    "acting_user_id": str(uuid.uuid4()),
                }
            )
        },
    )

    assert len(redis_client.acked) == 1


def test_read_only_v3_engagement_skips_queued_milestone(
    db: Session, engagement: Engagement, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "v3_intelligence_enabled", True)
    _enable_v3(db, engagement)
    engagement.status = EngagementStatus.archived
    db.commit()
    redis_client = FakeRedis()
    consumer = _consumer(redis_client)

    def unexpected_milestone(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("read-only engagement must not run intelligence")

    monkeypatch.setattr(consumer, "_v3_handle_milestone", unexpected_milestone)
    consumer._process_one(
        outbound_stream(engagement.id),
        "archived-1",
        {
            "data": json.dumps(
                {
                    "type": "baseline.completed",
                    "event_id": str(uuid.uuid4()),
                    "acting_user_id": str(uuid.uuid4()),
                }
            )
        },
    )

    assert len(redis_client.acked) == 1


def test_v3_cutover_skips_legacy_finding_and_reassess_events(
    db: Session, engagement: Engagement, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "v3_intelligence_enabled", True)
    _enable_v3(db, engagement)
    redis_client = FakeRedis()
    consumer = _consumer(redis_client)

    def unexpected_legacy_run(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("v1 intelligence must not run after the v3 cutover")

    monkeypatch.setattr(consumer, "_analyze", unexpected_legacy_run)
    monkeypatch.setattr(consumer, "_reassess", unexpected_legacy_run)
    stream = outbound_stream(engagement.id)
    actor_id = uuid.uuid4()
    events = [
        {
            "type": "finding.created",
            "event_id": str(uuid.uuid4()),
            "finding_id": str(uuid.uuid4()),
            "acting_user_id": str(actor_id),
        },
        {
            "type": "strategy.reassess.requested",
            "event_id": str(uuid.uuid4()),
            "work_item_id": str(uuid.uuid4()),
            "acting_user_id": str(actor_id),
        },
    ]

    for index, event in enumerate(events, start=1):
        consumer._process_one(
            stream,
            f"{index}-0",
            {"data": json.dumps(event)},
        )

    assert len(redis_client.acked) == 2
    receipts = db.execute(
        select(ProcessingReceipt).where(
            ProcessingReceipt.engagement_id == engagement.id
        )
    ).scalars().all()
    assert len(receipts) == 2
    assert all(row.status == ProcessingReceiptStatus.completed for row in receipts)


def test_v3_cutover_routes_run_completed_to_milestone_hook(
    db: Session, engagement: Engagement, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "v3_intelligence_enabled", True)
    _enable_v3(db, engagement)
    redis_client = FakeRedis()
    consumer = _consumer(redis_client)
    thread_id = uuid.uuid4()
    actor_id = uuid.uuid4()
    calls: list[tuple[uuid.UUID, uuid.UUID, uuid.UUID | None]] = []

    def record(
        _session: Session,
        *,
        engagement_id: uuid.UUID,
        thread_id: uuid.UUID,
        acting_user_id: uuid.UUID | None,
    ) -> None:
        calls.append((engagement_id, thread_id, acting_user_id))

    monkeypatch.setattr(consumer, "_v3_analyze_on_run", record)
    consumer._process_one(
        outbound_stream(engagement.id),
        "3-0",
        {
            "data": json.dumps(
                {
                    "type": "run.completed",
                    "event_id": str(uuid.uuid4()),
                    "thread_id": str(thread_id),
                    "acting_user_id": str(actor_id),
                }
            )
        },
    )

    assert calls == [(engagement.id, thread_id, actor_id)]
    assert len(redis_client.acked) == 1


@pytest.mark.parametrize(
    "event_type",
    ["collection.job.completed", "coverage.gap.opened", "baseline.completed"],
)
def test_v3_cutover_routes_engagement_milestones(
    db: Session,
    engagement: Engagement,
    monkeypatch: pytest.MonkeyPatch,
    event_type: str,
) -> None:
    monkeypatch.setattr(settings, "v3_intelligence_enabled", True)
    _enable_v3(db, engagement)
    redis_client = FakeRedis()
    consumer = _consumer(redis_client)
    actor_id = uuid.uuid4()
    playbook_run_id = uuid.uuid4()
    calls: list[dict[str, Any]] = []

    def record(_session: Session, **kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(consumer, "_v3_handle_milestone", record)
    consumer._process_one(
        outbound_stream(engagement.id),
        "milestone-1",
        {
            "data": json.dumps(
                {
                    "type": event_type,
                    "event_id": str(uuid.uuid4()),
                    "engagement_id": str(engagement.id),
                    "acting_user_id": str(actor_id),
                    **(
                        {"playbook_run_id": str(playbook_run_id)}
                        if event_type == "collection.job.completed"
                        else {}
                    ),
                }
            )
        },
    )

    assert calls == [
        {
            "engagement_id": engagement.id,
            "milestone_type": event_type,
            "acting_user_id": actor_id,
            "thread_id": (
                playbook_run_id
                if event_type == "collection.job.completed"
                else None
            ),
        }
    ]
    assert len(redis_client.acked) == 1


def test_v3_run_hook_resolves_actor_llm_and_milestone(
    db: Session, engagement: Engagement, monkeypatch: pytest.MonkeyPatch
) -> None:
    redis_client = FakeRedis()
    actor_id = uuid.uuid4()
    thread_id = uuid.uuid4()
    llm = object()
    resolved: list[dict[str, Any]] = []
    invoked: list[dict[str, Any]] = []

    monkeypatch.setattr(
        "app.worker.strategic_consumer.load_run_model",
        lambda *_args: {"acting_user_id": str(actor_id)},
    )

    def resolve_mode_llm(_session: Session, **kwargs: Any) -> tuple[Any, str, str]:
        resolved.append(kwargs)
        return llm, "test", "test-model"

    monkeypatch.setattr(
        "app.worker.strategic_consumer.resolve_llm_for_mode", resolve_mode_llm
    )

    def record_cycle(_session: Session, **kwargs: Any) -> None:
        primary_factory = kwargs.pop("llm_factory")
        review_factory = kwargs.pop("coverage_review_llm_factory")
        kwargs["primary_llm"] = primary_factory()
        kwargs["coverage_review_llm"] = review_factory()
        invoked.append(kwargs)

    monkeypatch.setattr(
        "app.worker.strategic_consumer.run_milestone_cycle", record_cycle
    )
    consumer = StrategicConsumer(
        agent=object(),  # type: ignore[arg-type]
        redis_client=redis_client,
        session_factory=SessionLocal,
    )

    consumer._v3_analyze_on_run(
        db,
        engagement_id=engagement.id,
        thread_id=thread_id,
    )

    assert resolved == [
        {
            "redis_client": redis_client,
            "user_id": actor_id,
            "engagement_id": engagement.id,
            "mode": AgentPromptMode.analysis,
        },
        {
            "redis_client": redis_client,
            "user_id": actor_id,
            "engagement_id": engagement.id,
            "mode": AgentPromptMode.coverage_review,
        },
    ]
    assert invoked == [
        {
            "engagement_id": engagement.id,
            "milestone_type": "run.completed",
            "acting_user_id": actor_id,
            "primary_llm": (llm, "test", "test-model"),
            "coverage_review_llm": (llm, "test", "test-model"),
            "thread_id": thread_id,
        }
    ]


def test_v3_run_hook_respects_auto_assess_disabled(
    db: Session, engagement: Engagement, monkeypatch: pytest.MonkeyPatch
) -> None:
    engagement.auto_assess_enabled = False
    db.flush()

    def unexpected(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("disabled engagement must not invoke milestone intelligence")

    monkeypatch.setattr(
        "app.worker.strategic_consumer.run_milestone_cycle", unexpected
    )
    consumer = StrategicConsumer(
        agent=object(),  # type: ignore[arg-type]
        redis_client=FakeRedis(),
        session_factory=SessionLocal,
    )

    consumer._v3_analyze_on_run(
        db,
        engagement_id=engagement.id,
        thread_id=uuid.uuid4(),
        acting_user_id=uuid.uuid4(),
    )


def test_v3_run_hook_failure_remains_retryable(
    db: Session, engagement: Engagement, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "v3_intelligence_enabled", True)
    _enable_v3(db, engagement)
    redis_client = FakeRedis()
    consumer = _consumer(redis_client)
    event_id = str(uuid.uuid4())

    def fail(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("injected milestone failure")

    monkeypatch.setattr(consumer, "_v3_analyze_on_run", fail)
    consumer._process_one(
        outbound_stream(engagement.id),
        "4-0",
        {
            "data": json.dumps(
                {
                    "type": "run.completed",
                    "event_id": event_id,
                    "thread_id": str(uuid.uuid4()),
                }
            )
        },
    )

    assert redis_client.acked == []
    db.expire_all()
    receipt = db.get(ProcessingReceipt, f"event:{event_id}")
    assert receipt is not None
    assert receipt.status == ProcessingReceiptStatus.processing
    assert "injected milestone failure" in (receipt.last_error or "")
