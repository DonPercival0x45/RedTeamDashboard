"""A5 tests — approve-before-run gate.

Covers:

- ``enqueue_run`` against an active playbook creates a run in
  ``awaiting_approval`` (not ``pending``); inactive stays ``pending``.
- Worker's ``claim_next_pending`` skips awaiting rows.
- ``approve_run`` flips to pending + stamps approver + timestamp; second
  call is a no-op (idempotent); non-awaiting → RunNotAwaitingApprovalError.
- ``reject_run`` flips to cancelled with rejection_reason + stamps rejecter;
  requires reason.
- HTTP: POST approve returns 200 + attribution fields, 404 unknown, 409
  terminal, 403 guest. POST reject: 422 empty reason, 200 happy, 409
  terminal.
- List endpoint honors ``?status=awaiting_approval`` filter for the
  approval queue view.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.main import app
from app.models import (
    Engagement,
    EngagementStatus,
    EngagementWorkState,
    Playbook,
    PlaybookRun,
    PlaybookRunStatus,
    User,
    UserRole,
)
from app.services import methodology as meth
from app.services.playbook import (
    RunNotAwaitingApprovalError,
    approve_run,
    catalog,
    claim_next_pending,
    enqueue_run,
    load_seed_playbooks,
    reject_run,
)


@pytest.fixture(autouse=True)
def _cleanup_queue():
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
        name="A5 Test",
        slug=f"a5-{uuid.uuid4().hex[:8]}",
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
def active_playbook(db: Session) -> Playbook:
    """Bespoke gated playbook — the seeded ones are all inactive so A3b/A3c
    tests keep working. This fixture creates a minimal 2-step playbook
    marked ``active=True``."""
    from app.models import PlaybookStep

    pb = Playbook(
        slug=f"gated-{uuid.uuid4().hex[:6]}",
        version=1,
        name="Gated test playbook",
        description="Needs approval before the worker executes.",
        applies_to_asset_class="domain",
        active=True,
    )
    db.add(pb)
    db.flush()
    db.add(
        PlaybookStep(
            playbook_id=pb.id,
            sort_order=10,
            tool_slug="whois",
            args_template={"domain": "{{scope_item}}"},
            satisfies_node_ids=[],
        )
    )
    db.commit()
    return pb


@pytest.fixture()
def inactive_playbook(db: Session) -> Playbook:
    load_seed_playbooks(db)
    db.commit()
    pb = catalog.get_by_slug(db, "osint-passive-domain")
    assert pb is not None
    assert pb.active is False
    return pb


# ---------------------------------------------------------------------------
# enqueue_run status branching
# ---------------------------------------------------------------------------


def test_enqueue_active_playbook_yields_awaiting_approval(
    db: Session, engagement: Engagement, active_playbook: Playbook
) -> None:
    run = enqueue_run(
        db, engagement=engagement, playbook=active_playbook, scope_subset=["foo.com"],
    )
    db.flush()
    assert run.status is PlaybookRunStatus.awaiting_approval
    assert run.approved_by is None
    assert run.approved_at is None


def test_enqueue_inactive_playbook_yields_pending(
    db: Session, engagement: Engagement, inactive_playbook: Playbook
) -> None:
    run = enqueue_run(
        db, engagement=engagement, playbook=inactive_playbook, scope_subset=["foo.com"],
    )
    db.flush()
    assert run.status is PlaybookRunStatus.pending


# ---------------------------------------------------------------------------
# Worker skips awaiting_approval
# ---------------------------------------------------------------------------


def test_worker_claim_skips_awaiting_approval(
    db: Session, engagement: Engagement, active_playbook: Playbook
) -> None:
    """``claim_next_pending`` only sees ``status='pending'`` rows so a
    gated run sits until an analyst approves it."""
    enqueue_run(
        db, engagement=engagement, playbook=active_playbook, scope_subset=["foo.com"],
    )
    db.commit()
    assert claim_next_pending(db) is None


# ---------------------------------------------------------------------------
# approve_run
# ---------------------------------------------------------------------------


@pytest.fixture()
def approver(db: Session) -> User:
    u = User(
        id=uuid.uuid4(),
        email=f"approver-{uuid.uuid4().hex[:6]}@example.com",
        display_name="Approver",
        role=UserRole.user,
        is_active=True,
    )
    db.add(u)
    db.commit()
    return u


def test_approve_flips_to_pending_and_stamps_attribution(
    db: Session, engagement: Engagement, active_playbook: Playbook, approver: User
) -> None:
    run = enqueue_run(
        db, engagement=engagement, playbook=active_playbook, scope_subset=["foo.com"],
    )
    db.commit()
    result = approve_run(
        db, run_id=run.id, approver_id=approver.id,
        reason="analyst signed off",
        now=datetime(2026, 7, 23, tzinfo=UTC),
    )
    db.commit()
    assert result.status is PlaybookRunStatus.pending
    assert result.approved_by == approver.id
    assert result.approved_at == datetime(2026, 7, 23, tzinfo=UTC)
    assert result.approval_reason == "analyst signed off"


def test_approve_is_idempotent_after_first_call(
    db: Session, engagement: Engagement, active_playbook: Playbook, approver: User
) -> None:
    """A second approve on an already-approved run is a no-op — doesn't
    re-stamp timestamps, doesn't error."""
    run = enqueue_run(
        db, engagement=engagement, playbook=active_playbook, scope_subset=["foo.com"],
    )
    db.commit()
    first_ts = datetime(2026, 7, 23, tzinfo=UTC)
    approve_run(db, run_id=run.id, approver_id=approver.id, now=first_ts)
    db.commit()
    approve_run(
        db, run_id=run.id, approver_id=approver.id,
        now=datetime(2026, 7, 24, tzinfo=UTC),  # different time
    )
    db.commit()
    db.refresh(run)
    assert run.approved_at == first_ts


