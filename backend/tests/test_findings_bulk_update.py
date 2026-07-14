"""Transactional bulk finding triage API."""
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
    Finding,
    FindingPhase,
    FindingStatus,
    Severity,
)


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def engagement(db: Session) -> Iterator[Engagement]:
    row = Engagement(
        name="Bulk triage",
        slug=f"bulk-triage-{uuid.uuid4().hex[:8]}",
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


def seed(db: Session, engagement_id: uuid.UUID, title: str) -> Finding:
    row = Finding(
        engagement_id=engagement_id,
        title=title,
        severity=Severity.info,
        details={},
        source_tool="test",
        phase=FindingPhase.vuln_scan,
        status=FindingStatus.pending_validation,
        tags=[],
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def headers() -> dict[str, str]:
    return {"X-User-Id": "bulk-triage@example.com"}


def test_bulk_validate_is_atomic_and_audited(
    client: TestClient, db: Session, engagement: Engagement
) -> None:
    first = seed(db, engagement.id, "first")
    second = seed(db, engagement.id, "second")
    response = client.post(
        f"/engagements/{engagement.slug}/findings/bulk-update",
        headers=headers(),
        json={
            "finding_ids": [str(first.id), str(second.id)],
            "operation": "set_status",
            "status": "validated",
            "reason": "reviewed scanner batch",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["affected"] == 2
    db.refresh(first)
    db.refresh(second)
    assert first.status is FindingStatus.validated
    assert second.status is FindingStatus.validated
    assert first.validated_by is not None and first.validated_at is not None
    audit = db.execute(
        select(AuditLog).where(
            AuditLog.engagement_id == engagement.id,
            AuditLog.event_type == "findings.bulk_updated",
        )
    ).scalar_one()
    assert audit.payload["count"] == 2
    assert audit.payload["reason"] == "reviewed scanner batch"


def test_bulk_update_rejects_partial_selection(
    client: TestClient, db: Session, engagement: Engagement
) -> None:
    finding = seed(db, engagement.id, "unchanged")
    response = client.post(
        f"/engagements/{engagement.slug}/findings/bulk-update",
        headers=headers(),
        json={
            "finding_ids": [str(finding.id), str(uuid.uuid4())],
            "operation": "set_severity",
            "severity": "critical",
        },
    )
    assert response.status_code == 400
    db.refresh(finding)
    assert finding.severity is Severity.info


def test_bulk_tags_and_clear_exclusion(
    client: TestClient, db: Session, engagement: Engagement
) -> None:
    finding = seed(db, engagement.id, "tagged")
    add = client.post(
        f"/engagements/{engagement.slug}/findings/bulk-update",
        headers=headers(),
        json={
            "finding_ids": [str(finding.id)],
            "operation": "add_tags",
            "tags": [" scanner ", "scanner", "reviewed"],
        },
    )
    assert add.status_code == 200, add.text
    assert add.json()["findings"][0]["tags"] == ["scanner", "reviewed"]

    clear = client.post(
        f"/engagements/{engagement.slug}/findings/bulk-update",
        headers=headers(),
        json={
            "finding_ids": [str(finding.id)],
            "operation": "set_exclusion",
            "exclusion": None,
        },
    )
    assert clear.status_code == 200, clear.text
