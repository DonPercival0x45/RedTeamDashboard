"""Redis Streams consumer loop.

Discovery: at refresh intervals the consumer queries the DB for active
engagement IDs, builds the set of inbound stream names, and ensures the
consumer group exists on each (idempotent ``XGROUP CREATE MKSTREAM``).

Polling: a single blocking ``XREADGROUP`` across all known streams. Each
message is decoded into an envelope and handed to ``RunRunner.handle``.
Successful deliveries are ACKed. Failures remain in the pending-entry list,
are reclaimed after a bounded idle interval, and move to a dead-letter stream
after a bounded delivery count. Stable outbox command IDs claim durable DB
receipts under a per-thread advisory lock; checkpoints make crash replay safe.

Shutdown: callers pass a ``threading.Event``; the loop checks it between
poll cycles. The blocking ``XREADGROUP`` uses a bounded ``block`` timeout so
stop signals are honored within that window.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

import structlog
from redis.exceptions import ResponseError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ActorType, AuditLog, Engagement, EngagementStatus
from app.runs.events import decode_envelope
from app.runs.streams import (
    CONSUMER_GROUP,
    engagement_id_from_inbound,
    inbound_stream,
)
from app.services.processing_receipt import (
    claim,
    complete,
    lock_and_validate_command,
    locked_session,
    record_error,
)
from app.worker.runner import RunRunner
from app.worker.stream_recovery import claim_stale, dead_letter, delivery_count

logger = structlog.get_logger(__name__)

SessionFactory = Callable[[], Session]


class StreamConsumer:
    def __init__(
        self,
        *,
        runner: RunRunner,
        redis_client: Any,
        session_factory: SessionFactory,
        consumer_group: str = CONSUMER_GROUP,
        consumer_name: str | None = None,
        refresh_interval: float = 5.0,
        engagement_ids: list[uuid.UUID] | None = None,
        claim_idle_ms: int = 300_000,
        max_delivery_attempts: int = 5,
        reclaim_count: int = 10,
    ) -> None:
        self._runner = runner
        self._redis = redis_client
        self._session_factory = session_factory
        self._group = consumer_group
        self._consumer = consumer_name or f"worker-{uuid.uuid4().hex[:8]}"
        self._refresh_interval = refresh_interval
        # Tests pass a fixed allow-list so the test thread doesn't pick up
        # messages from other engagements lingering in the DB. Production
        # leaves this None and consumes everything active.
        self._restrict_to: set[uuid.UUID] | None = (
            set(engagement_ids) if engagement_ids is not None else None
        )
        self._known_streams: set[str] = set()
        self._last_refresh = 0.0
        self._claim_idle_ms = claim_idle_ms
        self._max_delivery_attempts = max_delivery_attempts
        self._reclaim_count = reclaim_count

    # ------------------------------------------------------------------
    # Engagement discovery
    # ------------------------------------------------------------------

    def refresh_streams(self) -> set[str]:
        engagement_ids = self._active_engagement_ids()
        streams = {inbound_stream(eid) for eid in engagement_ids}

        for stream in streams - self._known_streams:
            self._ensure_group(stream)

        self._known_streams = streams
        self._last_refresh = time.time()
        return streams

    def _ensure_group(self, stream: str) -> None:
        # ID "0" so the group also sees messages added before group creation —
        # avoids a race where the API/driver xadds before the worker's discovery
        # refresh has had a chance to create the group on a new engagement's
        # stream. New groups have their own delivery checkpoint so this doesn't
        # cause duplicate processing across worker restarts.
        try:
            self._redis.xgroup_create(stream, self._group, id="0", mkstream=True)
            logger.info("worker.group_created", stream=stream, group=self._group)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    def _active_engagement_ids(self) -> list[uuid.UUID]:
        session = self._session_factory()
        try:
            all_active = list(
                session.execute(
                    select(Engagement.id).where(Engagement.status == EngagementStatus.active)
                ).scalars()
            )
        finally:
            session.close()
        if self._restrict_to is None:
            return all_active
        return [eid for eid in all_active if eid in self._restrict_to]

    # ------------------------------------------------------------------
    # Poll loop
    # ------------------------------------------------------------------

    def run_once(self, *, block_ms: int = 1000) -> int:
        if time.time() - self._last_refresh > self._refresh_interval:
            self.refresh_streams()

        if not self._known_streams:
            time.sleep(min(block_ms / 1000.0, 0.5))
            return 0

        reclaimed = self._reclaim_pending()
        if reclaimed:
            return reclaimed

        stream_dict = {s: ">" for s in self._known_streams}
        try:
            response = self._redis.xreadgroup(
                self._group,
                self._consumer,
                stream_dict,
                count=10,
                block=block_ms,
            )
        except ResponseError as exc:
            # A stream we knew about got XDEL'd (commonly: test cleanup, or an
            # engagement was flushed). XREADGROUP fails on the whole batch even
            # if only one stream is missing — easiest recovery is to forget what
            # we know and let the next refresh re-create groups for whatever is
            # still active in the DB.
            if "NOGROUP" in str(exc):
                logger.warning(
                    "worker.nogroup_recovering",
                    error=str(exc),
                )
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
                logger.exception("worker.iteration_failed")
                time.sleep(1.0)

    # ------------------------------------------------------------------
    # Per-message handling
    # ------------------------------------------------------------------

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
        try:
            engagement_id = engagement_id_from_inbound(stream_name)
            envelope = decode_envelope(fields)
            raw_command_id = str(envelope.get("command_id") or "")
            command_id = raw_command_id or f"legacy:{stream_name}:{msg_id}"
            thread_id = str(envelope.get("thread_id") or "")
            if not thread_id:
                raise ValueError("command missing thread_id")
            logger.info(
                "worker.message_received",
                stream=stream_name,
                msg_id=msg_id,
                engagement_id=str(engagement_id),
                envelope_type=envelope.get("type"),
                thread_id=thread_id,
                command_id=command_id,
            )
            lock_key = f"command-thread:{thread_id}"
            with locked_session(self._session_factory, lock_key) as receipt_session:
                receipt = None
                try:
                    receipt, should_process = claim(
                        receipt_session,
                        delivery_id=f"command:{command_id}",
                        kind="command",
                        engagement_id=engagement_id,
                        thread_id=thread_id,
                    )
                    if not should_process:
                        logger.info("worker.duplicate_command_skipped", command_id=command_id)
                    elif (
                        raw_command_id
                        and lock_and_validate_command(receipt_session, command_id) is None
                    ):
                        logger.info(
                            "worker.cancelled_or_stale_command_skipped",
                            command_id=command_id,
                        )
                        complete(receipt_session, receipt)
                    else:
                        self._runner.handle(engagement_id, envelope)
                        complete(receipt_session, receipt)
                except Exception as exc:
                    if receipt is not None:
                        record_error(receipt_session, receipt, exc)
                    raise
        except Exception as exc:
            attempts = delivery_count(
                self._redis,
                stream=stream_name,
                group=self._group,
                message_id=msg_id,
            )
            logger.exception(
                "worker.message_failed",
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
                    engagement_id_from_inbound(stream_name),
                    msg_id=msg_id,
                    error=str(exc),
                    attempts=attempts,
                )
            return

        try:
            self._redis.xack(stream_name, self._group, msg_id)
        except Exception:
            logger.exception(
                "worker.ack_failed",
                stream=stream_name,
                msg_id=msg_id,
            )

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
                    event_type="worker.command_dead_lettered",
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
            logger.exception("worker.dead_letter_audit_failed", msg_id=msg_id)
        finally:
            session.close()
