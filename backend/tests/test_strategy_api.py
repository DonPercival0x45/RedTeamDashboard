"""Focused integration coverage for the manual Strategy/work-ledger API."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.main import app
from app.models import (
    AuditLog,
    Engagement,
    EngagementStatus,
    EngagementWorkState,
    Finding,
    FindingPhase,
    FindingStatus,
    Severity,
    StrategySignal,
    User,
    UserRole,
    WorkItemResult,
)

HDR = {"X-User-Id": "strategy-api@example.com"}


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def engagement(db: Session) -> Iterator[Engagement]:
    row = Engagement(
        name="Strategy API",
        slug=f"strategy-api-{uuid.uuid4().hex[:8]}",
        status=EngagementStatus.active,
        work_state=EngagementWorkState.active,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    try:
        yield row
    finally:
        db.execute(text("SELECT flush_engagement(:id)"), {"id": row.id})
        db.commit()


def _finding(db: Session, engagement: Engagement) -> Finding:
    row = Finding(
        engagement_id=engagement.id,
        title="API authentication behavior",
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


def test_strategy_revision_acceptance_and_stale_base(
    client: TestClient, db: Session, engagement: Engagement
) -> None:
    proposed = client.post(
        f"/engagements/{engagement.slug}/strategy/revisions",
        json={"body": "Test the public API.", "state": "proposed"},
        headers=HDR,
    )
    assert proposed.status_code == 201, proposed.text
    revision_id = proposed.json()["id"]

    accepted = client.post(
        f"/engagements/{engagement.slug}/strategy/revisions/{revision_id}/accept",
        json={"based_on_revision_id": None},
        headers=HDR,
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["state"] == "current"

    successor = client.post(
        f"/engagements/{engagement.slug}/strategy/revisions",
        json={
            "body": "Test API and alternate hostname.",
            "state": "proposed",
            "based_on_revision_id": revision_id,
        },
        headers=HDR,
    )
    assert successor.status_code == 201, successor.text
    stale = client.post(
        f"/engagements/{engagement.slug}/strategy/revisions/{successor.json()['id']}/accept",
        json={"based_on_revision_id": None},
        headers=HDR,
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["detail"]["code"] == "stale_strategy_revision"

    direct = client.post(
        f"/engagements/{engagement.slug}/strategy/revisions",
        json={
            "body": "Direct analyst edit.",
            "state": "current",
            "based_on_revision_id": revision_id,
        },
        headers=HDR,
    )
    assert direct.status_code == 201, direct.text
    assert direct.json()["state"] == "current"
    current = client.get(f"/engagements/{engagement.slug}/strategy", headers=HDR)
    assert current.status_code == 200
    assert current.json()["id"] == direct.json()["id"]

    audits = list(
        db.execute(select(AuditLog).where(AuditLog.engagement_id == engagement.id)).scalars()
    )
    assert {row.event_type for row in audits} >= {
        "strategy.revision_proposed",
        "strategy.revision_accepted",
    }


def test_objective_and_work_item_versions_links_and_rollup(
    client: TestClient, db: Session, engagement: Engagement
) -> None:
    finding = _finding(db, engagement)
    objective = client.post(
        f"/engagements/{engagement.slug}/objectives",
        json={"title": "Assess authentication", "priority": "high"},
        headers=HDR,
    )
    assert objective.status_code == 201, objective.text
    objective_body = objective.json()

    work = client.post(
        f"/engagements/{engagement.slug}/work-items",
        json={
            "title": "Compare authentication behavior",
            "objective_id": objective_body["id"],
            "priority": "high",
            "executor_type": "analyst",
            "finding_links": [{"finding_id": str(finding.id), "relationship": "primary"}],
        },
        headers=HDR,
    )
    assert work.status_code == 201, work.text
    body = work.json()
    assert body["row_version"] == 1
    assert body["finding_links"][0]["finding_id"] == str(finding.id)

    started = client.post(
        f"/work-items/{body['id']}/start",
        json={"expected_row_version": 1},
        headers=HDR,
    )
    assert started.status_code == 200, started.text
    assert started.json()["status"] == "in_progress"
    assert started.json()["row_version"] == 2

    stale = client.patch(
        f"/work-items/{body['id']}",
        json={"expected_row_version": 1, "title": "Stale overwrite"},
        headers=HDR,
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["detail"]["current_row_version"] == 2

    rollup = client.get(f"/engagements/{engagement.slug}/work-item-rollup", headers=HDR)
    assert rollup.status_code == 200, rollup.text
    assert rollup.json()["engagement"]["remaining"] == 1
    assert rollup.json()["by_finding"][str(finding.id)]["remaining"] == 1


def test_result_accept_is_idempotent_and_effects_are_explicit(
    client: TestClient, db: Session, engagement: Engagement
) -> None:
    work = client.post(
        f"/engagements/{engagement.slug}/work-items",
        json={"title": "Verify secondary host", "executor_type": "finding_agent"},
        headers=HDR,
    )
    assert work.status_code == 201, work.text
    work_id = work.json()["id"]
    result = client.post(
        f"/work-items/{work_id}/results",
        json={
            "summary": "The behavior is shared.",
            "structured": {"confidence": "high", "suggested_effect": {"focus": "gateway"}},
        },
        headers=HDR,
    )
    assert result.status_code == 201, result.text
    result_id = result.json()["id"]

    decision = {
        "expected_work_item_version": 1,
        "resolve_work_item": True,
        "resolution_outcome": "completed",
        "resolution_note": "Compared both hosts.",
        "share_with_strategy": True,
    }
    accepted = client.post(f"/work-item-results/{result_id}/accept", json=decision, headers=HDR)
    assert accepted.status_code == 200, accepted.text
    accepted_body = accepted.json()
    assert accepted_body["result"]["state"] == "accepted"
    assert accepted_body["work_item"]["status"] == "completed"
    assert accepted_body["work_item"]["row_version"] == 2
    assert accepted_body["strategy_signal"]["source_work_item_result_id"] == result_id

    repeated = client.post(f"/work-item-results/{result_id}/accept", json=decision, headers=HDR)
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["work_item"]["row_version"] == 2
    assert (
        db.execute(
            select(StrategySignal).where(
                StrategySignal.source_work_item_result_id == uuid.UUID(result_id)
            )
        )
        .scalars()
        .one()
    )
    assert (
        db.execute(select(WorkItemResult).where(WorkItemResult.id == uuid.UUID(result_id)))
        .scalar_one()
        .state.value
        == "accepted"
    )


def test_archived_completed_and_guest_mutations_are_read_only(
    client: TestClient, db: Session, engagement: Engagement
) -> None:
    engagement.status = EngagementStatus.archived
    db.commit()
    archived = client.post(
        f"/engagements/{engagement.slug}/objectives",
        json={"title": "Must not write"},
        headers=HDR,
    )
    assert archived.status_code == 409, archived.text

    engagement.status = EngagementStatus.active
    engagement.work_state = EngagementWorkState.completed
    db.commit()
    completed = client.post(
        f"/engagements/{engagement.slug}/work-items",
        json={"title": "Must not write"},
        headers=HDR,
    )
    assert completed.status_code == 409, completed.text

    guest = User(
        email=f"strategy-guest-{uuid.uuid4().hex[:6]}@example.test",
        role=UserRole.guest,
    )
    db.add(guest)
    engagement.work_state = EngagementWorkState.active
    db.commit()
    denied = client.post(
        f"/engagements/{engagement.slug}/objectives",
        json={"title": "Guest write"},
        headers={"X-User-Id": guest.email},
    )
    assert denied.status_code == 403, denied.text


def test_checkpoint_and_resume_are_deterministic(
    client: TestClient, engagement: Engagement
) -> None:
    created = client.post(
        f"/engagements/{engagement.slug}/checkpoints",
        json={"narrative": "Analyst handoff"},
        headers=HDR,
    )
    assert created.status_code == 201, created.text
    assert "work_items" in created.json()["facts"]

    resume = client.get(f"/engagements/{engagement.slug}/resume", headers=HDR)
    assert resume.status_code == 200, resume.text
    body = resume.json()
    assert body["since_checkpoint"]["checkpoint_id"] == created.json()["id"]
    assert "checks" in body["report_readiness"]
    assert body["generated_at"]