def test_approve_non_awaiting_raises(
    db: Session, engagement: Engagement, inactive_playbook: Playbook, approver: User
) -> None:
    """A pending run (not awaiting) can't be approved — it's already
    claimable."""
    run = enqueue_run(
        db, engagement=engagement, playbook=inactive_playbook,
        scope_subset=["foo.com"],
    )
    db.commit()
    with pytest.raises(RunNotAwaitingApprovalError):
        approve_run(db, run_id=run.id, approver_id=approver.id)


def test_approve_unknown_raises_keyerror(
    db: Session, approver: User
) -> None:
    with pytest.raises(KeyError):
        approve_run(db, run_id=uuid.uuid4(), approver_id=approver.id)


# ---------------------------------------------------------------------------
# reject_run
# ---------------------------------------------------------------------------


def test_reject_flips_to_cancelled_and_stamps_attribution(
    db: Session, engagement: Engagement, active_playbook: Playbook, approver: User
) -> None:
    run = enqueue_run(
        db, engagement=engagement, playbook=active_playbook, scope_subset=["foo.com"],
    )
    db.commit()
    result = reject_run(
        db, run_id=run.id, approver_id=approver.id,
        reason="scope not covered",
        now=datetime(2026, 7, 23, tzinfo=UTC),
    )
    db.commit()
    assert result.status is PlaybookRunStatus.cancelled
    assert result.rejected_by == approver.id
    assert result.rejection_reason == "scope not covered"
    # last_error mirrors the rejection reason so existing consumers see it.
    assert result.last_error == "rejected: scope not covered"
    assert result.completed_at == datetime(2026, 7, 23, tzinfo=UTC)


def test_reject_non_awaiting_raises(
    db: Session, engagement: Engagement, inactive_playbook: Playbook, approver: User
) -> None:
    run = enqueue_run(
        db, engagement=engagement, playbook=inactive_playbook,
        scope_subset=["foo.com"],
    )
    db.commit()
    with pytest.raises(RunNotAwaitingApprovalError):
        reject_run(db, run_id=run.id, approver_id=approver.id, reason="no")


# ---------------------------------------------------------------------------
# HTTP surface
# ---------------------------------------------------------------------------


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def guest_user(db: Session) -> User:
    u = User(
        id=uuid.uuid4(),
        email=f"a5-guest-{uuid.uuid4().hex[:6]}@example.com",
        display_name="A5 Guest",
        role=UserRole.guest,
        is_active=True,
    )
    db.add(u)
    db.commit()
    return u


def _headers(u: User) -> dict[str, str]:
    return {"X-User-Id": u.email}


def test_post_run_active_playbook_returns_awaiting(
    db: Session, client: TestClient, engagement: Engagement,
    active_playbook: Playbook, approver: User,
) -> None:
    """POST against an active playbook returns 202 with status=awaiting_approval,
    not pending."""
    # The catalog endpoint installs seeds by default; make sure our custom
    # gated playbook is visible by referencing it directly.
    resp = client.post(
        f"/engagements/{engagement.slug}/playbook-runs",
        headers=_headers(approver),
        json={
            "playbook_slug": active_playbook.slug,
            "scope_subset": ["foo.com"],
        },
    )
    assert resp.status_code == 202
    assert resp.json()["status"] == PlaybookRunStatus.awaiting_approval.value


