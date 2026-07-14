"""Governance tests for typed strategist suggestions and execution handoff."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.main import app
from app.models import (
    AgentName,
    AuditLog,
    Engagement,
    EngagementStatus,
    EngagementWorkState,
    Finding,
    FindingPhase,
    FindingStatus,
    OwnerEligibility,
    ScopeItem,
    ScopeKind,
    Severity,
    Suggestion,
    SuggestionKind,
    SuggestionStatus,
    Task,
    TaskStatus,
    WorkItem,
    WorkItemExecutor,
    WorkItemFinding,
    WorkItemPriority,
    WorkItemStatus,
)

HDR = {"X-User-Id": "strategy-governance@example.com"}


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def engagements(db: Session) -> Iterator[tuple[Engagement, Engagement]]:
    rows = (
        Engagement(
            name="Strategy Governance A",
            slug=f"strategy-gov-a-{uuid.uuid4().hex[:8]}",
            status=EngagementStatus.active,
            work_state=EngagementWorkState.active,
        ),
        Engagement(
            name="Strategy Governance B",
            slug=f"strategy-gov-b-{uuid.uuid4().hex[:8]}",
            status=EngagementStatus.active,
            work_state=EngagementWorkState.active,
        ),
    )
    db.add_all(rows)
    db.commit()
    for row in rows:
        db.refresh(row)
    try:
        yield rows
    finally:
        for row in rows:
            db.execute(text("SELECT flush_engagement(:id)"), {"id": row.id})
            db.commit()


def _finding(db: Session, engagement: Engagement, title: str) -> Finding:
    row = Finding(
        engagement_id=engagement.id,
        title=title,
        severity=Severity.medium,
        details={},
        source_tool="manual",
        target="api.example.test",
        phase=FindingPhase.general,
        status=FindingStatus.pending_validation,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _work_suggestion(
    db: Session,
    engagement: Engagement,
    finding_links: list[dict[str, str]],
) -> Suggestion:
    row = Suggestion(
        engagement_id=engagement.id,
        title="Compare authentication behavior",
        body="Determine whether behavior is gateway-wide.",
        kind=SuggestionKind.work_item,
        status=SuggestionStatus.open,
        created_by_agent=AgentName.engagement_strategist,
        payload={
            "schema_version": 1,
            "work_item": {
                "title": "Compare authentication behavior",
                "description": "Compare both findings.",
                "rationale": "May change affected targets.",
                "priority": "high",
                "executor_type": "analyst",
                "acceptance_criteria": ["Reference both findings"],
                "finding_links": finding_links,
            },
        },
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _work_item(db: Session, engagement: Engagement) -> WorkItem:
    row = WorkItem(
        engagement_id=engagement.id,
        title="Identify exposed services",
        status=WorkItemStatus.ready,
        priority=WorkItemPriority.high,
        executor_type=WorkItemExecutor.tactical,
        acceptance_criteria=[],
        row_version=1,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_accept_work_item_suggestion_links_multiple_findings_without_dispatch(
    client: TestClient,
    db: Session,
    engagements: tuple[Engagement, Engagement],
) -> None:
    engagement, _other = engagements
    primary = _finding(db, engagement, "Primary authentication issue")
    related = _finding(db, engagement, "Secondary authentication issue")
    suggestion = _work_suggestion(
        db,
        engagement,
        [
            {"finding_id": str(primary.id), "relationship": "primary"},
            {"finding_id": str(related.id), "relationship": "related"},
        ],
    )

    response = client.post(f"/suggestions/{suggestion.id}/accept", headers=HDR)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["work_item"]["status"] == "ready"
    assert body["task"] is None
    assert body["dispatched"] is False
    work_item_id = uuid.UUID(body["work_item"]["id"])
    links = list(
        db.execute(
            select(WorkItemFinding).where(WorkItemFinding.work_item_id == work_item_id)
        ).scalars()
    )
    assert {(row.finding_id, row.relationship.value) for row in links} == {
        (primary.id, "primary"),
        (related.id, "related"),
    }
    assert (
        db.execute(select(Task).where(Task.work_item_id == work_item_id)).scalar_one_or_none()
        is None
    )
    audit = db.execute(
        select(AuditLog).where(
            AuditLog.engagement_id == engagement.id,
            AuditLog.event_type == "suggestion.accepted",
        )
    ).scalar_one()
    assert audit.payload["work_item_id"] == str(work_item_id)
    assert audit.payload["dispatched"] is False


def test_foreign_finding_rejects_work_suggestion_atomically(
    client: TestClient,
    db: Session,
    engagements: tuple[Engagement, Engagement],
) -> None:
    engagement, other = engagements
    foreign = _finding(db, other, "Foreign finding")
    suggestion = _work_suggestion(
        db,
        engagement,
        [{"finding_id": str(foreign.id), "relationship": "primary"}],
    )

    response = client.post(f"/suggestions/{suggestion.id}/accept", headers=HDR)

    assert response.status_code == 422, response.text
    db.expire_all()
    assert db.get(Suggestion, suggestion.id).status == SuggestionStatus.open
    assert (
        db.execute(
            select(WorkItem).where(WorkItem.engagement_id == engagement.id)
        ).scalar_one_or_none()
        is None
    )
    assert (
        db.execute(
            select(AuditLog).where(
                AuditLog.engagement_id == engagement.id,
                AuditLog.event_type == "suggestion.accepted",
            )
        ).scalar_one_or_none()
        is None
    )


def test_execution_suggestion_is_inert_then_acceptance_routes_through_tactical(
    client: TestClient,
    db: Session,
    engagements: tuple[Engagement, Engagement],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engagement, _other = engagements
    finding = _finding(db, engagement, "Service finding")
    work = _work_item(db, engagement)
    db.add(
        ScopeItem(
            engagement_id=engagement.id,
            kind=ScopeKind.ip,
            value="203.0.113.10",
            is_exclusion=False,
            source="defined",
        )
    )
    db.commit()
    request = {
        "tool": "portscan",
        "target": "203.0.113.10",
        "task_kind": "enum",
        "title": "Identify exposed services",
        "expected_work_item_version": 1,
        "idempotency_key": f"exec-{uuid.uuid4()}",
        "finding_id": str(finding.id),
    }

    created = client.post(f"/work-items/{work.id}/execution-suggestions", json=request, headers=HDR)

    assert created.status_code == 201, created.text
    suggestion_id = uuid.UUID(created.json()["suggestion"]["id"])
    assert db.execute(select(Task).where(Task.work_item_id == work.id)).scalar_one_or_none() is None
    repeated = client.post(
        f"/work-items/{work.id}/execution-suggestions", json=request, headers=HDR
    )
    assert repeated.status_code == 201, repeated.text
    assert repeated.json()["suggestion"]["id"] == str(suggestion_id)

    calls: list[uuid.UUID] = []

    def fake_dispatch(
        self: object,
        session: Session,
        *,
        task: Task,
        acting_user_id: uuid.UUID,
        **_kwargs: object,
    ) -> uuid.UUID:
        assert acting_user_id
        calls.append(task.id)
        task.status = TaskStatus.dispatched
        task.dispatched_at = datetime.now(tz=UTC)
        task.run_id = uuid.uuid4()
        session.flush()
        return task.run_id

    from app.services import suggestion_router

    monkeypatch.setattr(
        suggestion_router.TacticalAgent,
        "dispatch",
        fake_dispatch,
    )
    accepted = client.post(f"/suggestions/{suggestion_id}/accept", headers=HDR)

    assert accepted.status_code == 200, accepted.text
    accepted_body = accepted.json()
    assert accepted_body["dispatched"] is True
    assert accepted_body["task"]["work_item_id"] == str(work.id)
    task = db.get(Task, uuid.UUID(accepted_body["task"]["id"]))
    assert task is not None
    assert task.work_item_id == work.id
    assert task.finding_id == finding.id
    assert task.owner_eligibility == OwnerEligibility.agent
    assert calls == [task.id]

    repeated_accept = client.post(f"/suggestions/{suggestion_id}/accept", headers=HDR)
    assert repeated_accept.status_code == 200, repeated_accept.text
    assert repeated_accept.json()["dispatched"] is True
    assert repeated_accept.json()["task"]["id"] == str(task.id)
    assert calls == [task.id]


def test_execution_proposal_rechecks_version_scope_and_terminal_state(
    client: TestClient,
    db: Session,
    engagements: tuple[Engagement, Engagement],
) -> None:
    engagement, _other = engagements
    work = _work_item(db, engagement)
    scope = ScopeItem(
        engagement_id=engagement.id,
        kind=ScopeKind.ip,
        value="203.0.113.20",
        is_exclusion=False,
        source="defined",
    )
    db.add(scope)
    db.commit()
    base = {
        "tool": "portscan",
        "target": "203.0.113.20",
        "task_kind": "scan",
        "title": "Scan current target",
        "expected_work_item_version": 1,
        "idempotency_key": f"stale-{uuid.uuid4()}",
    }
    created = client.post(f"/work-items/{work.id}/execution-suggestions", json=base, headers=HDR)
    assert created.status_code == 201, created.text
    suggestion_id = uuid.UUID(created.json()["suggestion"]["id"])

    work.row_version = 2
    db.commit()
    stale_accept = client.post(f"/suggestions/{suggestion_id}/accept", headers=HDR)
    assert stale_accept.status_code == 409, stale_accept.text
    assert db.execute(select(Task).where(Task.work_item_id == work.id)).scalar_one_or_none() is None
    db.expire_all()
    assert db.get(Suggestion, suggestion_id).status == SuggestionStatus.open

    work = db.get(WorkItem, work.id)
    work.row_version = 1
    db.delete(db.get(ScopeItem, scope.id))
    db.commit()
    no_scope_accept = client.post(f"/suggestions/{suggestion_id}/accept", headers=HDR)
    assert no_scope_accept.status_code == 422, no_scope_accept.text
    assert "outside current scope" in no_scope_accept.text
    assert db.execute(select(Task).where(Task.work_item_id == work.id)).scalar_one_or_none() is None

    work = db.get(WorkItem, work.id)
    work.status = WorkItemStatus.completed
    db.commit()
    terminal_create = client.post(
        f"/work-items/{work.id}/execution-suggestions",
        json={**base, "idempotency_key": f"terminal-{uuid.uuid4()}"},
        headers=HDR,
    )
    assert terminal_create.status_code == 409, terminal_create.text
