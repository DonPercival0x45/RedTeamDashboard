"""Stage 1 MCP lease service: mint / release / extend / sweep / validate.

The lease is the bearer record the MCP server filters every request by;
its lifecycle has to be precise so a redelivered terminal event can't
silently re-open or double-release a surface.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import (
    Engagement,
    EngagementStatus,
    MCPLeaseStatus,
    OwnerEligibility,
    Task,
    TaskKind,
    TaskStatus,
)
from app.services import mcp_lease


@pytest.fixture()
def engagement(db: Session) -> Iterator[Engagement]:
    eng = Engagement(
        name="lease-test",
        slug=f"lease-{uuid.uuid4().hex[:8]}",
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


@pytest.fixture()
def task(db: Session, engagement: Engagement) -> Task:
    t = Task(
        engagement_id=engagement.id,
        title="enum subdomains",
        kind=TaskKind.enum,
        owner_eligibility=OwnerEligibility.agent,
        status=TaskStatus.pending,
        payload={"tool": "subfinder", "target": "acme.test"},
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def test_mint_sets_active_and_records_inputs(db: Session, task: Task) -> None:
    lease = mcp_lease.mint(
        db,
        task=task,
        allowed_tools=["subfinder", "crt_sh"],
        context={"engagement": {"slug": "x"}},
        prompt_keys=["passive_recon"],
        ttl_seconds=600,
    )
    db.commit()
    assert lease.status == MCPLeaseStatus.active.value
    assert lease.allowed_tools == ["subfinder", "crt_sh"]
    assert lease.context == {"engagement": {"slug": "x"}}
    assert lease.prompt_keys == ["passive_recon"]
    assert lease.released_at is None
    assert lease.task_id == task.id
    assert lease.engagement_id == task.engagement_id
    # TTL roughly honored.
    assert (
        timedelta(seconds=590)
        < (lease.expires_at - lease.created_at)
        < timedelta(seconds=610)
    )


def test_release_flips_status_and_stamps_released_at(
    db: Session, task: Task
) -> None:
    lease = mcp_lease.mint(
        db,
        task=task,
        allowed_tools=["subfinder"],
        context={},
        prompt_keys=[],
    )
    db.commit()
    released = mcp_lease.release(db, lease_id=lease.id, reason="run_completed")
    db.commit()
    assert released is not None
    assert released.status == MCPLeaseStatus.released.value
    assert released.released_at is not None


def test_double_release_is_idempotent(db: Session, task: Task) -> None:
    lease = mcp_lease.mint(
        db,
        task=task,
        allowed_tools=["subfinder"],
        context={},
        prompt_keys=[],
    )
    db.commit()
    first = mcp_lease.release(db, lease_id=lease.id, reason="first")
    db.commit()
    first_released_at = first.released_at
    second = mcp_lease.release(db, lease_id=lease.id, reason="redelivery")
    db.commit()
    # Same lease, status unchanged, released_at NOT bumped.
    assert second is not None
    assert second.status == MCPLeaseStatus.released.value
    assert second.released_at == first_released_at


def test_release_unknown_returns_none(db: Session) -> None:
    assert mcp_lease.release(db, lease_id=uuid.uuid4()) is None


def test_sweep_expired_flips_past_due_only(db: Session, task: Task) -> None:
    fresh = mcp_lease.mint(
        db,
        task=task,
        allowed_tools=["subfinder"],
        context={},
        prompt_keys=[],
        ttl_seconds=3600,
    )
    stale = mcp_lease.mint(
        db,
        task=task,
        allowed_tools=["subfinder"],
        context={},
        prompt_keys=[],
        ttl_seconds=10,
    )
    # Force stale into the past.
    stale.expires_at = datetime.now(tz=UTC) - timedelta(seconds=5)
    db.commit()

    swept = mcp_lease.sweep_expired(db)
    db.commit()
    assert swept == 1
    db.refresh(fresh)
    db.refresh(stale)
    assert fresh.status == MCPLeaseStatus.active.value
    assert stale.status == MCPLeaseStatus.expired.value


def test_validate_token_rejects_released_expired_and_unknown(
    db: Session, task: Task
) -> None:
    lease = mcp_lease.mint(
        db,
        task=task,
        allowed_tools=["subfinder"],
        context={},
        prompt_keys=[],
    )
    db.commit()

    # Happy path.
    assert mcp_lease.validate_token(db, str(lease.id)) is not None

    # Malformed.
    assert mcp_lease.validate_token(db, "not-a-uuid") is None

    # Unknown.
    assert mcp_lease.validate_token(db, str(uuid.uuid4())) is None

    # Released → reject.
    mcp_lease.release(db, lease_id=lease.id)
    db.commit()
    assert mcp_lease.validate_token(db, str(lease.id)) is None

    # Now expired (re-mint + push expires into past).
    lease2 = mcp_lease.mint(
        db,
        task=task,
        allowed_tools=["subfinder"],
        context={},
        prompt_keys=[],
    )
    lease2.expires_at = datetime.now(tz=UTC) - timedelta(seconds=1)
    db.commit()
    assert mcp_lease.validate_token(db, str(lease2.id)) is None


def test_find_active_for_task_returns_freshest(db: Session, task: Task) -> None:
    older = mcp_lease.mint(
        db,
        task=task,
        allowed_tools=["subfinder"],
        context={},
        prompt_keys=[],
    )
    db.commit()
    mcp_lease.release(db, lease_id=older.id)
    db.commit()

    newer = mcp_lease.mint(
        db,
        task=task,
        allowed_tools=["crt_sh"],
        context={},
        prompt_keys=[],
    )
    db.commit()
    found = mcp_lease.find_active_for_task(db, task.id)
    assert found is not None
    assert found.id == newer.id


def test_extend_pushes_expiry_only_on_active(
    db: Session, task: Task
) -> None:
    lease = mcp_lease.mint(
        db,
        task=task,
        allowed_tools=["subfinder"],
        context={},
        prompt_keys=[],
        ttl_seconds=60,
    )
    db.commit()
    original_expiry = lease.expires_at
    mcp_lease.extend(db, lease_id=lease.id, additional_seconds=120)
    db.commit()
    assert (lease.expires_at - original_expiry) == timedelta(seconds=120)

    # No-op once released.
    mcp_lease.release(db, lease_id=lease.id)
    db.commit()
    extended_expiry = lease.expires_at
    mcp_lease.extend(db, lease_id=lease.id, additional_seconds=99999)
    db.commit()
    assert lease.expires_at == extended_expiry
