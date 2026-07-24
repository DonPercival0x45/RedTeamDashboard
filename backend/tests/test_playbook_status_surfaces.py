"""First-class playbook visibility in Status, running jobs, and decisions."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.main import app
from app.models import (
    Approval,
    ApprovalStatus,
    Engagement,
    EngagementStatus,
    EngagementWorkState,
    OwnerEligibility,
    Playbook,
    PlaybookExecutorKind,
    PlaybookRun,
    PlaybookRunStatus,
    RiskLevel,
    Task,
    TaskKind,
    TaskStatus,
    User,
    UserRole,
)


@pytest.fixture()
def surface_data(db: Session):
    user = User(
        id=uuid.uuid4(),
        email=f"surface-{uuid.uuid4().hex[:8]}@example.com",
        display_name="Surface tester",
        role=UserRole.user,
        is_active=True,
    )
    engagement = Engagement(
        name="Status surface",
        slug=f"surface-{uuid.uuid4().hex[:8]}",
        status=EngagementStatus.active,
        work_state=EngagementWorkState.active,
    )
    playbook = Playbook(
        slug=f"surface-pb-{uuid.uuid4().hex[:8]}",
        version=1,
        name="Surface playbook",
        description="Visibility test",
        applies_to_asset_class="domain",
        active=True,
    )
    db.add_all([user, engagement, playbook])
    db.flush()
    now = datetime.now(tz=UTC)
    awaiting = PlaybookRun(
        engagement_id=engagement.id,
        playbook_id=playbook.id,
        requested_by=user.id,
        status=PlaybookRunStatus.awaiting_approval,
        scope_subset=["foo.example"],
        executor_kind=PlaybookExecutorKind.internal,
        steps_total=2,
    )
    partial = PlaybookRun(
        engagement_id=engagement.id,
        playbook_id=playbook.id,
        requested_by=user.id,
        status=PlaybookRunStatus.partial,
        scope_subset=["bar.example"],
        executor_kind=PlaybookExecutorKind.internal,
        created_at=now - timedelta(minutes=3),
        started_at=now - timedelta(minutes=2),
        completed_at=now - timedelta(minutes=1),
        steps_total=2,
        steps_succeeded=1,
        steps_failed=1,
        findings_new=3,
        findings_total=3,
        last_error="one step failed",
    )
    task = Task(
        engagement_id=engagement.id,
        title="Legacy enumeration",
        kind=TaskKind.enum,
        owner_eligibility=OwnerEligibility.agent,
        status=TaskStatus.running,
        payload={},
        dispatched_at=now - timedelta(minutes=1),
    )
    approval = Approval(
        engagement_id=engagement.id,
        thread_id=str(uuid.uuid4()),
        tool_name="nmap",
        tool_args={"target": "foo.example"},
        risk=RiskLevel.active,
        scope_check={"allowed": True},
        status=ApprovalStatus.pending,
    )
    db.add_all([awaiting, partial, task, approval])
    db.commit()
    try:
        yield {
            "user": user,
            "engagement": engagement,
            "playbook": playbook,
            "awaiting": awaiting,
            "partial": partial,
            "task": task,
            "approval": approval,
        }
    finally:
        db.rollback()
        db.execute(delete(Engagement).where(Engagement.id == engagement.id))
        db.execute(delete(Playbook).where(Playbook.id == playbook.id))
        db.execute(delete(User).where(User.id == user.id))
        db.commit()


def _headers(data: dict) -> dict[str, str]:
    return {"X-User-Id": data["user"].email}


def test_engagement_status_includes_playbook_runs(surface_data: dict) -> None:
    with TestClient(app) as client:
        response = client.get(
            f"/engagements/{surface_data['engagement'].slug}/status",
            headers=_headers(surface_data),
        )
    assert response.status_code == 200, response.text
    rows = response.json()["playbook_runs"]
    by_id = {row["id"]: row for row in rows}

    awaiting = by_id[str(surface_data["awaiting"].id)]
    assert awaiting["kind"] == "playbook"
    assert awaiting["color"] == "pending"
    assert awaiting["raw_status"] == "awaiting_approval"
    assert awaiting["title"] == "Surface playbook"

    partial = by_id[str(surface_data["partial"].id)]
    assert partial["color"] == "completed"
    assert partial["outcome"] == "partial"
    assert partial["log"]["findings_new"] == 3


def test_playbook_status_step_log_is_durable(surface_data: dict) -> None:
    with TestClient(app) as client:
        response = client.get(
            f"/engagements/{surface_data['engagement'].slug}/status/playbooks/"
            f"{surface_data['partial'].id}/steps",
            headers=_headers(surface_data),
        )
    assert response.status_code == 200, response.text
    kinds = [step["kind"] for step in response.json()["steps"]]
    assert kinds == ["playbook.requested", "playbook.started", "playbook.partial"]


def test_global_running_jobs_unions_tasks_and_live_playbooks(surface_data: dict) -> None:
    with TestClient(app) as client:
        response = client.get("/jobs/running", headers=_headers(surface_data))
    assert response.status_code == 200, response.text
    by_id = {row["id"]: row for row in response.json()}

    task = by_id[str(surface_data["task"].id)]
    assert task["kind"] == "task"
    assert task["status"] == "running"
    assert task["awaiting_action"] is False

    run = by_id[str(surface_data["awaiting"].id)]
    assert run["kind"] == "playbook"
    assert run["awaiting_action"] is True
    assert run["steps_total"] == 2
    assert str(surface_data["partial"].id) not in by_id


def test_awaiting_playbook_cannot_bypass_rejection_audit(surface_data: dict) -> None:
    with TestClient(app) as client:
        response = client.post(
            f"/playbook-runs/{surface_data['awaiting'].id}/cancel",
            headers=_headers(surface_data),
        )
    assert response.status_code == 409


def test_decision_inbox_unions_tool_and_playbook_approvals(surface_data: dict) -> None:
    with TestClient(app) as client:
        response = client.get("/decision-inbox", headers=_headers(surface_data))
    assert response.status_code == 200, response.text
    by_id = {row["id"]: row for row in response.json()}

    tool = by_id[str(surface_data["approval"].id)]
    assert tool["kind"] == "tool_approval"
    assert tool["tool_name"] == "nmap"
    assert tool["engagement_slug"] == surface_data["engagement"].slug

    run = by_id[str(surface_data["awaiting"].id)]
    assert run["kind"] == "playbook_run"
    assert run["playbook_name"] == "Surface playbook"
    assert run["scope_subset"] == ["foo.example"]
    assert str(surface_data["partial"].id) not in by_id
