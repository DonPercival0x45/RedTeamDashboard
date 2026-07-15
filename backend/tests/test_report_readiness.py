"""Engagement report-readiness preflight."""
from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.main import app
from app.models import (
    Attachment,
    Engagement,
    EngagementStatus,
    Finding,
    FindingPhase,
    FindingStatus,
    OwnerEligibility,
    ScopeItem,
    ScopeKind,
    Severity,
    Task,
    TaskKind,
    TaskStatus,
)


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def engagement(db: Session) -> Iterator[Engagement]:
    row = Engagement(
        name="Readiness",
        slug=f"readiness-{uuid.uuid4().hex[:8]}",
        status=EngagementStatus.active,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    try:
        yield row
    finally:
        db.execute(text("SELECT flush_engagement(:id)"), {"id": row.id})
        db.commit()


def headers() -> dict[str, str]:
    return {"X-User-Id": "readiness@example.com"}


def test_readiness_reports_blocking_finding_ids(
    client: TestClient, db: Session, engagement: Engagement
) -> None:
    pending = Finding(
        engagement_id=engagement.id,
        title="Needs review",
        severity=Severity.high,
        details={},
        phase=FindingPhase.vuln_scan,
        status=FindingStatus.pending_validation,
    )
    db.add(pending)
    db.commit()
    db.refresh(pending)

    response = client.get(
        f"/engagements/{engagement.slug}/report/readiness", headers=headers()
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ready"] is False
    checks = {check["key"]: check for check in body["checks"]}
    assert checks["pending_validation"]["count"] == 1
    assert checks["pending_validation"]["finding_ids"] == [str(pending.id)]
    assert checks["formal_scope"]["count"] == 1
    assert checks["reportable_findings"]["count"] == 1


def test_deferred_task_has_specific_resolution_blocker(
    client: TestClient, db: Session, engagement: Engagement
) -> None:
    db.add(
        Task(
            engagement_id=engagement.id,
            title="Deferred enumeration",
            kind=TaskKind.enum,
            owner_eligibility=OwnerEligibility.agent,
            status=TaskStatus.deferred,
            payload={"tool": "portscan", "target": "203.0.113.20"},
        )
    )
    db.commit()

    response = client.get(
        f"/engagements/{engagement.slug}/report/readiness", headers=headers()
    )
    assert response.status_code == 200, response.text
    checks = {check["key"]: check for check in response.json()["checks"]}
    assert checks["deferred_work"]["count"] == 1
    assert checks["deferred_work"]["target_view"].startswith("status")
    assert checks["active_work"]["count"] == 0


def test_complete_finding_with_scope_and_evidence_is_ready(
    client: TestClient, db: Session, engagement: Engagement
) -> None:
    db.add(
        ScopeItem(
            engagement_id=engagement.id,
            kind=ScopeKind.domain,
            value="example.test",
            is_exclusion=False,
            source="defined",
        )
    )
    finding = Finding(
        engagement_id=engagement.id,
        title="Reportable",
        summary="A complete analyst-reviewed narrative.",
        target="example.test",
        severity=Severity.medium,
        details={},
        phase=FindingPhase.vuln_scan,
        status=FindingStatus.validated,
    )
    db.add(finding)
    db.flush()
    db.add(
        Attachment(
            finding_id=finding.id,
            engagement_id=engagement.id,
            filename="evidence.txt",
            content_type="text/plain",
            size_bytes=8,
            data=b"evidence",
            created_by="readiness@example.com",
        )
    )
    db.commit()

    response = client.get(
        f"/engagements/{engagement.slug}/report/readiness", headers=headers()
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ready"] is True
    assert body["reportable_count"] == 1
    checks = {check["key"]: check for check in body["checks"]}
    assert checks["missing_summary"]["count"] == 0
    assert checks["missing_evidence"]["count"] == 0
    assert checks["formal_scope"]["count"] == 0
