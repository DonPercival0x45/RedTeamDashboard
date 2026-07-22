"""Strategic watcher consumer loop.

A second Redis Streams consumer that lives alongside the existing
``StreamConsumer``. Instead of reading the *inbound* per-engagement command
streams, this one reads the *outbound* event streams (``runs:{eid}:events``)
under a NEW consumer group (``strategic-watcher``) so it doesn't compete with
the SSE endpoint or with other workers' delivery of inbound commands.

For ``finding.created`` envelopes it loads the persisted ``Finding`` and asks
``StrategicAgent`` to propose next-step suggestions. Durable
``strategy.reassess.requested`` events run the existing engagement strategist
after rechecking the engagement kill switch and acquiring its cooldown.
Nothing dispatches until the analyst accepts — pure watcher.

Failure handling: successful/no-op events are ACKed. Transient handler failures
stay pending and are reclaimed after an idle interval; poison events move to a
dead-letter stream with an audit record after bounded deliveries. An LLM-call
failure handled by ``StrategicAgent`` still commits a failed execution normally.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

import structlog
from redis.exceptions import ResponseError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents import StrategicAgent
from app.models import (
    ActorType,
    AgentExecution,
    AgentExecutionStatus,
    AgentTrigger,
    AuditLog,
    Engagement,
    EngagementStatus,
    Finding,
    Task,
)
from app.runs.streams import engagement_id_from_outbound, outbound_stream
from app.services.engagement_strategist import (
    acquire_auto_reassess_cooldown,
    release_auto_reassess_cooldown,
    run_engagement_strategist,
)
from app.services.processing_receipt import (
    claim,
    complete,
    locked_session,
    record_error,
)
from app.worker.stream_recovery import claim_stale, dead_letter, delivery_count

logger = structlog.get_logger(__name__)

SessionFactory = Callable[[], Session]

STRATEGIC_GROUP = "strategic-watcher"


class StrategicConsumer:
    def __init__(
        self,
        *,
        agent: StrategicAgent,
        redis_client: Any,
        session_factory: SessionFactory,
        consumer_group: str = STRATEGIC_GROUP,
        consumer_name: str | None = None,
        refresh_interval: float = 5.0,
        claim_idle_ms: int = 300_000,
        max_delivery_attempts: int = 5,
        reclaim_count: int = 10,
    ) -> None:
        self._agent = agent
        self._redis = redis_client
        self._session_factory = session_factory
        self._group = consumer_group
        self._consumer = consumer_name or f"strategic-{uuid.uuid4().hex[:8]}"
        self._refresh_interval = refresh_interval
        self._known_streams: set[str] = set()
        self._last_refresh = 0.0
        self._claim_idle_ms = claim_idle_ms
        self._max_delivery_attempts = max_delivery_attempts
        self._reclaim_count = reclaim_count

    def _active_engagement_ids(self) -> list[uuid.UUID]:
        session = self._session_factory()
        try:
            return list(
                session.execute(
                    select(Engagement.id).where(Engagement.status == EngagementStatus.active)
                ).scalars()
            )
        finally:
            session.close()

    def _ensure_group(self, stream: str) -> None:
        try:
            # Start at 0 so events emitted before the watcher's first discovery
            # are not lost. Durable event receipts suppress completed replays.
            self._redis.xgroup_create(stream, self._group, id="0", mkstream=True)
            logger.info("strategic.group_created", stream=stream, group=self._group)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    def refresh_streams(self) -> set[str]:
        streams = {outbound_stream(eid) for eid in self._active_engagement_ids()}
        for s in streams - self._known_streams:
            self._ensure_group(s)
        self._known_streams = streams
        self._last_refresh = time.time()
        return streams

    def run_once(self, *, block_ms: int = 1000) -> int:
        if time.time() - self._last_refresh > self._refresh_interval:
            self.refresh_streams()

        if not self._known_streams:
            time.sleep(min(block_ms / 1000.0, 0.5))
            return 0

        reclaimed = self._reclaim_pending()
        if reclaimed:
            return reclaimed

        try:
            response = self._redis.xreadgroup(
                self._group,
                self._consumer,
                {s: ">" for s in self._known_streams},
                count=10,
                block=block_ms,
            )
        except ResponseError as exc:
            if "NOGROUP" in str(exc):
                # The outbound stream was deleted (engagement flushed). Forget
                # everything and let the next refresh recreate as needed.
                logger.warning("strategic.nogroup_recovering", error=str(exc))
                self._known_streams = set()
                self._last_refresh = 0.0
                return 0
            raise

        processed = 0
        for stream_name, messages in response or []:
            for msg_id, fields in messages:
                self._process_one(stream_name, msg_id, fields)
                processed += 1
        return processed

    def run_forever(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            try:
                self.run_once(block_ms=1000)
            except Exception:
                logger.exception("strategic.iteration_failed")
                time.sleep(1.0)

    def _reclaim_pending(self) -> int:
        processed = 0
        for stream in sorted(self._known_streams):
            try:
                messages = claim_stale(
                    self._redis,
                    stream=stream,
                    group=self._group,
                    consumer=self._consumer,
                    min_idle_ms=self._claim_idle_ms,
                    count=self._reclaim_count,
                )
            except ResponseError as exc:
                if "NOGROUP" in str(exc):
                    self._known_streams = set()
                    self._last_refresh = 0.0
                    return processed
                raise
            for msg_id, fields in messages:
                self._process_one(stream, msg_id, fields)
                processed += 1
        return processed

    def _process_one(
        self,
        stream_name: str,
        msg_id: str,
        fields: dict[str, Any],
    ) -> None:
        delivery_id = f"event:{stream_name}:{msg_id}"
        try:
            engagement_id = engagement_id_from_outbound(stream_name)
            raw = fields.get("data") or fields.get(b"data")
            if raw is None:
                raise ValueError("strategic envelope missing data")
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            envelope = json.loads(raw)
            delivery_id = f"event:{envelope.get('event_id') or f'{stream_name}:{msg_id}'}"
            event_type = envelope.get("type")
            thread_id = str(envelope.get("thread_id") or "") or None

            with locked_session(self._session_factory, delivery_id) as receipt_session:
                receipt = None
                try:
                    receipt, should_process = claim(
                        receipt_session,
                        delivery_id=delivery_id,
                        kind="strategic_event",
                        engagement_id=engagement_id,
                        thread_id=thread_id,
                    )
                    if not should_process:
                        logger.info("strategic.duplicate_event_skipped", delivery_id=delivery_id)
                    elif envelope.get("source") == "worker_lifecycle":
                        # Legacy envelopes have no analyst identity. They are a
                        # live-run notification, not a Strategic instruction.
                        complete(receipt_session, receipt)
                    elif event_type in ("finding.created", "finding.updated"):
                        finding_id_raw = envelope.get("finding_id")
                        acting_user_id_raw = envelope.get("acting_user_id")
                        if not finding_id_raw or not acting_user_id_raw:
                            raise ValueError(f"{event_type} missing finding/actor identity")
                        if receipt.agent_execution_id is None:
                            receipt.agent_execution_id = uuid.uuid4()
                            receipt_session.commit()
                        self._analyze(
                            receipt_session,
                            uuid.UUID(finding_id_raw),
                            acting_user_id=uuid.UUID(acting_user_id_raw),
                            execution_id=receipt.agent_execution_id,
                        )
                        complete(receipt_session, receipt)
                    elif event_type == "strategy.reassess.requested":
                        acting_user_id_raw = envelope.get("acting_user_id")
                        if not acting_user_id_raw:
                            raise ValueError(
                                "strategy.reassess.requested missing actor identity"
                            )
                        if receipt.agent_execution_id is None:
                            receipt.agent_execution_id = uuid.uuid4()
                            receipt_session.commit()
                        self._reassess(
                            receipt_session,
                            engagement_id=engagement_id,
                            acting_user_id=uuid.UUID(acting_user_id_raw),
                            execution_id=receipt.agent_execution_id,
                        )
                        complete(receipt_session, receipt)
                    elif event_type in ("run.completed", "run.errored"):
                        if not thread_id:
                            raise ValueError(f"{event_type} missing thread_id")
                        self._release_lease_for_run(
                            receipt_session, uuid.UUID(thread_id), reason=event_type
                        )
                        complete(receipt_session, receipt)
                    else:
                        complete(receipt_session, receipt)
                except Exception as exc:
                    if receipt is not None:
                        record_error(receipt_session, receipt, exc)
                    raise
        except Exception as exc:
            attempts = delivery_count(
                self._redis, stream=stream_name, group=self._group, message_id=msg_id
            )
            logger.exception(
                "strategic.message_failed",
                stream=stream_name,
                msg_id=msg_id,
                attempts=attempts,
            )
            if attempts >= self._max_delivery_attempts:
                dead_letter(
                    self._redis,
                    stream=stream_name,
                    group=self._group,
                    message_id=msg_id,
                    fields=fields,
                    error=str(exc),
                    attempts=attempts,
                )
                self._audit_dead_letter(
                    engagement_id_from_outbound(stream_name),
                    msg_id=msg_id,
                    error=str(exc),
                    attempts=attempts,
                )
            return

        try:
            self._redis.xack(stream_name, self._group, msg_id)
        except Exception:
            logger.exception("strategic.ack_failed", stream=stream_name, msg_id=msg_id)

    def _audit_dead_letter(
        self,
        engagement_id: uuid.UUID,
        *,
        msg_id: str,
        error: str,
        attempts: int,
    ) -> None:
        session = self._session_factory()
        try:
            session.add(
                AuditLog(
                    engagement_id=engagement_id,
                    actor_type=ActorType.agent,
                    actor_id=self._consumer,
                    event_type="strategic.event_dead_lettered",
                    payload={
                        "message_id": msg_id,
                        "consumer_group": self._group,
                        "attempts": attempts,
                        "error": error[:2000],
                    },
                )
            )
            session.commit()
        except Exception:
            session.rollback()
            logger.exception("strategic.dead_letter_audit_failed", msg_id=msg_id)
        finally:
            session.close()

    def _release_lease_for_run(
        self, session: Session, thread_id: uuid.UUID, *, reason: str
    ) -> None:
        """Release the lease tied to this run on terminal events.

        Two lookup paths because Stage 3+1 introduced direct-run leases
        (no Task wrapping them):
        - Tactical-dispatched runs: find the Task by run_id, then the
          lease by task_id.
        - Direct runs (POST /engagements/{slug}/runs): no Task; the lease
          stashed ``thread_id`` in its ``context["_thread_id"]`` at mint
          time, so we look it up there.

        Idempotent — redelivered terminal events are safe."""
        from sqlalchemy import select

        from app.services import mcp_lease

        try:
            task = session.execute(
                select(Task).where(Task.run_id == thread_id)
            ).scalar_one_or_none()
            active = (
                mcp_lease.find_active_for_task(session, task.id)
                if task is not None
                else mcp_lease.find_active_for_thread(session, thread_id)
            )
            if active is None:
                return
            mcp_lease.release(session, lease_id=active.id, reason=reason)
            logger.info(
                "strategic.lease_released",
                task_id=str(task.id) if task is not None else None,
                lease_id=str(active.id),
                reason=reason,
            )
        except Exception:
            session.rollback()
            logger.exception("strategic.release_lease_failed")
            raise

    def _reassess(
        self,
        session: Session,
        *,
        engagement_id: uuid.UUID,
        acting_user_id: uuid.UUID,
        execution_id: uuid.UUID,
    ) -> None:
        engagement = session.execute(
            select(Engagement)
            .where(Engagement.id == engagement_id)
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()
        if engagement is None:
            logger.info("auto_reassess.engagement_missing", engagement_id=str(engagement_id))
            return
        if not engagement.auto_assess_enabled:
            logger.info("auto_reassess.disabled", engagement_id=str(engagement_id))
            return

        existing = session.get(AgentExecution, execution_id)
        if existing is not None and existing.status == AgentExecutionStatus.completed:
            logger.info(
                "auto_reassess.execution_already_completed",
                engagement_id=str(engagement_id),
                execution_id=str(execution_id),
            )
            return

        owner_token = str(execution_id)
        cooldown_token = acquire_auto_reassess_cooldown(
            self._redis,
            engagement_id,
            owner_token=owner_token,
        )
        if cooldown_token is None:
            logger.info("auto_reassess.cooldown_active", engagement_id=str(engagement_id))
            return
        try:
            execution, _output, _context_hash, suggestions = run_engagement_strategist(
                session,
                self._redis,
                engagement=engagement,
                acting_user_id=acting_user_id,
                mode="reassess",
                execution_id=execution_id,
            )
            logger.info(
                "auto_reassess.completed",
                engagement_id=str(engagement_id),
                execution_id=str(execution.id),
                suggestion_count=len(suggestions),
            )
        except Exception:
            session.rollback()
            try:
                release_auto_reassess_cooldown(
                    self._redis, engagement_id, cooldown_token
                )
            except Exception:
                # The stable execution token retains ownership, so retry may
                # proceed even if Redis was unavailable during cleanup.
                logger.exception(
                    "auto_reassess.cooldown_release_failed",
                    engagement_id=str(engagement_id),
                )
            logger.exception("auto_reassess.failed", engagement_id=str(engagement_id))
            raise

    def _analyze(
        self,
        session: Session,
        finding_id: uuid.UUID,
        *,
        acting_user_id: uuid.UUID,
        execution_id: uuid.UUID,
    ) -> None:
        try:
            finding = session.get(Finding, finding_id)
            if finding is None:
                raise ValueError(f"finding {finding_id} not found")
            execution, suggestions = self._agent.analyze_finding(
                session,
                finding=finding,
                trigger=AgentTrigger.finding,
                acting_user_id=acting_user_id,
                execution_id=execution_id,
            )
            logger.info(
                "strategic.analyzed",
                finding_id=str(finding_id),
                execution_id=str(execution.id),
                suggestion_count=len(suggestions),
            )
        except Exception:
            session.rollback()
            logger.exception("strategic.analyze_failed", finding_id=str(finding_id))
            raise
