"""Finding pane activity timeline tests.

Covers the Phase-1 dossier feed assembled by ``build_finding_activity``:
creation provenance, finding-scoped tasks, agent executions, and audit events.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.main import app
from app.models import (
    ActorType,
    AgentExecution,
    AgentExecutionStatus,
    AgentName,
    AgentTrigger,
    AuditLog,
    Engagement,
    EngagementStatus,
    Finding,
    FindingPhase,
    FindingStatus,
    OwnerEligibility,
    Severity,
    Task,
    TaskKind,
    TaskStatus,
)
from app.services.finding_activity import build_finding_activity

HDR = {"X-User-Id": "finding-activity@example.com"}


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def engagement(db: Session) -> Iterator[Engagement]:
    eng = Engagement(
        name="Finding Activity",
        slug=f"finding-activity-{uuid.uuid4().hex[:8]}",
        status=EngagementStatus.active,
    )
    db.add(eng)
    db.commit()
    db.refresh(eng)
    try:
        yield eng
    finally:
        # audit_log is append-only; use the test helper that bypasses the
        # trigger for this engagement instead of deleting audit rows directly.
        db.execute(text("SELECT flush_engagement(:id)"), {"id": eng.id})
        db.commit()


@pytest.fixture()
def finding(db: Session, engagement: Engagement) -> Finding:
    base = datetime(2026, 7, 8, 12, 0, tzinfo=UTC)
    row = Finding(
        engagement_id=engagement.id,
        title="Interesting exposed host",
        severity=Severity.medium,
        details={"thread_id": "run-123", "evidence": "banner"},
        source_tool="subfinder",
        target="app.example.test",
        phase=FindingPhase.osint,
        status=FindingStatus.validated,
        created_at=base,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_build_finding_activity_merges_sources_in_reverse_chronological_order(
    db: Session, engagement: Engagement, finding: Finding
) -> None:
    base = finding.created_at

    task = Task(
        engagement_id=engagement.id,
        finding_id=finding.id,
        title="Resolve exposed host",
        kind=TaskKind.enum,
        owner_eligibility=OwnerEligibility.agent,
        status=TaskStatus.completed,
        payload={"tool": "dns_lookup"},
        dispatched_at=base + timedelta(minutes=1),
        completed_at=base + timedelta(minutes=2),
    )
    execution = AgentExecution(
        engagement_id=engagement.id,
        agent=AgentName.strategic,
        trigger=AgentTrigger.manual,
        input={"finding_id": str(finding.id)},
        output={"summary": "next step"},
        model_provider="test-provider",
        model_name="test-model",
        status=AgentExecutionStatus.completed,
        started_at=base + timedelta(minutes=3),
        completed_at=base + timedelta(minutes=4),
    )
    # Same engagement, different finding_id: must not leak into this timeline.
    unrelated_execution = AgentExecution(
        engagement_id=engagement.id,
        agent=AgentName.strategic,
        trigger=AgentTrigger.manual,
        input={"finding_id": str(uuid.uuid4())},
        output={},
        status=AgentExecutionStatus.completed,
        started_at=base + timedelta(minutes=5),
    )
    audit = AuditLog(
        engagement_id=engagement.id,
        actor_type=ActorType.user,
        actor_id="analyst@example.com",
        event_type="finding.updated",
        payload={"finding_id": str(finding.id), "changes": {"status": "validated"}},
        created_at=base + timedelta(minutes=6),
    )
    unrelated_audit = AuditLog(
        engagement_id=engagement.id,
        actor_type=ActorType.user,
        actor_id="analyst@example.com",
        event_type="finding.updated",
        payload={"finding_id": str(uuid.uuid4()), "changes": {"title": "other"}},
        created_at=base + timedelta(minutes=7),
    )
    db.add_all([task, execution, unrelated_execution, audit, unrelated_audit])
    db.commit()

    rows = build_finding_activity(db, finding.id)

    assert [r["label"] for r in rows] == [
        "Updated",
        "strategic run",
        "Resolve exposed host",
        "Finding created - subfinder",
    ]
    assert rows[0]["kind"] == "finding.updated"
    assert rows[0]["actor"] == "analyst@example.com"
    assert "validated" in (rows[0]["detail"] or "")
    assert rows[0]["ref_type"] == "audit"

    assert rows[1]["kind"] == "agent_run"
    assert rows[1]["detail"] == "test-provider/test-model · completed"
    assert rows[1]["ref_type"] == "execution"
    assert rows[1]["ref_id"] == str(execution.id)

    assert rows[2]["kind"] == "task"
    assert rows[2]["actor"] == "agent"
    assert rows[2]["detail"] == "enum · completed · agent"
    assert rows[2]["ref_type"] == "task"
    assert rows[2]["ref_id"] == str(task.id)

    assert rows[3]["kind"] == "created"
    assert rows[3]["detail"] == "app.example.test"
    assert rows[3]["ref_type"] == "thread"
    assert rows[3]["ref_id"] == "run-123"


def test_build_finding_activity_returns_empty_for_missing_finding(db: Session) -> None:
    assert build_finding_activity(db, uuid.uuid4()) == []


def test_finding_activity_api_returns_timeline(
    client: TestClient, db: Session, finding: Finding
) -> None:
    db.add(
        AuditLog(
            engagement_id=finding.engagement_id,
            actor_type=ActorType.user,
            actor_id="api-user@example.com",
            event_type="finding.summary_recorded",
            payload={"finding_id": str(finding.id)},
            created_at=finding.created_at + timedelta(minutes=1),
        )
    )
    db.commit()

    resp = client.get(f"/findings/{finding.id}/activity", headers=HDR)

    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["label"] == "Summary recorded"
    assert body[-1]["label"] == "Finding created - subfinder"


def test_get_finding_api_returns_single_finding(
    client: TestClient, finding: Finding
) -> None:
    resp = client.get(f"/findings/{finding.id}", headers=HDR)

    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == str(finding.id)
    assert body["title"] == "Interesting exposed host"
    assert body["target"] == "app.example.test"
