"""Transactional publication of canonical Strategic finding feedback.

Producers stage one outbound domain event in the same SQL transaction as the
finding mutation. Redis delivery is at-least-once through the shared outbox;
Strategic's durable processing receipt makes replay safe.
"""
from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable, Mapping
from typing import Any

import structlog
from sqlalchemy.orm import Session

from app.models import Finding
from app.models.command_outbox import CommandOutbox
from app.runs.streams import outbound_stream
from app.services.command_outbox import enqueue_event, publish_entry

logger = structlog.get_logger(__name__)


def _idempotency_key(*, source: str, operation_id: str, finding_id: uuid.UUID) -> str:
    raw = f"finding-feedback:{source}:{operation_id}:{finding_id}"
    if len(raw) <= 255:
        return raw
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"finding-feedback:{source[:80]}:{digest}"


def stage_finding_feedback(
    session: Session,
    *,
    finding: Finding,
    acting_user_id: uuid.UUID | str,
    operation_id: uuid.UUID | str,
    source: str,
    event_type: str = "finding.created",
    thread_id: uuid.UUID | str | None = None,
    tool: str | None = None,
    args: Mapping[str, Any] | None = None,
    data: Mapping[str, Any] | None = None,
) -> CommandOutbox:
    """Stage one canonical feedback event in the caller's transaction.

    ``operation_id`` names the durable producer operation (worker thread,
    import request, chat action, MCP finding, or tool invocation). Combining it
    with the canonical finding id collapses repeated items in one operation but
    allows a later operation that enriches a grouped parent to trigger a fresh
    assessment.
    """
    actor = str(acting_user_id)
    operation = str(operation_id)
    if not actor:
        raise ValueError("acting_user_id is required for finding feedback")
    if not operation:
        raise ValueError("operation_id is required for finding feedback")
    if event_type not in {"finding.created", "finding.updated"}:
        raise ValueError(f"unsupported finding feedback event: {event_type}")

    session.flush()
    key = _idempotency_key(
        source=source,
        operation_id=operation,
        finding_id=finding.id,
    )
    payload = {
        "type": event_type,
        "thread_id": str(thread_id) if thread_id is not None else None,
        "tool": tool or finding.source_tool,
        "args": dict(args or {}),
        "data": dict(data or {}),
        "target": finding.target,
        "severity": finding.severity.value,
        "title": finding.title,
        "finding_id": str(finding.id),
        "phase": finding.phase.value,
        "status": finding.status.value,
        "acting_user_id": actor,
        "source": source,
        "operation_id": operation,
    }
    return enqueue_event(
        session,
        idempotency_key=key,
        engagement_id=finding.engagement_id,
        stream_name=outbound_stream(finding.engagement_id),
        payload=payload,
        thread_id=str(thread_id) if thread_id is not None else None,
    )


def publish_feedback_entries(
    session: Session,
    redis_client: Any,
    entries: Iterable[CommandOutbox | uuid.UUID],
) -> None:
    """Best-effort low-latency publish after commit; the relay owns retries."""
    for entry in entries:
        entry_id = entry.id if isinstance(entry, CommandOutbox) else entry
        try:
            publish_entry(session, redis_client, entry_id)
        except Exception:  # noqa: BLE001 - committed outbox remains relay-retryable
            session.rollback()
            logger.exception("finding_feedback.immediate_publish_failed", entry_id=str(entry_id))
