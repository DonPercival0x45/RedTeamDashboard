"""Verify audit_log is genuinely append-only at the DB layer.

These tests are the proof that an operator (or a buggy migration) can't
silently rewrite the history of authorization decisions. They run against the
live compose Postgres.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.models import ActorType, AuditLog, Engagement, EngagementStatus


def _make_audit_row(db: Session, engagement_id: uuid.UUID | None = None) -> AuditLog:
    row = AuditLog(
        engagement_id=engagement_id,
        actor_type=ActorType.system,
        actor_id="test",
        event_type="test.event",
        payload={"hello": "world"},
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_audit_log_blocks_update(db: Session) -> None:
    row = _make_audit_row(db)
    with pytest.raises(DBAPIError) as excinfo:
        db.execute(
            text("UPDATE audit_log SET event_type = 'tampered' WHERE id = :id"),
            {"id": row.id},
        )
        db.commit()
    assert "append-only" in str(excinfo.value).lower()
    db.rollback()


def test_audit_log_blocks_delete(db: Session) -> None:
    row = _make_audit_row(db)
    with pytest.raises(DBAPIError) as excinfo:
        db.execute(text("DELETE FROM audit_log WHERE id = :id"), {"id": row.id})
        db.commit()
    assert "append-only" in str(excinfo.value).lower()
    db.rollback()


def test_flush_engagement_bypasses_trigger(db: Session) -> None:
    eng = Engagement(
        name="flush-test",
        slug=f"flush-test-{uuid.uuid4().hex[:8]}",
        status=EngagementStatus.active,
    )
    db.add(eng)
    db.commit()
    db.refresh(eng)

    _make_audit_row(db, engagement_id=eng.id)
    _make_audit_row(db, engagement_id=eng.id)

    db.execute(text("SELECT flush_engagement(:id)"), {"id": eng.id})
    db.commit()

    remaining = db.execute(
        select(AuditLog).where(AuditLog.engagement_id == eng.id)
    ).all()
    assert remaining == []

    engagement_gone = db.execute(
        select(Engagement).where(Engagement.id == eng.id)
    ).first()
    assert engagement_gone is None
