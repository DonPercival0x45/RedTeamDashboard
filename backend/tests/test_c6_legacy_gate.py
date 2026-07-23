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
