"""Redis Streams consumer loop.

Discovery: at refresh intervals the consumer queries the DB for active
Project IDs, builds the set of inbound stream names, and ensures the
consumer group exists on each (idempotent ``XGROUP CREATE MKSTREAM``).

Polling: a single blocking ``XREADGROUP`` across all known streams. Each
message is decoded into an envelope, handed to ``RunRunner.handle``, and
acked unconditionally — Phase 0 has no dead-letter queue, so we log poison
messages and move on instead of letting them redeliver forever.

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

from app.models import Project, ProjectStatus
from app.runs.events import decode_envelope
from app.runs.streams import (
    CONSUMER_GROUP,
    engagement_id_from_inbound,
    inbound_stream,
)
from app.worker.runner import RunRunner

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

    # ------------------------------------------------------------------
    # Project discovery
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
        # refresh has had a chance to create the group on a new Project's
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
                    select(Project.id).where(
                        Project.status == ProjectStatus.active
                    )
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
            # Project was flushed). XREADGROUP fails on the whole batch even
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

    def _process_one(
        self,
        stream_name: str,
        msg_id: str,
        fields: dict[str, Any],
    ) -> None:
        try:
            project_id = engagement_id_from_inbound(stream_name)
            envelope = decode_envelope(fields)
            logger.info(
                "worker.message_received",
                stream=stream_name,
                msg_id=msg_id,
                project_id=str(project_id),
                envelope_type=envelope.get("type"),
                thread_id=envelope.get("thread_id"),
            )
            self._runner.handle(project_id, envelope)
        except Exception:
            logger.exception(
                "worker.message_failed",
                stream=stream_name,
                msg_id=msg_id,
            )
        finally:
            try:
                self._redis.xack(stream_name, self._group, msg_id)
            except Exception:
                logger.exception(
                    "worker.ack_failed",
                    stream=stream_name,
                    msg_id=msg_id,
                )
