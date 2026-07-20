"""Transactional, at-least-once Redis command outbox."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.command_outbox import CommandOutbox, CommandOutboxStatus
from app.runs.events import encode_command, encode_event

logger = structlog.get_logger(__name__)
MAX_PUBLISH_ATTEMPTS = 10


def enqueue_command(
    session: Session,
    *,
    idempotency_key: str,
    engagement_id: uuid.UUID,
    stream_name: str,
    payload: dict[str, Any],
    task_id: uuid.UUID | None = None,
) -> CommandOutbox:
    existing = session.execute(
        select(CommandOutbox).where(CommandOutbox.idempotency_key == idempotency_key)
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    envelope = dict(payload)
    envelope["command_id"] = idempotency_key
    row = CommandOutbox(
        idempotency_key=idempotency_key,
        engagement_id=engagement_id,
        task_id=task_id,
        thread_id=str(payload["thread_id"]),
        delivery_kind="command",
        stream_name=stream_name,
        encoded_payload=encode_command(envelope),
        status=CommandOutboxStatus.pending,
    )
    session.add(row)
    session.flush()
    return row


def enqueue_event(
    session: Session,
    *,
    idempotency_key: str,
    engagement_id: uuid.UUID,
    stream_name: str,
    payload: dict[str, Any],
    thread_id: str | None = None,
) -> CommandOutbox:
    """Stage a durable outbound domain event in the caller transaction.

    ``event_id`` and ``feedback_id`` are stable aliases of the logical outbox
    key. Manual/import/MCP producers can reuse this API in the feedback slice.
    """
    existing = session.execute(
        select(CommandOutbox).where(CommandOutbox.idempotency_key == idempotency_key)
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    envelope = dict(payload)
    envelope.setdefault("event_id", idempotency_key)
    envelope.setdefault("feedback_id", idempotency_key)
    row = CommandOutbox(
        idempotency_key=idempotency_key,
        engagement_id=engagement_id,
        task_id=None,
        thread_id=thread_id,
        delivery_kind="event",
        stream_name=stream_name,
        encoded_payload=encode_event(envelope),
        status=CommandOutboxStatus.pending,
    )
    session.add(row)
    session.flush()
    return row


def publish_entry(session: Session, redis_client: Any, entry_id: uuid.UUID) -> bool:
    row = session.execute(
        select(CommandOutbox).where(CommandOutbox.id == entry_id).with_for_update()
    ).scalar_one_or_none()
    if row is None or row.status in {
        CommandOutboxStatus.published,
        CommandOutboxStatus.cancelled,
        CommandOutboxStatus.failed,
    }:
        return True
    now = datetime.now(tz=UTC)
    if row.next_attempt_at and row.next_attempt_at > now:
        session.rollback()
        return False
    row.attempts += 1
    try:
        redis_client.xadd(row.stream_name, dict(row.encoded_payload))
    except Exception as exc:  # noqa: BLE001 - durable retry state
        row.last_error = str(exc)[:2000]
        is_durable_event = row.delivery_kind == "event"
        if not is_durable_event and row.attempts >= MAX_PUBLISH_ATTEMPTS:
            row.status = CommandOutboxStatus.failed
            row.next_attempt_at = None
        else:
            # Domain events are never abandoned: retain a bounded delay but
            # keep retrying until publication succeeds or the row is cancelled.
            row.status = CommandOutboxStatus.pending
            delay = min(300, 2 ** min(row.attempts, 8))
            row.next_attempt_at = now + timedelta(seconds=delay)
        session.commit()
        logger.warning(
            "command_outbox.publish_failed",
            entry_id=str(row.id),
            attempts=row.attempts,
            terminal=row.status == CommandOutboxStatus.failed,
            error=str(exc),
        )
        return False
    row.status = CommandOutboxStatus.published
    row.published_at = now
    row.next_attempt_at = None
    row.last_error = None
    session.commit()
    logger.info("command_outbox.published", entry_id=str(row.id), attempts=row.attempts)
    return True


def publish_pending_batch(session: Session, redis_client: Any, *, limit: int = 50) -> int:
    now = datetime.now(tz=UTC)
    ids = list(
        session.execute(
            select(CommandOutbox.id)
            .where(
                CommandOutbox.status == CommandOutboxStatus.pending,
                or_(
                    CommandOutbox.next_attempt_at.is_(None),
                    CommandOutbox.next_attempt_at <= now,
                ),
            )
            .order_by(CommandOutbox.created_at, CommandOutbox.id)
            .with_for_update(skip_locked=True)
            .limit(limit)
        ).scalars()
    )
    session.commit()
    return sum(bool(publish_entry(session, redis_client, entry_id)) for entry_id in ids)
