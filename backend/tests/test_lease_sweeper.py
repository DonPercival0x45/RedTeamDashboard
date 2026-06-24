"""LeaseSweeperThread — Stage 3+1.5.

Verifies the daemon thread that periodically flips expired active
leases (``status='active' AND expires_at < now()``) to
``status='expired'`` for clean accounting. The per-request
``validate_token`` already rejects expired leases at the MCP server,
so failure of this sweeper is not a security event.
"""
from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import Engagement, EngagementStatus, MCPLease, MCPLeaseStatus
from app.worker.lease_sweeper import LeaseSweeperThread


@pytest.fixture()
def engagement(db: Session) -> Iterator[Engagement]:
    eng = Engagement(
        name="sweeper-test",
        slug=f"sweep-{uuid.uuid4().hex[:8]}",
        status=EngagementStatus.active,
    )
    db.add(eng)
    db.commit()
    db.refresh(eng)
    try:
        yield eng
    finally:
        db.execute(text("SELECT flush_engagement(:id)"), {"id": eng.id})
        db.commit()


def _make_lease(
    db: Session, engagement: Engagement, *, expires_in_seconds: int
) -> MCPLease:
    """Insert an active lease whose expires_at is `expires_in_seconds`
    relative to now. Negative values produce already-expired leases."""
    now = datetime.now(tz=UTC)
    lease = MCPLease(
        task_id=None,
        engagement_id=engagement.id,
        allowed_tools=[],
        context={},
        prompt_keys=[],
        status=MCPLeaseStatus.active.value,
        created_at=now,
        expires_at=now + timedelta(seconds=expires_in_seconds),
    )
    db.add(lease)
    db.commit()
    db.refresh(lease)
    return lease


def test_run_once_flips_expired_lease(
    db: Session, engagement: Engagement
) -> None:
    """The classic case: one active lease past expires_at → flipped."""
    lease = _make_lease(db, engagement, expires_in_seconds=-60)

    sweeper = LeaseSweeperThread(
        session_factory=lambda: db, interval_seconds=300
    )
    swept = sweeper.run_once()
    assert swept == 1

    fresh = db.execute(
        select(MCPLease).where(MCPLease.id == lease.id)
    ).scalar_one()
    assert fresh.status == MCPLeaseStatus.expired.value


def test_run_once_returns_zero_when_nothing_expired(
    db: Session, engagement: Engagement
) -> None:
    """An active lease still inside its TTL is left alone."""
    lease = _make_lease(db, engagement, expires_in_seconds=3600)

    sweeper = LeaseSweeperThread(
        session_factory=lambda: db, interval_seconds=300
    )
    swept = sweeper.run_once()
    assert swept == 0

    fresh = db.execute(
        select(MCPLease).where(MCPLease.id == lease.id)
    ).scalar_one()
    assert fresh.status == MCPLeaseStatus.active.value


def test_run_once_swallows_db_errors() -> None:
    """A transient DB blip mustn't kill the sweeper thread — caller logs
    the exception, the next tick gets a fresh shot."""

    def _bad_factory() -> Session:
        raise RuntimeError("simulated DB outage")

    sweeper = LeaseSweeperThread(
        session_factory=_bad_factory, interval_seconds=300
    )
    # Should NOT raise; returns 0 swept.
    assert sweeper.run_once() == 0


def test_run_forever_exits_promptly_on_stop_event(
    db: Session, engagement: Engagement
) -> None:
    """``stop_event.wait(interval)`` short-circuits the sleep so SIGTERM
    breaks out within sub-second instead of waiting the full interval.
    Smoke test: start with a tiny interval, set stop, expect quick exit."""
    _make_lease(db, engagement, expires_in_seconds=-60)

    sweeper = LeaseSweeperThread(
        session_factory=lambda: db, interval_seconds=0.05
    )
    stop_event = threading.Event()
    thread = threading.Thread(
        target=sweeper.run_forever,
        args=(stop_event,),
        name="sweeper-test",
        daemon=True,
    )
    thread.start()

    # Let it run a couple of cycles, then stop.
    time.sleep(0.2)
    stop_event.set()
    thread.join(timeout=2.0)
    assert not thread.is_alive(), "sweeper thread did not exit on stop_event"