def test_approve_endpoint_flips_and_returns_attribution(
    db: Session, client: TestClient, engagement: Engagement,
    active_playbook: Playbook, approver: User,
) -> None:
    post = client.post(
        f"/engagements/{engagement.slug}/playbook-runs",
        headers=_headers(approver),
        json={"playbook_slug": active_playbook.slug, "scope_subset": ["foo.com"]},
    )
    run_id = post.json()["id"]
    resp = client.post(
        f"/playbook-runs/{run_id}/approve",
        headers=_headers(approver),
        json={"reason": "reviewed and signed off"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == PlaybookRunStatus.pending.value
    assert body["approved_by"] == str(approver.id)
    assert body["approval_reason"] == "reviewed and signed off"


def test_approve_endpoint_unknown_404(
    client: TestClient, approver: User
) -> None:
    resp = client.post(
        f"/playbook-runs/{uuid.uuid4()}/approve",
        headers=_headers(approver),
        json={"reason": "any"},
    )
    assert resp.status_code == 404


def test_approve_endpoint_non_awaiting_409(
    db: Session, client: TestClient, engagement: Engagement,
    inactive_playbook: Playbook, approver: User,
) -> None:
    post = client.post(
        f"/engagements/{engagement.slug}/playbook-runs",
        headers=_headers(approver),
        json={"playbook_slug": "osint-passive-domain", "scope_subset": ["foo.com"]},
    )
    run_id = post.json()["id"]
    resp = client.post(
        f"/playbook-runs/{run_id}/approve",
        headers=_headers(approver),
        json={},
    )
    assert resp.status_code == 409


def test_approve_endpoint_guest_blocked(
    db: Session, client: TestClient, engagement: Engagement,
    active_playbook: Playbook, approver: User, guest_user: User,
) -> None:
    post = client.post(
        f"/engagements/{engagement.slug}/playbook-runs",
        headers=_headers(approver),
        json={"playbook_slug": active_playbook.slug, "scope_subset": ["foo.com"]},
    )
    run_id = post.json()["id"]
    resp = client.post(
        f"/playbook-runs/{run_id}/approve",
        headers=_headers(guest_user),
        json={},
    )
    assert resp.status_code == 403


def test_reject_endpoint_flips_and_carries_reason(
    db: Session, client: TestClient, engagement: Engagement,
    active_playbook: Playbook, approver: User,
) -> None:
    post = client.post(
        f"/engagements/{engagement.slug}/playbook-runs",
        headers=_headers(approver),
        json={"playbook_slug": active_playbook.slug, "scope_subset": ["foo.com"]},
    )
    run_id = post.json()["id"]
    resp = client.post(
        f"/playbook-runs/{run_id}/reject",
        headers=_headers(approver),
        json={"reason": "wrong scope selection"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == PlaybookRunStatus.cancelled.value
    assert body["rejection_reason"] == "wrong scope selection"
    assert body["rejected_by"] == str(approver.id)


def test_reject_endpoint_requires_reason_422(
    db: Session, client: TestClient, engagement: Engagement,
    active_playbook: Playbook, approver: User,
) -> None:
    post = client.post(
        f"/engagements/{engagement.slug}/playbook-runs",
        headers=_headers(approver),
        json={"playbook_slug": active_playbook.slug, "scope_subset": ["foo.com"]},
    )
    run_id = post.json()["id"]
    resp = client.post(
        f"/playbook-runs/{run_id}/reject",
        headers=_headers(approver),
        json={"reason": "   "},  # whitespace only
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# List filter — ?status=awaiting_approval
# ---------------------------------------------------------------------------


def test_list_runs_status_filter(
    db: Session, client: TestClient, engagement: Engagement,
    active_playbook: Playbook, inactive_playbook: Playbook, approver: User,
) -> None:
    # One awaiting_approval + one pending.
    client.post(
        f"/engagements/{engagement.slug}/playbook-runs",
        headers=_headers(approver),
        json={"playbook_slug": active_playbook.slug, "scope_subset": ["foo.com"]},
    )
    client.post(
        f"/engagements/{engagement.slug}/playbook-runs",
        headers=_headers(approver),
        json={"playbook_slug": "osint-passive-domain", "scope_subset": ["bar.com"]},
    )
    resp = client.get(
        f"/engagements/{engagement.slug}/playbook-runs?status=awaiting_approval",
        headers=_headers(approver),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert all(r["status"] == "awaiting_approval" for r in body)
    assert len(body) == 1


def test_list_runs_unknown_status_422(
    db: Session, client: TestClient, engagement: Engagement, approver: User
) -> None:
    resp = client.get(
        f"/engagements/{engagement.slug}/playbook-runs?status=maybe-someday",
        headers=_headers(approver),
    )
    assert resp.status_code == 422
