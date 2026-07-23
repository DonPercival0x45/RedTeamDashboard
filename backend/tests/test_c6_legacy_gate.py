"""C6a tests — v3 engagements can't kick LangGraph runs or trigger Tactical.

Two gates, one settings toggle:

- ``POST /engagements/{slug}/runs`` returns 409 with a pointer to
  ``/playbook-runs`` when the engagement is v3 and
  ``enforce_v3_playbook_only`` is True. Legacy engagements untouched. Toggle
  off allows the LangGraph path through.
- ``TacticalAgent.dispatch`` raises ``TacticalSkippedV3`` for v3 engagements
  under the same conditions; toggle off falls through to normal dispatch
  (which errors on missing tool for our stub input — proves we got past the
  early guard).

Doesn't assert the shape of successful LangGraph dispatch — that's covered
by ``test_direct_run_lease`` and friends. This file's job is only the gate.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.agents.tactical import TacticalAgent, TacticalSkippedV3
from app.core.config import settings
from app.main import app
from app.models import (
    AgentTrigger,
    Engagement,
    EngagementArchitecture,
    EngagementStatus,
    EngagementWorkState,
    OwnerEligibility,
    Task,
    TaskKind,
    TaskStatus,
    User,
    UserRole,
)


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def _cleanup_command_outbox(db: Session):
    """Toggle-off + legacy tests hit ``POST /runs`` fully, which commits
    ``CommandOutbox`` rows outside the ``db`` fixture's transaction. Sweep
    them after the test so ``test_execution_durability`` (or any other
    outbox-relay test running later in the session) doesn't relay them
    unexpectedly."""
    from app.models import CommandOutbox

    yield
    db.query(CommandOutbox).delete()
    db.commit()


@pytest.fixture()
def user(db: Session) -> User:
    u = User(
        id=uuid.uuid4(),
        email=f"c6-{uuid.uuid4().hex[:6]}@example.com",
        display_name="C6a Tester",
        role=UserRole.user,
        is_active=True,
    )
    db.add(u)
    db.commit()
    return u


def _mk_engagement(
    db: Session,
    *,
    architecture: EngagementArchitecture,
) -> Engagement:
    eng = Engagement(
        name=f"c6-{architecture.value}",
        slug=f"c6-{architecture.value}-{uuid.uuid4().hex[:6]}",
        status=EngagementStatus.active,
        work_state=EngagementWorkState.active,
        intelligence_architecture=architecture,
        converted_to_v3_at=(
            datetime.now(tz=UTC)
            if architecture is EngagementArchitecture.v3
            else None
        ),
    )
    db.add(eng)
    db.commit()
    return eng


# ---------------------------------------------------------------------------
# POST /engagements/{slug}/runs gate
# ---------------------------------------------------------------------------


def test_start_run_returns_409_for_v3_engagement(
    db: Session, client: TestClient, user: User
) -> None:
    eng = _mk_engagement(db, architecture=EngagementArchitecture.v3)
    resp = client.post(
        f"/engagements/{eng.slug}/runs",
        headers={"X-User-Id": user.email},
        json={"prompt": "test"},
    )
    assert resp.status_code == 409, resp.text
    assert "playbook-runs" in resp.json()["detail"]
    assert "v3" in resp.json()["detail"]


def test_start_run_toggle_off_allows_v3_through(
    db: Session, client: TestClient, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When enforce_v3_playbook_only is False, the v3 gate is a no-op. The
    request may still fail downstream (missing BYO key, etc.), but not with
    the C6a 409."""
    monkeypatch.setattr(settings, "enforce_v3_playbook_only", False)
    eng = _mk_engagement(db, architecture=EngagementArchitecture.v3)
    resp = client.post(
        f"/engagements/{eng.slug}/runs",
        headers={"X-User-Id": user.email},
        json={"prompt": "test"},
    )
    # Not 409 with the C6a detail. The endpoint may still 400/409 for other
    # reasons (BYO key missing), but the C6a-specific message shouldn't
    # appear.
    if resp.status_code == 409:
        assert "playbook-runs" not in resp.json().get("detail", "")


def test_start_run_legacy_engagement_untouched(
    db: Session, client: TestClient, user: User
) -> None:
    """Legacy engagements never see the C6a gate."""
    eng = _mk_engagement(db, architecture=EngagementArchitecture.legacy)
    resp = client.post(
        f"/engagements/{eng.slug}/runs",
        headers={"X-User-Id": user.email},
        json={"prompt": "test"},
    )
    # 400 (BYO key missing) is fine — the C6a-specific 409 must not appear.
    if resp.status_code == 409:
        assert "playbook-runs" not in resp.json().get("detail", "")


