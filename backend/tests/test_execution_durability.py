from __future__ import annotations

import json
import threading
import time
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
import redis as redis_lib
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.agents.tactical import TacticalAgent
from app.api.deps import redis_client as redis_dependency
from app.core.config import settings
from app.db.session import SessionLocal
from app.main import app
from app.models import (
    AgentExecution,
    AgentExecutionStatus,
    AgentName,
    AgentTrigger,
    Approval,
    ApprovalStatus,
    CommandOutbox,
    CommandOutboxStatus,
    Engagement,
    EngagementStatus,
    Finding,
    FindingOrigin,
    FindingPhase,
    FindingStatus,
    OwnerEligibility,
    ProcessingReceipt,
    RiskLevel,
    Severity,
    Task,
    TaskKind,
    TaskStatus,
)
from app.runs.events import encode_command, encode_event
from app.runs.streams import inbound_stream, outbound_stream
from app.services.command_outbox import enqueue_command, enqueue_event, publish_entry
from app.worker.consumer import StreamConsumer
from app.worker.outbox_relay import CommandOutboxRelay
from app.worker.runner import RunRunner
from app.worker.strategic_consumer import StrategicConsumer

HDR = {"X-User-Id": "durability@example.com"}


@pytest.fixture()
def redis_client() -> Iterator[redis_lib.Redis]:
    client = redis_lib.Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        yield client
    finally:
        client.close()


