"""First-class playbook visibility in Status, running jobs, and decisions."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.main import app
from app.models import (
    Approval,
    ApprovalStatus,
    Engagement,
    EngagementArchitecture,
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
    WorkerComponent,
    WorkerInstance,
    WorkerOperationalEvent,
)
from app.services.worker_observability import WorkerRuntime


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
        intelligence_architecture=EngagementArchitecture.v3,
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


def test_status_surfaces_a_live_run_without_registered_worker_telemetry(
    surface_data: dict,
) -> None:
    with SessionLocal() as setup:
        setup.execute(delete(WorkerOperationalEvent))
        setup.execute(delete(WorkerInstance))
        run = setup.get(PlaybookRun, surface_data["awaiting"].id)
        assert run is not None
        run.status = PlaybookRunStatus.running
        run.started_at = datetime.now(tz=UTC)
        run.worker_id = "legacy-worker"
        run.worker_heartbeat_at = datetime.now(tz=UTC)
        setup.commit()

    with TestClient(app) as client:
        response = client.get(
            f"/engagements/{surface_data['engagement'].slug}/status",
            headers=_headers(surface_data),
        )
    assert response.status_code == 200, response.text
    pool = response.json()["worker_pool"]
    assert pool["health"] == "degraded"
    assert pool["capacity"] == 1
    assert pool["busy"] == 1
    assert pool["slots"][0]["state"] == "untracked"
    assert pool["slots"][0]["current_run"]["playbook_name"] == "Surface playbook"


def test_status_exposes_live_worker_slots_queue_and_incidents(surface_data: dict) -> None:
    with SessionLocal() as cleanup:
        cleanup.execute(delete(WorkerOperationalEvent))
        cleanup.execute(delete(WorkerInstance))
        cleanup.commit()
    runtime = WorkerRuntime(
        session_factory=SessionLocal,
        role="playbook",
        concurrency=2,
        health_file=f"/tmp/test-worker-{uuid.uuid4()}",
    )
    runtime.start()
    owner_token_0 = str(uuid.uuid4())
    owner_token_1 = str(uuid.uuid4())
    runtime.component_started("playbook-lane", slot=0, owner_token=owner_token_0)
    runtime.component_started("playbook-lane", slot=1, owner_token=owner_token_1)
    runtime.component_heartbeat(
        "playbook-lane",
        slot=0,
        state="busy",
        current_run_id=surface_data["awaiting"].id,
    )

    with SessionLocal() as setup:
        foreign_engagement = Engagement(
            name="Foreign status surface",
            slug=f"foreign-{uuid.uuid4().hex[:8]}",
            status=EngagementStatus.active,
            work_state=EngagementWorkState.active,
            intelligence_architecture=EngagementArchitecture.v3,
        )
        setup.add(foreign_engagement)
        setup.flush()
        foreign_run = PlaybookRun(
            engagement_id=foreign_engagement.id,
            playbook_id=surface_data["playbook"].id,
            status=PlaybookRunStatus.running,
            scope_subset=["private.example"],
            executor_kind=PlaybookExecutorKind.internal,
            steps_total=9,
            started_at=datetime.now(tz=UTC),
            worker_id=owner_token_1,
            worker_heartbeat_at=datetime.now(tz=UTC),
        )
        setup.add(foreign_run)
        setup.commit()
        foreign_engagement_id = foreign_engagement.id
        foreign_run_id = foreign_run.id

    runtime.component_heartbeat(
        "playbook-lane",
        slot=1,
        state="busy",
        current_run_id=foreign_run_id,
    )
    with SessionLocal() as setup:
        foreign_component = setup.query(WorkerComponent).filter(
            WorkerComponent.worker_instance_id == runtime.id,
            WorkerComponent.slot == 1,
        ).one()
        foreign_component.last_error = "private foreign failure"
        setup.commit()
    runtime.record_event(
        event_type="thread.crashed",
        message="lane failed safely",
        component="playbook-lane",
        slot=0,
        playbook_run_id=surface_data["awaiting"].id,
    )
    runtime.record_event(
        event_type="thread.crashed",
        message="private foreign incident",
        component="playbook-lane",
        slot=1,
        playbook_run_id=foreign_run_id,
    )
    try:
        with TestClient(app) as client:
            response = client.get(
                f"/engagements/{surface_data['engagement'].slug}/status",
                headers=_headers(surface_data),
            )
        assert response.status_code == 200, response.text
        pool = response.json()["worker_pool"]
        assert pool["health"] == "healthy"
        assert pool["capacity"] == 2
        assert pool["busy"] == 2
        assert pool["idle"] == 0
        assert pool["slots"][0]["current_run"]["playbook_name"] == "Surface playbook"
        assert pool["slots"][1]["current_run"] is None
        assert pool["slots"][1]["last_error"] is None
        messages = {row["message"] for row in pool["recent_failures"]}
        assert "lane failed safely" in messages
        assert "private foreign incident" not in messages
        assert str(foreign_run_id) not in response.text
        assert "private foreign" not in response.text
    finally:
        runtime.stop("test complete")
        with SessionLocal() as cleanup:
            cleanup.execute(
                delete(WorkerOperationalEvent).where(
                    WorkerOperationalEvent.worker_instance_id == runtime.id
                )
            )
            cleanup.execute(delete(WorkerInstance).where(WorkerInstance.id == runtime.id))
            cleanup.execute(
                delete(Engagement).where(Engagement.id == foreign_engagement_id)
            )
            cleanup.commit()


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