# ---------------------------------------------------------------------------
# Tactical.dispatch gate
# ---------------------------------------------------------------------------


def _mk_task(db: Session, engagement: Engagement) -> Task:
    task = Task(
        engagement_id=engagement.id,
        title="c6a gate test",
        kind=TaskKind.scan,
        owner_eligibility=OwnerEligibility.agent,
        status=TaskStatus.pending,
        payload={"tool": "subfinder", "target": "example.com"},
    )
    db.add(task)
    db.commit()
    return task


def test_tactical_dispatch_skips_v3_engagement(
    db: Session, user: User
) -> None:
    """v3 engagement → TacticalSkippedV3 before any LLM call happens."""
    eng = _mk_engagement(db, architecture=EngagementArchitecture.v3)
    task = _mk_task(db, eng)
    agent = TacticalAgent(redis_client=None)
    with pytest.raises(TacticalSkippedV3):
        agent.dispatch(
            db, task=task, acting_user_id=user.id,
            trigger=AgentTrigger.manual,
        )
    # Task not mutated — status stays pending, no run_id assigned.
    db.refresh(task)
    assert task.status is TaskStatus.pending
    assert task.run_id is None


def test_tactical_dispatch_toggle_off_falls_through(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When enforce_v3_playbook_only is False, dispatch proceeds past the
    C6a guard (and hits downstream logic; we just prove it wasn't
    TacticalSkippedV3)."""
    monkeypatch.setattr(settings, "enforce_v3_playbook_only", False)
    eng = _mk_engagement(db, architecture=EngagementArchitecture.v3)
    task = _mk_task(db, eng)
    agent = TacticalAgent(redis_client=None)
    # Downstream error is fine (unknown tool, missing lease, etc.); we
    # just assert it isn't the C6a skip.
    try:
        agent.dispatch(
            db, task=task, acting_user_id=user.id,
            trigger=AgentTrigger.manual,
        )
    except TacticalSkippedV3:
        pytest.fail("C6a gate should be off when toggle is False")
    except Exception:
        # Any other failure mode is acceptable — the point is the gate
        # let us through.
        pass


def test_tactical_dispatch_legacy_untouched(db: Session, user: User) -> None:
    """Legacy engagements never see the C6a gate."""
    eng = _mk_engagement(db, architecture=EngagementArchitecture.legacy)
    task = _mk_task(db, eng)
    agent = TacticalAgent(redis_client=None)
    try:
        agent.dispatch(
            db, task=task, acting_user_id=user.id,
            trigger=AgentTrigger.manual,
        )
    except TacticalSkippedV3:
        pytest.fail("C6a gate must never fire on legacy engagements")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# C6b: POST /tasks/{id}/retry
# ---------------------------------------------------------------------------


def _mk_failed_task(db: Session, engagement: Engagement) -> Task:
    """Retry endpoint only accepts failed/deferred agent-eligible tasks."""
    from datetime import UTC as _UTC
    from datetime import datetime as _dt

    task = Task(
        engagement_id=engagement.id,
        title="c6b retry test",
        kind=TaskKind.scan,
        owner_eligibility=OwnerEligibility.agent,
        status=TaskStatus.failed,
        payload={"tool": "subfinder", "target": "example.com"},
        run_id=uuid.uuid4(),
        completed_at=_dt.now(tz=_UTC),
    )
    db.add(task)
    db.commit()
    return task


def test_retry_returns_409_for_v3_engagement(
    db: Session, client: TestClient, user: User
) -> None:
    """v3 engagement → retry endpoint refuses with 409 + playbook-runs pointer.

    Task state is restored (status stays failed, run_id preserved) so the
    aborted retry hasn't stripped the row's history.
    """
    eng = _mk_engagement(db, architecture=EngagementArchitecture.v3)
    task = _mk_failed_task(db, eng)
    prior_status = task.status
    prior_run_id = task.run_id

    resp = client.post(
        f"/tasks/{task.id}/retry",
        headers={"X-User-Id": user.email},
    )
    assert resp.status_code == 409, resp.text
    assert "playbook-runs" in resp.json()["detail"]

    db.refresh(task)
    assert task.status is prior_status
    assert task.run_id == prior_run_id


def test_retry_toggle_off_falls_through_on_v3(
    db: Session, client: TestClient, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Toggle off → v3 engagement no longer 409s at the C6b layer. Downstream
    may still fail for other reasons; we just prove the C6b message is gone."""
    monkeypatch.setattr(settings, "enforce_v3_playbook_only", False)
    eng = _mk_engagement(db, architecture=EngagementArchitecture.v3)
    task = _mk_failed_task(db, eng)
    resp = client.post(
        f"/tasks/{task.id}/retry",
        headers={"X-User-Id": user.email},
    )
    if resp.status_code == 409:
        assert "playbook-runs" not in resp.json().get("detail", "")


# ---------------------------------------------------------------------------
# C6b: POST /engagements/{slug}/findings/{fid}/analyze
# ---------------------------------------------------------------------------


def test_analyze_endpoint_returns_409_for_v3(
    db: Session, client: TestClient, user: User
) -> None:
    """v3 engagement → per-finding analyze refuses with 409 pointing at the
    Strategy view's on-demand analysis intelligence mode."""
    from app.models import Finding, Severity

    eng = _mk_engagement(db, architecture=EngagementArchitecture.v3)
    finding = Finding(
        engagement_id=eng.id,
        title="c6b analyze test",
        severity=Severity.info,
    )
    db.add(finding)
    db.commit()

    resp = client.post(
        f"/findings/{finding.id}/analyze",
        headers={"X-User-Id": user.email},
    )
    assert resp.status_code == 409, resp.text
    assert "Strategy" in resp.json()["detail"]


def test_analyze_endpoint_toggle_off_passes_gate_on_v3(
    db: Session, client: TestClient, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Toggle off → the C6b gate is a no-op. Downstream may still 400 for BYO
    key reasons, but the C6b-specific 'Strategy' pointer must not appear."""
    from app.models import Finding, Severity

    monkeypatch.setattr(settings, "enforce_v3_playbook_only", False)
    eng = _mk_engagement(db, architecture=EngagementArchitecture.v3)
    finding = Finding(
        engagement_id=eng.id,
        title="c6b analyze toggle test",
        severity=Severity.info,
    )
    db.add(finding)
    db.commit()

    resp = client.post(
        f"/findings/{finding.id}/analyze",
        headers={"X-User-Id": user.email},
    )
    if resp.status_code == 409:
        # If it 409s at all, it's not the C6b-shaped message.
        assert "Strategy view" not in resp.json().get("detail", "")


# ---------------------------------------------------------------------------
# C6c: new-engagement default architecture
# ---------------------------------------------------------------------------


def test_new_engagement_defaults_to_v3_when_methodology_provided(
    client: TestClient, user: User
) -> None:
    """C6c: caller supplies methodology_slug but omits intelligence_architecture
    → resolves to v3 (the new default)."""
    resp = client.post(
        "/engagements",
        headers={"X-User-Id": user.email},
        json={
            "name": "c6c v3 default",
            "methodology_slug": "osint-minimal",
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["intelligence_architecture"] == "v3"


def test_new_engagement_defaults_to_legacy_without_methodology(
    client: TestClient, user: User
) -> None:
    """C6c compat fallback: v3 needs a methodology snapshot. If the caller
    omits both fields, we silently downshift to legacy so old callers keep
    working."""
    resp = client.post(
        "/engagements",
        headers={"X-User-Id": user.email},
        json={"name": "c6c fallback legacy"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["intelligence_architecture"] == "legacy"


def test_new_engagement_explicit_legacy_still_honored(
    client: TestClient, user: User
) -> None:
    """Callers who explicitly request legacy still get it, even with a
    methodology (an opt-out from the C6c default)."""
    resp = client.post(
        "/engagements",
        headers={"X-User-Id": user.email},
        json={
            "name": "c6c explicit legacy",
            "intelligence_architecture": "legacy",
            "methodology_slug": "osint-minimal",
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["intelligence_architecture"] == "legacy"


def test_new_engagement_toggle_off_defaults_to_legacy(
    client: TestClient, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Operator flips ``default_intelligence_architecture=legacy`` → new
    engagements land on legacy even when methodology_slug is present."""
    monkeypatch.setattr(settings, "default_intelligence_architecture", "legacy")
    resp = client.post(
        "/engagements",
        headers={"X-User-Id": user.email},
        json={
            "name": "c6c toggle off",
            "methodology_slug": "osint-minimal",
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["intelligence_architecture"] == "legacy"


# ---------------------------------------------------------------------------
# C6b: services/suggestion_router + services/finding_chat catch branches
#
# The exception fires from Tactical.dispatch (covered by the C6a tests above);
# each service module only has a small ``except TacticalSkippedV3:`` no-op or
# result-shape branch. Full-flow tests would need Suggestion + Finding + chat
# conversation fixtures, which is disproportionate for a 4-line catch. Rely on
# code review + the C6a exception coverage for those two.
# ---------------------------------------------------------------------------
