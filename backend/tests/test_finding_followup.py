from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.main import app
from app.models import (
    Engagement,
    EngagementStatus,
    Finding,
    FindingPhase,
    FindingRemediationUpdate,
    FindingRetest,
    FindingStatus,
    PlaybookRun,
    Severity,
    Task,
)

HDR = {"X-User-Id": "followup-analyst@example.com"}


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def finding(db: Session) -> Iterator[Finding]:
    engagement = Engagement(
        name="Follow-up tracking",
        slug=f"followup-{uuid.uuid4().hex[:8]}",
        status=EngagementStatus.active,
    )
    db.add(engagement)
    db.flush()
    row = Finding(
        engagement_id=engagement.id,
        title="Retestable issue",
        severity=Severity.high,
        details={},
        phase=FindingPhase.vuln_scan,
        status=FindingStatus.validated,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    try:
        yield row
    finally:
        db.execute(text("SELECT flush_engagement(:id)"), {"id": engagement.id})
        db.commit()


def test_follow_up_history_is_append_only_and_does_not_execute(
    client: TestClient, db: Session, finding: Finding
) -> None:
    validation_state = (finding.status, finding.validated_at, finding.validated_by)
    task_count = db.scalar(select(func.count()).select_from(Task))
    run_count = db.scalar(select(func.count()).select_from(PlaybookRun))

    first = client.post(
        f"/findings/{finding.id}/remediation-updates",
        headers=HDR,
        json={"status": "in_progress", "note": "Patch is being rolled out."},
    )
    assert first.status_code == 201, first.text
    second = client.post(
        f"/findings/{finding.id}/remediation-updates",
        headers=HDR,
        json={"status": "ready_for_retest", "note": "Client requested verification."},
    )
    assert second.status_code == 201, second.text
    retest = client.post(
        f"/findings/{finding.id}/retests",
        headers=HDR,
        json={"outcome": "partially_fixed", "note": "Primary route fixed; alternate remains."},
    )
    assert retest.status_code == 201, retest.text

    response = client.get(f"/findings/{finding.id}/follow-up", headers=HDR)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["latest_remediation"]["status"] == "ready_for_retest"
    assert body["latest_retest"]["outcome"] == "partially_fixed"
    assert [row["status"] for row in body["remediation_updates"]] == [
        "ready_for_retest",
        "in_progress",
    ]

    db.refresh(finding)
    assert (finding.status, finding.validated_at, finding.validated_by) == validation_state
    assert db.scalar(select(func.count()).select_from(Task)) == task_count
    assert db.scalar(select(func.count()).select_from(PlaybookRun)) == run_count
    assert db.scalar(
        select(func.count()).select_from(FindingRemediationUpdate).where(
            FindingRemediationUpdate.finding_id == finding.id
        )
    ) == 2
    assert db.scalar(
        select(func.count()).select_from(FindingRetest).where(
            FindingRetest.finding_id == finding.id
        )
    ) == 1


def test_follow_up_rejects_unknown_finding(client: TestClient) -> None:
    missing = uuid.uuid4()
    assert client.get(f"/findings/{missing}/follow-up", headers=HDR).status_code == 404
    assert client.post(
        f"/findings/{missing}/retests",
        headers=HDR,
        json={"outcome": "fixed"},
    ).status_code == 404