@pytest.fixture()
def engagement(db: Session) -> Iterator[Engagement]:
    row = Engagement(
        name="execution durability",
        slug=f"execution-durability-{uuid.uuid4().hex[:8]}",
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


def _approval(db: Session, engagement: Engagement) -> Approval:
    row = Approval(
        engagement_id=engagement.id,
        thread_id=str(uuid.uuid4()),
        tool_name="portscan",
        tool_call_id="call-durability-test",
        tool_args={"ip": "10.0.0.5"},
        risk=RiskLevel.active,
        scope_check={"ok": True},
        status=ApprovalStatus.pending,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _pending_count(client: redis_lib.Redis, stream: str, group: str) -> int:
    """Normalize Redis 7 tuple and Redis 8 mapping XPENDING summaries."""
    summary = client.xpending(stream, group)
    if isinstance(summary, dict):
        return int(summary.get("pending", summary.get(b"pending", 0)) or 0)
    if isinstance(summary, (list, tuple)) and summary:
        return int(summary[0] or 0)
    return int(getattr(summary, "pending", 0) or 0)


class _FailingRedis:
    def hgetall(self, _key: str) -> dict[str, str]:
        return {}

    def xadd(self, _stream: str, _fields: dict[str, str]) -> None:
        raise ConnectionError("injected redis outage")


def test_approval_redis_failure_leaves_relayable_outbox(
    db: Session,
    engagement: Engagement,
    redis_client: redis_lib.Redis,
) -> None:
    approval = _approval(db, engagement)
    app.dependency_overrides[redis_dependency] = lambda: _FailingRedis()
    try:
        response = TestClient(app).post(
            f"/approvals/{approval.id}/decision", headers=HDR, json={"approved": True}
        )
    finally:
        app.dependency_overrides.pop(redis_dependency, None)
    assert response.status_code == 200

    db.expire_all()
    outbox = db.execute(
        select(CommandOutbox).where(
            CommandOutbox.idempotency_key == f"approval.resume:{approval.id}"
        )
    ).scalar_one()
    assert outbox.status == CommandOutboxStatus.pending
    assert outbox.attempts == 1
    assert "injected redis outage" in (outbox.last_error or "")
    outbox.next_attempt_at = None
    db.commit()

    redis_client.delete(inbound_stream(engagement.id))
    relay = CommandOutboxRelay(
        redis_client=redis_client,
        session_factory=SessionLocal,
        interval_seconds=0,
    )
    assert relay.run_once() == 1
    payload = json.loads(redis_client.xrange(inbound_stream(engagement.id))[0][1]["data"])
    assert payload["command_id"] == f"approval.resume:{approval.id}"
    redis_client.delete(inbound_stream(engagement.id))


def test_concurrent_conflicting_approval_decisions_publish_one_command(
    db: Session,
    engagement: Engagement,
    redis_client: redis_lib.Redis,
) -> None:
    approval = _approval(db, engagement)
    stream = inbound_stream(engagement.id)
    redis_client.delete(stream)

    def decide(approved: bool) -> int:
        return (
            TestClient(app)
            .post(
                f"/approvals/{approval.id}/decision",
                headers=HDR,
                json={"approved": approved},
            )
            .status_code
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = sorted(pool.map(decide, [True, False]))
    assert statuses == [200, 409]
    assert len(redis_client.xrange(stream)) == 1
    assert (
        db.execute(
            select(CommandOutbox).where(
                CommandOutbox.idempotency_key == f"approval.resume:{approval.id}"
            )
        )
        .scalars()
        .all()
        .__len__()
        == 1
    )
    redis_client.delete(stream)


class _RecordingRunner:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.messages: list[dict[str, Any]] = []

    def handle(self, _engagement_id: uuid.UUID, envelope: dict[str, Any]) -> None:
        if self.fail:
            raise ConnectionError("injected transient handler failure")
        self.messages.append(envelope)


def test_inbound_consumer_reclaims_abandoned_pending_entry(
    engagement: Engagement, redis_client: redis_lib.Redis
) -> None:
    stream = inbound_stream(engagement.id)
    group = f"durability-{uuid.uuid4().hex[:8]}"
    redis_client.delete(stream)
    redis_client.xgroup_create(stream, group, id="0", mkstream=True)
    redis_client.xadd(
        stream,
        encode_command({"type": "run.start", "thread_id": str(uuid.uuid4()), "prompt": "test"}),
    )
    redis_client.xreadgroup(group, "dead-worker", {stream: ">"}, count=1)

    runner = _RecordingRunner()
    consumer = StreamConsumer(
        runner=runner,  # type: ignore[arg-type]
        redis_client=redis_client,
        session_factory=SessionLocal,
        consumer_group=group,
        engagement_ids=[engagement.id],
        claim_idle_ms=0,
    )
    consumer.refresh_streams()
    assert consumer.run_once(block_ms=1) == 1
    assert len(runner.messages) == 1
    assert _pending_count(redis_client, stream, group) == 0
    redis_client.delete(stream)


def test_inbound_failure_is_not_acked_then_dead_letters(
    engagement: Engagement, redis_client: redis_lib.Redis
) -> None:
    stream = inbound_stream(engagement.id)
    group = f"poison-{uuid.uuid4().hex[:8]}"
    redis_client.delete(stream)
    runner = _RecordingRunner(fail=True)
    consumer = StreamConsumer(
        runner=runner,  # type: ignore[arg-type]
        redis_client=redis_client,
        session_factory=SessionLocal,
        consumer_group=group,
        engagement_ids=[engagement.id],
        claim_idle_ms=0,
        max_delivery_attempts=2,
    )
    consumer.refresh_streams()
    redis_client.xadd(
        stream,
        encode_command({"type": "run.start", "thread_id": str(uuid.uuid4()), "prompt": "test"}),
    )
    assert consumer.run_once(block_ms=1) == 1
    assert _pending_count(redis_client, stream, group) == 1
    assert consumer.run_once(block_ms=1) == 1
    assert _pending_count(redis_client, stream, group) == 0
    assert redis_client.xlen(f"{stream}:dead:{group}") == 1
    redis_client.delete(stream, f"{stream}:dead:{group}")


def test_strategic_consumer_reclaims_abandoned_event(
    engagement: Engagement, redis_client: redis_lib.Redis
) -> None:
    stream = outbound_stream(engagement.id)
    group = f"strategic-durability-{uuid.uuid4().hex[:8]}"
    redis_client.delete(stream)
    redis_client.xgroup_create(stream, group, id="0", mkstream=True)
    redis_client.xadd(
        stream,
        encode_event({"type": "run.started", "thread_id": str(uuid.uuid4()), "prompt": "test"}),
    )
    redis_client.xreadgroup(group, "dead-strategic", {stream: ">"}, count=1)
    consumer = StrategicConsumer(
        agent=object(),  # type: ignore[arg-type]
        redis_client=redis_client,
        session_factory=SessionLocal,
        consumer_group=group,
        claim_idle_ms=0,
    )
    consumer._known_streams = {stream}
    consumer._last_refresh = time.time()
    assert consumer.run_once(block_ms=1) == 1
    assert _pending_count(redis_client, stream, group) == 0
    redis_client.delete(stream)


def _published_start(
    db: Session, engagement: Engagement, *, task: Task | None = None
) -> tuple[CommandOutbox, dict[str, str]]:
    thread_id = str(task.run_id if task and task.run_id else uuid.uuid4())
    row = enqueue_command(
        db,
        idempotency_key=f"run.start:{thread_id}",
        engagement_id=engagement.id,
        task_id=task.id if task else None,
        stream_name=inbound_stream(engagement.id),
        payload={"type": "run.start", "thread_id": thread_id, "prompt": "durable"},
    )
    row.status = CommandOutboxStatus.published
    db.commit()
    db.refresh(row)
    return row, dict(row.encoded_payload)


def test_concurrent_duplicate_commands_have_one_durable_effect(
    db: Session, engagement: Engagement, redis_client: redis_lib.Redis
) -> None:
    row, fields = _published_start(db, engagement)
    runner = _RecordingRunner()
    group = f"duplicate-{uuid.uuid4().hex[:8]}"
    stream = inbound_stream(engagement.id)
    redis_client.delete(stream)
    redis_client.xgroup_create(stream, group, id="0", mkstream=True)
    first = StreamConsumer(
        runner=runner,  # type: ignore[arg-type]
        redis_client=redis_client,
        session_factory=SessionLocal,
        consumer_group=group,
        engagement_ids=[engagement.id],
    )
    second = StreamConsumer(
        runner=runner,  # type: ignore[arg-type]
        redis_client=redis_client,
        session_factory=SessionLocal,
        consumer_group=group,
        engagement_ids=[engagement.id],
    )
    threads = [
        threading.Thread(target=consumer._process_one, args=(stream, f"{i}-0", fields))
        for i, consumer in enumerate((first, second), start=1)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    assert all(not thread.is_alive() for thread in threads)
    assert len(runner.messages) == 1
    receipt = db.get(ProcessingReceipt, f"command:{row.idempotency_key}")
    assert receipt is not None and receipt.status.value == "completed"
    redis_client.delete(stream)


def test_processing_crash_is_reclaimable_without_losing_receipt(
    db: Session, engagement: Engagement, redis_client: redis_lib.Redis
) -> None:
    row, fields = _published_start(db, engagement)
    stream = inbound_stream(engagement.id)
    group = f"crash-{uuid.uuid4().hex[:8]}"
    redis_client.delete(stream)
    redis_client.xgroup_create(stream, group, id="0", mkstream=True)
    message_id = redis_client.xadd(stream, fields)
    redis_client.xreadgroup(group, "crashed", {stream: ">"}, count=1)
    failed = StreamConsumer(
        runner=_RecordingRunner(fail=True),  # type: ignore[arg-type]
        redis_client=redis_client,
        session_factory=SessionLocal,
        consumer_group=group,
        engagement_ids=[engagement.id],
        claim_idle_ms=0,
    )
    failed._process_one(stream, message_id, fields)
    recovered_runner = _RecordingRunner()
    recovered = StreamConsumer(
        runner=recovered_runner,  # type: ignore[arg-type]
        redis_client=redis_client,
        session_factory=SessionLocal,
        consumer_group=group,
        engagement_ids=[engagement.id],
        claim_idle_ms=0,
    )
    recovered._process_one(stream, message_id, fields)
    assert len(recovered_runner.messages) == 1
    redis_client.delete(stream)


def test_relay_cancel_race_ends_tombstoned(db: Session, engagement: Engagement) -> None:
    thread_id = uuid.uuid4()
    task = Task(
        engagement_id=engagement.id,
        title="race",
        kind=TaskKind.scan,
        owner_eligibility=OwnerEligibility.agent,
        status=TaskStatus.dispatched,
        payload={"tool": "portscan", "target": "10.0.0.5"},
        run_id=thread_id,
    )
    db.add(task)
    db.flush()
    outbox, _ = _published_start(db, engagement, task=task)
    outbox.status = CommandOutboxStatus.pending
    db.commit()
    entered = threading.Event()
    release = threading.Event()

    class BlockingRedis:
        def xadd(self, _stream: str, _fields: dict[str, str]) -> str:
            entered.set()
            assert release.wait(5)
            return "1-0"

    publisher = threading.Thread(
        target=lambda: publish_entry(SessionLocal(), BlockingRedis(), outbox.id)
    )
    publisher.start()
    assert entered.wait(5)

    from app.api.status import _tombstone_run_outbox

    def cancel() -> None:
        with SessionLocal() as session:
            locked = session.execute(
                select(Task).where(Task.id == task.id).with_for_update()
            ).scalar_one()
            _tombstone_run_outbox(session, locked)
            locked.status = TaskStatus.cancelled
            session.commit()

    canceller = threading.Thread(target=cancel)
    canceller.start()
    release.set()
    publisher.join(timeout=5)
    canceller.join(timeout=5)
    db.expire_all()
    assert db.get(CommandOutbox, outbox.id).status == CommandOutboxStatus.cancelled


def test_strategic_event_receipt_prevents_ack_replay_llm_call(
    db: Session, engagement: Engagement, redis_client: redis_lib.Redis
) -> None:
    finding = Finding(
        engagement_id=engagement.id,
        title="receipt finding",
        target="example.com",
        severity=Severity.info,
        status=FindingStatus.validated,
        phase=FindingPhase.osint,
    )
    db.add(finding)
    db.commit()
    calls = 0

    class Agent:
        def analyze_finding(self, _session: Session, **_kwargs: Any) -> tuple[Any, list[Any]]:
            nonlocal calls
            calls += 1
            return SimpleNamespace(id=_kwargs["execution_id"]), []

    stream = outbound_stream(engagement.id)
    group = f"event-receipt-{uuid.uuid4().hex[:8]}"
    redis_client.delete(stream)
    redis_client.xgroup_create(stream, group, id="0", mkstream=True)
    event_id = str(uuid.uuid4())
    fields = encode_event(
        {
            "type": "finding.created",
            "event_id": event_id,
            "thread_id": str(uuid.uuid4()),
            "finding_id": str(finding.id),
            "acting_user_id": str(uuid.uuid4()),
        }
    )
    consumer = StrategicConsumer(
        agent=Agent(),  # type: ignore[arg-type]
        redis_client=redis_client,
        session_factory=SessionLocal,
        consumer_group=group,
    )
    consumer._process_one(stream, "1-0", fields)
    consumer._process_one(stream, "1-0", fields)
    assert calls == 1
    redis_client.delete(stream)


def test_stale_resume_cannot_apply_to_later_interrupt(db: Session, engagement: Engagement) -> None:
    approval = _approval(db, engagement)
    approval.status = ApprovalStatus.approved
    approval.decision_args = {"approved": True}
    db.commit()

    class Graph:
        stream_calls = 0

        def get_state(self, _config: dict[str, Any]) -> Any:
            interrupt = SimpleNamespace(value={"tool_call_id": "later-call"})
            return SimpleNamespace(
                tasks=[SimpleNamespace(interrupts=[interrupt])], values={}, next=("tool_dispatch",)
            )

        def stream(self, *_args: Any, **_kwargs: Any) -> list[Any]:
            self.stream_calls += 1
            return []

    graph = Graph()
    runner = RunRunner(
        graph=graph,
        redis_client=SimpleNamespace(xadd=lambda *_args, **_kwargs: None),
        session_factory=SessionLocal,
    )
    runner.handle(
        engagement.id,
        {
            "type": "run.resume",
            "thread_id": approval.thread_id,
            "approval_id": str(approval.id),
            "tool_call_id": approval.tool_call_id,
            "approved": True,
        },
    )
    assert graph.stream_calls == 0


def test_tactical_pre_outbox_failure_rolls_back_caller_transaction(
    db: Session,
    engagement: Engagement,
    redis_client: redis_lib.Redis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = Task(
        engagement_id=engagement.id,
        title="atomic tactical",
        kind=TaskKind.scan,
        owner_eligibility=OwnerEligibility.agent,
        status=TaskStatus.pending,
        payload={"tool": "portscan", "target": "10.0.0.5"},
    )
    db.add(task)
    db.flush()
    task_id = task.id

    from app.agents.strategic import StrategicAgent

    monkeypatch.setattr(
        StrategicAgent,
        "provision_lease",
        lambda *_args, **_kwargs: SimpleNamespace(id=uuid.uuid4(), requires_container=False),
    )

    def fail_before_outbox(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("injected pre-outbox stop")

    monkeypatch.setattr("app.agents.tactical.enqueue_command", fail_before_outbox)
    with pytest.raises(RuntimeError, match="pre-outbox"):
        TacticalAgent(redis_client).dispatch(db, task=task, acting_user_id=uuid.uuid4())
    db.rollback()
    assert db.get(Task, task_id) is None


def test_outbox_poison_backoff_reaches_failed_terminal_state(
    db: Session, engagement: Engagement
) -> None:
    row = enqueue_command(
        db,
        idempotency_key=f"run.start:{uuid.uuid4()}",
        engagement_id=engagement.id,
        stream_name=inbound_stream(engagement.id),
        payload={"type": "run.start", "thread_id": str(uuid.uuid4()), "prompt": "bad"},
    )
    db.commit()
    for _ in range(10):
        publish_entry(db, _FailingRedis(), row.id)
        db.expire_all()
        current = db.get(CommandOutbox, row.id)
        if current.status == CommandOutboxStatus.failed:
            break
        current.next_attempt_at = None
        db.commit()
    db.expire_all()
    assert db.get(CommandOutbox, row.id).status == CommandOutboxStatus.failed


def test_strategic_group_processes_event_emitted_before_discovery_once(
    db: Session, engagement: Engagement, redis_client: redis_lib.Redis
) -> None:
    finding = Finding(
        engagement_id=engagement.id,
        title="pre-discovery",
        target="pre.example",
        severity=Severity.info,
        status=FindingStatus.validated,
        phase=FindingPhase.osint,
    )
    db.add(finding)
    db.commit()
    calls = 0

    class Agent:
        def analyze_finding(self, _session: Session, **_kwargs: Any) -> tuple[Any, list[Any]]:
            nonlocal calls
            calls += 1
            return SimpleNamespace(id=_kwargs["execution_id"]), []

    stream = outbound_stream(engagement.id)
    group = f"pre-discovery-{uuid.uuid4().hex[:8]}"
    redis_client.delete(stream)
    redis_client.xadd(
        stream,
        encode_event(
            {
                "type": "finding.created",
                "event_id": str(uuid.uuid4()),
                "thread_id": str(uuid.uuid4()),
                "finding_id": str(finding.id),
                "acting_user_id": str(uuid.uuid4()),
            }
        ),
    )
    consumer = StrategicConsumer(
        agent=Agent(),  # type: ignore[arg-type]
        redis_client=redis_client,
        session_factory=SessionLocal,
        consumer_group=group,
        refresh_interval=60,
    )
    consumer._active_engagement_ids = lambda: [engagement.id]  # type: ignore[method-assign]
    assert consumer.run_once(block_ms=1) == 1
    assert consumer.run_once(block_ms=1) == 0
    assert calls == 1
    redis_client.delete(stream)


def test_strategic_crash_after_execution_commit_reuses_accounting_identity(
    db: Session, engagement: Engagement, redis_client: redis_lib.Redis
) -> None:
    finding = Finding(
        engagement_id=engagement.id,
        title="crash identity",
        target="crash.example",
        severity=Severity.info,
        status=FindingStatus.validated,
        phase=FindingPhase.osint,
    )
    db.add(finding)
    db.commit()
    calls = 0

    class CrashThenRecoverAgent:
        def analyze_finding(
            self, session: Session, **kwargs: Any
        ) -> tuple[AgentExecution, list[Any]]:
            nonlocal calls
            calls += 1
            execution_id = kwargs["execution_id"]
            execution = session.get(AgentExecution, execution_id)
            if execution is None:
                execution = AgentExecution(
                    id=execution_id,
                    engagement_id=engagement.id,
                    agent=AgentName.strategic,
                    trigger=AgentTrigger.finding,
                    input={"finding_id": str(finding.id)},
                    status=AgentExecutionStatus.running,
                    started_at=datetime.now(tz=UTC),
                )
                session.add(execution)
                session.commit()
                raise ConnectionError("crash after initial execution commit")
            execution.status = AgentExecutionStatus.completed
            execution.completed_at = datetime.now(tz=UTC)
            return execution, []

    stream = outbound_stream(engagement.id)
    group = f"execution-replay-{uuid.uuid4().hex[:8]}"
    redis_client.delete(stream)
    redis_client.xgroup_create(stream, group, id="0", mkstream=True)
    event_id = str(uuid.uuid4())
    fields = encode_event(
        {
            "type": "finding.created",
            "event_id": event_id,
            "thread_id": str(uuid.uuid4()),
            "finding_id": str(finding.id),
            "acting_user_id": str(uuid.uuid4()),
        }
    )
    consumer = StrategicConsumer(
        agent=CrashThenRecoverAgent(),  # type: ignore[arg-type]
        redis_client=redis_client,
        session_factory=SessionLocal,
        consumer_group=group,
    )
    message_id = redis_client.xadd(stream, fields)
    redis_client.xreadgroup(group, "crashed", {stream: ">"}, count=1)
    consumer._process_one(stream, message_id, fields)
    consumer._process_one(stream, message_id, fields)

    receipt = db.get(ProcessingReceipt, f"event:{event_id}")
    executions = list(
        db.execute(
            select(AgentExecution).where(
                AgentExecution.engagement_id == engagement.id,
                AgentExecution.input["finding_id"].astext == str(finding.id),
            )
        ).scalars()
    )
    assert calls == 2
    assert len(executions) == 1
    assert receipt is not None
    assert receipt.agent_execution_id == executions[0].id
    assert executions[0].status == AgentExecutionStatus.completed
    redis_client.delete(stream)


@pytest.mark.parametrize(
    ("tool", "args", "data"),
    [
        ("unknown_tool", {"target": "plain.example"}, {"value": "plain"}),
        ("dns_lookup", {"domain": "grouped.example"}, {"domain": "grouped.example"}),
    ],
)
def test_finding_event_outbox_relays_after_post_commit_redis_failure(
    db: Session,
    engagement: Engagement,
    redis_client: redis_lib.Redis,
    tool: str,
    args: dict[str, Any],
    data: dict[str, Any],
) -> None:
    thread_id = str(uuid.uuid4())
    runner = RunRunner(
        graph=SimpleNamespace(),
        redis_client=_FailingRedis(),
        session_factory=SessionLocal,
    )
    row = runner._persist_finding(
        engagement.id,
        thread_id,
        {
            "tool": tool,
            "args": args,
            "data": data,
            "target": next(iter(args.values())),
            "title": f"durable {tool}",
        },
        acting_user_id=str(uuid.uuid4()),
    )
    db.expire_all()
    outbox = db.execute(
        select(CommandOutbox).where(
            CommandOutbox.delivery_kind == "event",
            CommandOutbox.thread_id == thread_id,
        )
    ).scalar_one()
    origin = db.execute(
        select(FindingOrigin).where(
            FindingOrigin.finding_id == row.id,
            FindingOrigin.thread_id == uuid.UUID(thread_id),
        )
    ).scalar_one()
    assert origin.source_tool == tool
    assert outbox.status == CommandOutboxStatus.pending
    assert outbox.attempts == 1
    payload = json.loads(outbox.encoded_payload["data"])
    assert payload["event_id"] == payload["feedback_id"] == outbox.idempotency_key
    assert payload["finding_id"] == str(row.id)

    outbox.next_attempt_at = None
    db.commit()
    stream = outbound_stream(engagement.id)
    redis_client.delete(stream)
    relay = CommandOutboxRelay(redis_client=redis_client, session_factory=SessionLocal)
    assert relay.run_once() == 1
    published = json.loads(redis_client.xrange(stream)[0][1]["data"])
    assert published["event_id"] == outbox.idempotency_key
    assert published["finding_id"] == str(row.id)
    redis_client.delete(stream)


def test_durable_event_outbox_never_enters_failed_terminal_state(
    db: Session, engagement: Engagement
) -> None:
    row = enqueue_event(
        db,
        idempotency_key=f"finding.created:{uuid.uuid4()}",
        engagement_id=engagement.id,
        stream_name=outbound_stream(engagement.id),
        thread_id=str(uuid.uuid4()),
        payload={
            "type": "finding.created",
            "thread_id": str(uuid.uuid4()),
            "finding_id": str(uuid.uuid4()),
            "acting_user_id": str(uuid.uuid4()),
        },
    )
    db.commit()
    for _ in range(12):
        publish_entry(db, _FailingRedis(), row.id)
        db.expire_all()
        current = db.get(CommandOutbox, row.id)
        assert current.status == CommandOutboxStatus.pending
        assert current.next_attempt_at is not None
        current.next_attempt_at = None
        db.commit()
