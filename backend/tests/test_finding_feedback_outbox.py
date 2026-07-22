from __future__ import annotations

import json
import uuid

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import Engagement, Finding, FindingPhase, FindingStatus, Severity
from app.models.command_outbox import CommandOutbox, CommandOutboxStatus
from app.services.finding_feedback import (
    publish_feedback_entries,
    stage_finding_feedback,
)


class _Redis:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.messages: list[tuple[str, dict]] = []

    def xadd(self, stream: str, payload: dict) -> str:
        if self.fail:
            raise ConnectionError("redis unavailable")
        self.messages.append((stream, payload))
        return "1-0"


def test_feedback_is_transactional_idempotent_and_relay_retryable(db: Session) -> None:
    engagement = Engagement(name="Feedback outbox", slug=f"feedback-{uuid.uuid4().hex[:8]}")
    db.add(engagement)
    db.flush()
    finding = Finding(
        engagement_id=engagement.id,
        title="Canonical parent",
        severity=Severity.medium,
        phase=FindingPhase.general,
        status=FindingStatus.pending_validation,
        source_tool="manual",
        details={},
    )
    db.add(finding)
    db.flush()
    actor = uuid.uuid4()
    operation = uuid.uuid4()
    first = stage_finding_feedback(
        db,
        finding=finding,
        acting_user_id=actor,
        operation_id=operation,
        source="manual",
    )
    duplicate = stage_finding_feedback(
        db,
        finding=finding,
        acting_user_id=actor,
        operation_id=operation,
        source="manual",
    )
    assert duplicate.id == first.id
    db.commit()

    publish_feedback_entries(db, _Redis(fail=True), [first.id])
    row = db.get(CommandOutbox, first.id)
    assert row is not None
    assert row.status == CommandOutboxStatus.pending
    assert row.attempts == 1

    row.next_attempt_at = None
    db.commit()
    redis = _Redis()
    publish_feedback_entries(db, redis, [first.id])
    db.refresh(row)
    assert row.status == CommandOutboxStatus.published
    assert len(redis.messages) == 1
    envelope = json.loads(redis.messages[0][1]["data"])
    assert envelope["finding_id"] == str(finding.id)
    assert envelope["operation_id"] == str(operation)

    assert db.execute(
        select(CommandOutbox).where(CommandOutbox.idempotency_key == first.idempotency_key)
    ).scalars().all() == [row]
    db.execute(text("SELECT flush_engagement(:id)"), {"id": engagement.id})
    db.commit()
