"""Durable processing receipts with session-scoped advisory serialization."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from app.models import CommandOutbox, CommandOutboxStatus, Task, TaskStatus
from app.models.processing_receipt import ProcessingReceipt, ProcessingReceiptStatus


@contextmanager
def locked_session(session_factory: Callable[[], Session], key: str) -> Iterator[Session]:
    """Pin a receipt Session and advisory lock to one explicit connection.

    Session commits do not release PostgreSQL session-scoped advisory locks.
    Binding the ORM Session to this exact Connection prevents pool check-in from
    moving later receipt work to another backend before the explicit unlock.
    """
    probe = session_factory()
    try:
        bind = probe.get_bind()
        engine = bind.engine if isinstance(bind, Connection) else bind
    finally:
        probe.close()

    connection = engine.connect()
    session = Session(bind=connection, expire_on_commit=False)
    unlocked: bool | None = None
    try:
        session.execute(
            text("SELECT pg_advisory_lock(hashtextextended(:key, 0))"),
            {"key": key},
        )
        session.commit()
        yield session
    finally:
        try:
            if session.in_transaction():
                session.rollback()
            unlocked = bool(
                session.execute(
                    text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))"),
                    {"key": key},
                ).scalar_one()
            )
            session.commit()
        finally:
            session.close()
            connection.close()
        if unlocked is not True:
            raise RuntimeError(f"failed to release advisory processing lock {key!r}")


def claim(
    session: Session,
    *,
    delivery_id: str,
    kind: str,
    engagement_id: uuid.UUID,
    thread_id: str | None,
) -> tuple[ProcessingReceipt, bool]:
    """Persist processing state; caller must already hold the advisory lock."""
    row = session.get(ProcessingReceipt, delivery_id)
    if row is not None and row.status == ProcessingReceiptStatus.completed:
        return row, False
    now = datetime.now(tz=UTC)
    if row is None:
        row = ProcessingReceipt(
            delivery_id=delivery_id,
            kind=kind,
            engagement_id=engagement_id,
            thread_id=thread_id,
            status=ProcessingReceiptStatus.processing,
            attempts=1,
            started_at=now,
        )
        session.add(row)
    else:
        row.status = ProcessingReceiptStatus.processing
        row.attempts += 1
        row.started_at = now
        row.last_error = None
    session.commit()
    return row, True


def lock_and_validate_command(session: Session, command_id: str) -> CommandOutbox | None:
    """Lock Task then outbox and reject tombstoned/stale command state.

    Locks stay held through handler effects and receipt completion. Cancellation
    uses the same Task -> outbox order, making the winner deterministic.
    """
    probe = session.execute(
        select(CommandOutbox.task_id).where(
            CommandOutbox.idempotency_key == command_id,
            CommandOutbox.delivery_kind == "command",
        )
    ).scalar_one_or_none()
    task = None
    if probe is not None:
        task = session.execute(
            select(Task).where(Task.id == probe).with_for_update()
        ).scalar_one_or_none()
    outbox = session.execute(
        select(CommandOutbox)
        .where(
            CommandOutbox.idempotency_key == command_id,
            CommandOutbox.delivery_kind == "command",
        )
        .with_for_update()
    ).scalar_one_or_none()
    if outbox is None or outbox.status != CommandOutboxStatus.published:
        return None
    if task is not None and task.status not in {TaskStatus.dispatched, TaskStatus.running}:
        return None
    return outbox


def complete(session: Session, receipt: ProcessingReceipt) -> None:
    receipt.status = ProcessingReceiptStatus.completed
    receipt.completed_at = datetime.now(tz=UTC)
    receipt.last_error = None
    session.commit()


def record_error(session: Session, receipt: ProcessingReceipt, error: Exception) -> None:
    delivery_id = receipt.delivery_id
    if session.in_transaction():
        session.rollback()
    current = session.get(ProcessingReceipt, delivery_id)
    if current is None:
        raise RuntimeError(f"processing receipt {delivery_id!r} disappeared")
    current.last_error = str(error)[:2000]
    session.commit()
