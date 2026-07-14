"""Coverage and deterministic engagement completion integration coverage."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.main import app
from app.models import (
    Engagement,
    EngagementStatus,
    EngagementWorkState,
    Finding,
    FindingPhase,
    FindingStatus,
    ScopeItem,
    ScopeKind,
    Severity,
)

HDR = {"X-User-Id": "completion-api@example.com"}


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def engagement(db: Session) -> Iterator[Engagement]:
    row = Engagement(
        name="Completion API",
        slug=f"completion-api-{uuid.uuid4().hex[:8]}",
        status=EngagementStatus.active,
        work_state=EngagementWorkState.active,
    )
    db.add(row)
    db.flush()
    db.add(
        ScopeItem(
            engagement_id=row.id,
            kind=ScopeKind.domain,
            value="example.test",
            is_exclusion=False,
            source="defined",
        )
    )
    db.add(
        Finding(
            engagement_id=row.id,
            title="Confirmed issue",
            summary="Evidence-backed report summary.",
            severity=Severity.medium,
            details={},
            source_tool="manual",
            target="api.example.test",
            phase=FindingPhase.general,
            status=FindingStatus.validated,
        )
    )
    db.commit()
    db.refresh(row)
    try:
        yield row
    finally:
        db.execute(text("SELECT flush_engagement(:id)"), {"id": row.id})
        db.commit()


def test_coverage_gap_requires_explicit_exception(
    client: TestClient, db: Session, engagement: Engagement
) -> None:
    invalid_gap = client.post(
        f"/engagements/{engagement.slug}/coverage",
        headers=HDR,
        json={
            "target_kind": "domain",
            "target_key": "missing-reason.example.test",
            "activity_category": "scanner_coverage",
            "status": "accepted_gap",
        },
    )
    assert invalid_gap.status_code == 422, invalid_gap.text

    coverage = client.post(
        f"/engagements/{engagement.slug}/coverage",
        headers=HDR,
        json={
            "target_kind": "domain",
            "target_key": "example.test",
            "activity_category": "scanner_coverage",
            "status": "deferred",
            "reason": "Client maintenance window ended.",
        },
    )
    assert coverage.status_code == 201, coverage.text
    duplicate = client.post(
        f"/engagements/{engagement.slug}/coverage",
        headers=HDR,
        json={
            "target_kind": "domain",
            "target_key": "example.test",
            "activity_category": "scanner_coverage",
            "status": "deferred",
            "reason": "Duplicate",
        },
    )
    assert duplicate.status_code == 409, duplicate.text

    readiness = client.get(f"/engagements/{engagement.slug}/completion/readiness", headers=HDR)
    assert readiness.status_code == 200, readiness.text
    body = readiness.json()
    assert not body["ready"]
    assert body["accepted_gap_candidates"][0]["ref"]["id"] == coverage.json()["id"]

    review = client.post(
        f"/engagements/{engagement.slug}/completion/review",
        headers=HDR,
        json={
            "expected_work_state_version": 1,
            "readiness_hash": body["readiness_hash"],
            "idempotency_key": "review-1",
        },
    )
    assert review.status_code == 200, review.text

    without_exception = client.post(
        f"/engagements/{engagement.slug}/completion/approve",
        headers=HDR,
        json={
            "expected_work_state_version": 2,
            "readiness_hash": review.json()["readiness"]["readiness_hash"],
            "idempotency_key": "approve-missing-gap",
            "accepted_exceptions": [],
        },
    )
    assert without_exception.status_code == 422, without_exception.text

    approved = client.post(
        f"/engagements/{engagement.slug}/completion/approve",
        headers=HDR,
        json={
            "expected_work_state_version": 2,
            "readiness_hash": review.json()["readiness"]["readiness_hash"],
            "idempotency_key": "approve-1",
            "accepted_exceptions": [
                {
                    "ref": {
                        "type": "coverage_item",
                        "id": coverage.json()["id"],
                    },
                    "rationale": "Accepted with the client's documented time constraint.",
                }
            ],
        },
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["work_state"] == "completed"

    locked = client.post(
        f"/engagements/{engagement.slug}/work-items",
        headers=HDR,
        json={"title": "Must reopen first"},
    )
    assert locked.status_code == 409, locked.text
    scope_locked = client.post(
        f"/engagements/{engagement.slug}/scope",
        headers=HDR,
        json={"kind": "domain", "value": "new.example.test"},
    )
    assert scope_locked.status_code == 409, scope_locked.text
    finding_id = db.query(Finding.id).filter(Finding.engagement_id == engagement.id).scalar()
    finding_locked = client.patch(
        f"/findings/{finding_id}",
        headers=HDR,
        json={"title": "Must reopen before editing"},
    )
    assert finding_locked.status_code == 409, finding_locked.text

    approval_id = approved.json()["decision"]["id"]
    reopened = client.post(
        f"/engagements/{engagement.slug}/completion/reopen",
        headers=HDR,
        json={
            "prior_completion_decision_id": approval_id,
            "expected_work_state_version": 3,
            "reason": "Client added a new formal-scope target.",
            "idempotency_key": "reopen-1",
        },
    )
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["work_state"] == "active"
