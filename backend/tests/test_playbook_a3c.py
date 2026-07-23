"""A3c tests — async playbook queue (enqueue + claim + execute + cancel).

Covers:

- ``enqueue_run`` creates a pending row with steps_total pre-populated.
- ``claim_next_pending`` returns the oldest pending row + flips it to
  running in the same tx.
- Second concurrent claimer sees nothing when a row is locked (SKIP LOCKED
  isolation — tested via two separate sessions).
- ``execute_pending_run`` drives a claimed row to terminal + emits milestone.
- Cancel-pending → cancelled + execute_pending_run treats it as no-op.
- Cancel-mid-run stops the runner between steps; final status=cancelled.
- Terminal cancel raises RunNotCancellableError.
- Worker thread: run_once drains one pending row + is idle when queue empty.
- HTTP: POST returns 202 pending, cancel endpoint 200 / 404 / 409.
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.main import app
from app.models import (
    ActorType,
    AuditLog,
    CommandOutbox,
    Engagement,
    EngagementStatus,
    EngagementWorkState,
    PlaybookRun,
    PlaybookRunStatus,
    User,
    UserRole,
)
from app.services import methodology as meth
from app.services.playbook import (
    RunNotCancellableError,
    cancel_run,
    catalog,
    claim_next_pending,
    enqueue_run,
    execute_pending_run,
    load_seed_playbooks,
)
from app.services.playbook.executor import StepResult
from app.worker.playbook_worker import PlaybookWorkerThread


class MockExecutor:
    def __init__(self, default: StepResult | None = None) -> None:
        self.default = default or StepResult(ok=True, findings_total=1, findings_new=1)
        self.calls: list[str] = []

    def run_step(self, *, tool_slug, args_template, scope_context) -> StepResult:
        self.calls.append(f"{tool_slug}:{scope_context}")
        return self.default


class SlowExecutor:
    """Executor that sleeps briefly per step. Used to test mid-run cancel —
    we cancel the row from another thread while the runner is between
    steps."""

    def __init__(self, delay_seconds: float = 0.05) -> None:
        self.delay = delay_seconds
        self.step_count = 0

    def run_step(self, *, tool_slug, args_template, scope_context) -> StepResult:
        time.sleep(self.delay)
        self.step_count += 1
        return StepResult(ok=True, findings_total=1)


@pytest.fixture()
def user(db: Session) -> User:
    u = User(
        id=uuid.uuid4(),
        email=f"a3c-{uuid.uuid4().hex[:6]}@example.com",
        display_name="A3c Tester",
        role=UserRole.user,
        is_active=True,
    )
    db.add(u)
    db.commit()
    return u


@pytest.fixture(autouse=True)
def _cleanup_queue():
    """A3c tests commit rows to Postgres so cross-session behavior (worker
    thread, SKIP LOCKED) actually works. The default ``db`` fixture rolls
    back its own transaction but committed rows survive, so we scrub
    ``playbook_runs`` before each test to guarantee a clean queue.

    Cleanup only runs BEFORE the test — teardown-time cleanup would race
    the ``db`` fixture's post-test rollback (LIFO order runs this teardown
    first while ``db`` still holds row locks from ``claim_next_pending``).
    """
    from sqlalchemy import delete

    s = SessionLocal()
    try:
        s.execute(delete(PlaybookRun))
        s.commit()
    finally:
        s.close()
    yield


@pytest.fixture()
def engagement(db: Session) -> Engagement:
    eng = Engagement(
        name="A3c Test",
        slug=f"a3c-{uuid.uuid4().hex[:8]}",
        status=EngagementStatus.active,
        work_state=EngagementWorkState.active,
    )
    db.add(eng)
    db.flush()
    meth.load_seed_catalog(db)
    meth.select_for_engagement(
        db, engagement_id=eng.id, slug="osint-minimal",
        now=datetime(2026, 7, 23, tzinfo=UTC),
    )
    db.commit()
    return eng


@pytest.fixture()
def playbook(db: Session):
    load_seed_playbooks(db)
    db.commit()
    pb = catalog.get_by_slug(db, "osint-passive-domain")
    assert pb is not None
    return pb


# ---------------------------------------------------------------------------
# enqueue_run
# ---------------------------------------------------------------------------


def test_enqueue_creates_pending_row_with_step_total(
    db: Session, engagement: Engagement, playbook
) -> None:
    run = enqueue_run(
        db,
        engagement=engagement,
        playbook=playbook,
        scope_subset=["a.com", "b.com"],
    )
    db.flush()
    assert run.status is PlaybookRunStatus.pending
    assert run.started_at is None
    assert run.completed_at is None
    # 5 steps × 2 scope items = 10.
    assert run.steps_total == 10
    assert run.steps_succeeded == 0
    assert run.steps_failed == 0


# ---------------------------------------------------------------------------
# claim_next_pending — happy path + FIFO
# ---------------------------------------------------------------------------


def test_claim_flips_pending_to_running(
    db: Session, engagement: Engagement, playbook
) -> None:
    enqueue_run(
        db, engagement=engagement, playbook=playbook, scope_subset=["a.com"],
    )
    db.commit()
    claimed = claim_next_pending(db)
    assert claimed is not None
    assert claimed.status is PlaybookRunStatus.running
    assert claimed.started_at is not None


def test_claim_returns_none_when_queue_empty(db: Session) -> None:
    assert claim_next_pending(db) is None


def test_claim_returns_oldest_first(
    db: Session, engagement: Engagement, playbook
) -> None:
    first = enqueue_run(
        db,
        engagement=engagement,
        playbook=playbook,
        scope_subset=["a.com"],
        now=datetime(2026, 7, 23, 10, 0, tzinfo=UTC),
    )
    second = enqueue_run(
        db,
        engagement=engagement,
        playbook=playbook,
        scope_subset=["b.com"],
        now=datetime(2026, 7, 23, 11, 0, tzinfo=UTC),
    )
    db.commit()
    claimed_first = claim_next_pending(db)
    db.commit()
    claimed_second = claim_next_pending(db)
    db.commit()
    assert claimed_first.id == first.id
    assert claimed_second.id == second.id


# ---------------------------------------------------------------------------
# SKIP LOCKED — two sessions can't grab the same row
# ---------------------------------------------------------------------------


def test_skip_locked_isolates_concurrent_claimers(
    db: Session, engagement: Engagement, playbook
) -> None:
    """Two sessions racing to claim the same pending row: one gets it, the
    other's SKIP LOCKED means it sees nothing (rather than blocking or
    duplicating the claim).

    Both sessions must be on distinct connections + in explicit
    transactions — SQLAlchemy's autobegin lazily starts a txn on first
    statement, but the FOR UPDATE lock is only held for the duration of
    the txn that acquires it, so we hold s1's txn open explicitly across
    the s2 claim.
    """
    enqueue_run(
        db, engagement=engagement, playbook=playbook, scope_subset=["a.com"],
    )
    db.commit()

    s1 = SessionLocal()
    s2 = SessionLocal()
    try:
        run_a = claim_next_pending(s1)  # locks the row inside s1's txn
        # Explicit no-commit: the row lock must still be held when s2 tries.
        run_b = claim_next_pending(s2)
        assert run_a is not None
        assert run_b is None
    finally:
        s1.rollback()
        s2.rollback()
        s1.close()
        s2.close()


# ---------------------------------------------------------------------------
# execute_pending_run
# ---------------------------------------------------------------------------


def test_execute_drives_claimed_run_to_completed(
    db: Session, engagement: Engagement, playbook, user: User
) -> None:
    run = enqueue_run(
        db,
        engagement=engagement,
        playbook=playbook,
        scope_subset=["foo.com"],
        requested_by=user.id,
    )
    db.commit()
    claim_next_pending(db)
    db.commit()
    ex = MockExecutor()
    result = execute_pending_run(db, run_id=run.id, executor=ex)
    db.commit()
    assert result.status is PlaybookRunStatus.completed
    assert result.steps_succeeded == 5
    assert result.completed_at is not None
    # Milestone landed.
    entry = db.execute(
        select(CommandOutbox).where(
            CommandOutbox.idempotency_key == f"collection.job.completed:{run.id}"
        )
    ).scalar_one_or_none()
    assert entry is not None
    envelope = json.loads(entry.encoded_payload["data"])
    assert envelope["acting_user_id"] == str(user.id)
    coverage_audits = db.execute(
        select(AuditLog).where(
            AuditLog.engagement_id == engagement.id,
            AuditLog.event_type == "coverage.recorded",
        )
    ).scalars().all()
    assert coverage_audits
    assert all(row.actor_type is ActorType.user for row in coverage_audits)
    assert {row.actor_id for row in coverage_audits} == {str(user.id)}


def test_execute_bails_on_cancelled_pending(
    db: Session, engagement: Engagement, playbook
) -> None:
    run = enqueue_run(
        db, engagement=engagement, playbook=playbook, scope_subset=["foo.com"],
    )
    db.commit()
    cancel_run(db, run_id=run.id, reason="analyst changed mind")
    db.commit()
    ex = MockExecutor()
    execute_pending_run(db, run_id=run.id, executor=ex)
    db.commit()
    db.refresh(run)
    assert run.status is PlaybookRunStatus.cancelled
    # No steps ran.
    assert ex.calls == []


# ---------------------------------------------------------------------------
# cancel_run
# ---------------------------------------------------------------------------


def test_cancel_pending_flips_status(
    db: Session, engagement: Engagement, playbook
) -> None:
    run = enqueue_run(
        db, engagement=engagement, playbook=playbook, scope_subset=["a.com"],
    )
    db.commit()
    cancel_run(db, run_id=run.id)
    db.commit()
    db.refresh(run)
    assert run.status is PlaybookRunStatus.cancelled
    assert run.completed_at is not None


def test_cancel_terminal_raises(
    db: Session, engagement: Engagement, playbook
) -> None:
    run = enqueue_run(
        db, engagement=engagement, playbook=playbook, scope_subset=["a.com"],
    )
    db.commit()
    claim_next_pending(db)
    db.commit()
    execute_pending_run(db, run_id=run.id, executor=MockExecutor())
    db.commit()
    # Now completed → cancel must 409.
    with pytest.raises(RunNotCancellableError):
        cancel_run(db, run_id=run.id)


def test_cancel_second_call_is_idempotent(
    db: Session, engagement: Engagement, playbook
) -> None:
    run = enqueue_run(
        db, engagement=engagement, playbook=playbook, scope_subset=["a.com"],
    )
    db.commit()
    first_ts = datetime.now(tz=UTC)
    cancel_run(db, run_id=run.id, now=first_ts)
    db.commit()
    cancel_run(db, run_id=run.id, now=first_ts + timedelta(hours=1))
    db.commit()
    db.refresh(run)
    assert run.status is PlaybookRunStatus.cancelled
    # completed_at was stamped by the FIRST cancel; second call is no-op.
    assert run.completed_at == first_ts


def test_cancel_unknown_raises_keyerror(db: Session) -> None:
    with pytest.raises(KeyError):
        cancel_run(db, run_id=uuid.uuid4())


# ---------------------------------------------------------------------------
# Worker thread
# ---------------------------------------------------------------------------


def test_worker_run_once_drains_one_run(
    db: Session, engagement: Engagement, playbook
) -> None:
    enqueue_run(
        db, engagement=engagement, playbook=playbook, scope_subset=["foo.com"],
    )
    db.commit()
    worker = PlaybookWorkerThread(session_factory=SessionLocal)
    did_work = worker.run_once()
    assert did_work is True
    # Re-read from a fresh session to see the committed state.
    s = SessionLocal()
    try:
        row = s.execute(
            select(PlaybookRun).where(
                PlaybookRun.engagement_id == engagement.id
            )
        ).scalar_one()
        assert row.status is not PlaybookRunStatus.pending
        assert row.status is not PlaybookRunStatus.running
        assert row.completed_at is not None
    finally:
        s.close()


def test_worker_run_once_idle_when_queue_empty() -> None:
    worker = PlaybookWorkerThread(session_factory=SessionLocal)
    assert worker.run_once() is False


@pytest.fixture()
def guest(db: Session) -> User:
    u = User(
        id=uuid.uuid4(),
        email=f"a3c-guest-{uuid.uuid4().hex[:6]}@example.com",
        display_name="A3c Guest",
        role=UserRole.guest,
        is_active=True,
    )
    db.add(u)
    db.commit()
    return u


# ---------------------------------------------------------------------------
# HTTP surface — 202 + cancel endpoint
# ---------------------------------------------------------------------------


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _headers(u: User) -> dict[str, str]:
    return {"X-User-Id": u.email}


def test_execute_without_requester_omits_acting_user(
    db: Session, engagement: Engagement, playbook
) -> None:
    """Pre-A8 (legacy) runs with no requester/approver keep the system fallback.

    The worker must never synthesize an analyst identity it cannot prove.
    """
    run = enqueue_run(
        db,
        engagement=engagement,
        playbook=playbook,
        scope_subset=["foo.com"],
    )
    db.commit()
    claim_next_pending(db)
    db.commit()
    execute_pending_run(db, run_id=run.id, executor=MockExecutor())
    db.commit()
    entry = db.execute(
        select(CommandOutbox).where(
            CommandOutbox.idempotency_key == f"collection.job.completed:{run.id}"
        )
    ).scalar_one()
    envelope = json.loads(entry.encoded_payload["data"])
    assert "acting_user_id" not in envelope


def test_post_returns_202_with_pending_row(
    db: Session,
    client: TestClient,
    user: User,
    engagement: Engagement,
    playbook,
) -> None:
    resp = client.post(
        f"/engagements/{engagement.slug}/playbook-runs",
        headers=_headers(user),
        json={"playbook_slug": "osint-passive-domain", "scope_subset": ["foo.example"]},
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == PlaybookRunStatus.pending.value
    assert body["started_at"] is None
    assert body["requested_by"] == str(user.id)


def test_cancel_endpoint_transitions_pending_to_cancelled(
    db: Session,
    client: TestClient,
    user: User,
    engagement: Engagement,
    playbook,
) -> None:
    post = client.post(
        f"/engagements/{engagement.slug}/playbook-runs",
        headers=_headers(user),
        json={"playbook_slug": "osint-passive-domain", "scope_subset": ["foo.example"]},
    )
    run_id = post.json()["id"]
    cancel = client.post(f"/playbook-runs/{run_id}/cancel", headers=_headers(user))
    assert cancel.status_code == 200
    assert cancel.json()["status"] == PlaybookRunStatus.cancelled.value


def test_cancel_endpoint_terminal_run_409(
    db: Session,
    client: TestClient,
    user: User,
    engagement: Engagement,
    playbook,
) -> None:
    # Enqueue + drain through the worker → terminal.
    run = enqueue_run(
        db, engagement=engagement, playbook=playbook, scope_subset=["foo.example"],
    )
    db.commit()
    worker = PlaybookWorkerThread(session_factory=SessionLocal)
    worker.run_once()
    cancel = client.post(f"/playbook-runs/{run.id}/cancel", headers=_headers(user))
    assert cancel.status_code == 409


def test_cancel_endpoint_unknown_run_404(
    client: TestClient, user: User
) -> None:
    resp = client.post(f"/playbook-runs/{uuid.uuid4()}/cancel", headers=_headers(user))
    assert resp.status_code == 404


def test_cancel_endpoint_guest_blocked(
    db: Session,
    client: TestClient,
    guest: User,
    user: User,
    engagement: Engagement,
    playbook,
) -> None:
    post = client.post(
        f"/engagements/{engagement.slug}/playbook-runs",
        headers=_headers(user),
        json={"playbook_slug": "osint-passive-domain", "scope_subset": ["foo.example"]},
    )
    run_id = post.json()["id"]
    resp = client.post(f"/playbook-runs/{run_id}/cancel", headers=_headers(guest))
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Mid-run cancel — runner bails between steps
# ---------------------------------------------------------------------------


def test_mid_run_cancel_uses_fresh_session_per_check(
    db: Session, engagement: Engagement, playbook
) -> None:
    """A cancel_run committed by another session must be visible when the
    runner reads status inside its execution loop.

    Rather than testing via thread timing (flaky in a mixed-connection
    test setup), we verify the mechanism directly: commit a cancel from a
    second session, then ask the runner to execute — it should see the
    cancel on its first per-step status check and bail without running any
    step.
    """
    run = enqueue_run(
        db, engagement=engagement, playbook=playbook, scope_subset=["a"],
    )
    db.commit()
    claim_next_pending(db)
    db.commit()
    # Cancel via a different session before execute_pending_run runs.
    s = SessionLocal()
    try:
        cancel_run(s, run_id=run.id, reason="mid-run cancel")
        s.commit()
    finally:
        s.close()

    ex = SlowExecutor(delay_seconds=0.0)
    execute_pending_run(db, run_id=run.id, executor=ex)
    db.commit()
    db.refresh(run)
    assert run.status is PlaybookRunStatus.cancelled
    # Runner saw the cancel and bailed — no steps ran.
    assert ex.step_count == 0
