from __future__ import annotations

import threading
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor

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
    FindingGroup,
    FindingGroupMember,
    FindingPhase,
    FindingStatus,
    Severity,
    User,
    UserRole,
)


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def engagement(db: Session) -> Iterator[Engagement]:
    row = Engagement(
        name="Finding Group Test",
        slug=f"finding-groups-{uuid.uuid4().hex[:8]}",
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


def _headers(email: str = "groups@example.com") -> dict[str, str]:
    return {"X-User-Id": email}


def _finding(
    engagement_id: uuid.UUID,
    title: str,
    *,
    severity: Severity = Severity.info,
    status: FindingStatus = FindingStatus.pending_validation,
) -> Finding:
    return Finding(
        engagement_id=engagement_id,
        title=title,
        source_tool="manual",
        target=f"{title.lower().replace(' ', '-')}.example",
        details={"marker": title},
        severity=severity,
        phase=FindingPhase.general,
        status=status,
    )


def _seed_findings(db: Session, engagement: Engagement) -> list[Finding]:
    rows = [
        _finding(engagement.id, "Alpha", severity=Severity.low),
        _finding(
            engagement.id,
            "Beta",
            severity=Severity.high,
            status=FindingStatus.validated,
        ),
        _finding(engagement.id, "Gamma", severity=Severity.medium),
    ]
    db.add_all(rows)
    db.commit()
    for row in rows:
        db.refresh(row)
    return rows


def test_create_and_list_group_is_non_destructive_and_idempotent(
    client: TestClient,
    db: Session,
    engagement: Engagement,
) -> None:
    findings = _seed_findings(db, engagement)
    before = {
        row.id: (row.title, row.severity, row.status, row.details, row.deleted_at)
        for row in findings
    }
    idempotency_key = str(uuid.uuid4())
    payload = {
        "name": "Shared exposure chain",
        "rationale": "These Findings support the same analyst narrative.",
        "finding_ids": [str(findings[1].id), str(findings[0].id)],
        "idempotency_key": idempotency_key,
    }

    response = client.post(
        f"/engagements/{engagement.slug}/finding-groups",
        headers=_headers(),
        json=payload,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == payload["name"]
    assert body["row_version"] == 1
    assert [row["finding_id"] for row in body["members"]] == payload["finding_ids"]
    assert body["rollup"] == {
        "member_count": 2,
        "available_members": 2,
        "unavailable_members": 0,
        "max_severity": "high",
        "status_counts": {"pending_validation": 1, "validated": 1},
        "excluded_count": 0,
    }

    repeated = client.post(
        f"/engagements/{engagement.slug}/finding-groups",
        headers=_headers(),
        json=payload,
    )
    assert repeated.status_code == 201
    assert repeated.json()["id"] == body["id"]
    mismatched_retry = client.post(
        f"/engagements/{engagement.slug}/finding-groups",
        headers=_headers(),
        json={**payload, "name": "Different request"},
    )
    assert mismatched_retry.status_code == 409
    assert db.query(FindingGroup).filter_by(engagement_id=engagement.id).count() == 1

    listed = client.get(
        f"/engagements/{engagement.slug}/finding-groups",
        headers=_headers(),
    )
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()] == [body["id"]]

    db.expire_all()
    after_rows = list(db.scalars(select(Finding).where(Finding.id.in_(before))))
    after = {
        row.id: (row.title, row.severity, row.status, row.details, row.deleted_at)
        for row in after_rows
    }
    assert after == before
    audit = db.scalar(
        select(AuditLog).where(
            AuditLog.engagement_id == engagement.id,
            AuditLog.event_type == "finding_group.created",
        )
    )
    assert audit is not None
    assert audit.payload["rationale"] == payload["rationale"]
    assert audit.payload["finding_ids"] == payload["finding_ids"]


def test_concurrent_idempotent_group_creation_returns_one_group(
    client: TestClient,
    db: Session,
    engagement: Engagement,
) -> None:
    findings = _seed_findings(db, engagement)
    payload = {
        "name": "Concurrent group",
        "rationale": "Both requests describe the same draft",
        "finding_ids": [str(findings[0].id), str(findings[1].id)],
        "idempotency_key": str(uuid.uuid4()),
    }
    # Resolve/create the acting user before the concurrent requests so this
    # test isolates group idempotency rather than authentication provisioning.
    assert client.get(
        f"/engagements/{engagement.slug}/finding-groups",
        headers=_headers(),
    ).status_code == 200

    barrier = threading.Barrier(3)

    def create() -> tuple[int, str]:
        with TestClient(app) as concurrent_client:
            barrier.wait()
            response = concurrent_client.post(
                f"/engagements/{engagement.slug}/finding-groups",
                headers=_headers(),
                json=payload,
            )
            return response.status_code, response.json().get("id", response.text)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(create) for _index in range(2)]
        barrier.wait()
        results = [future.result() for future in futures]

    assert [status_code for status_code, _group_id in results] == [201, 201]
    assert len({group_id for _status_code, group_id in results}) == 1
    assert db.query(FindingGroup).filter_by(engagement_id=engagement.id).count() == 1


def test_group_update_is_versioned_and_allows_overlapping_projections(
    client: TestClient,
    db: Session,
    engagement: Engagement,
) -> None:
    findings = _seed_findings(db, engagement)
    first = client.post(
        f"/engagements/{engagement.slug}/finding-groups",
        headers=_headers(),
        json={
            "name": "First",
            "rationale": "First rationale",
            "finding_ids": [str(findings[0].id), str(findings[1].id)],
            "idempotency_key": str(uuid.uuid4()),
        },
    ).json()
    second = client.post(
        f"/engagements/{engagement.slug}/finding-groups",
        headers=_headers(),
        json={
            "name": "Second",
            "rationale": "Overlapping projection",
            "finding_ids": [str(findings[1].id), str(findings[2].id)],
            "idempotency_key": str(uuid.uuid4()),
        },
    )
    assert second.status_code == 201, second.text

    update_payload = {
        "expected_row_version": first["row_version"],
        "name": "Updated first",
        "rationale": "Reordered and expanded",
        "finding_ids": [
            str(findings[2].id),
            str(findings[1].id),
            str(findings[0].id),
        ],
    }
    updated = client.put(
        f"/engagements/{engagement.slug}/finding-groups/{first['id']}",
        headers=_headers(),
        json=update_payload,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["row_version"] == 2
    assert [row["finding_id"] for row in updated.json()["members"]] == update_payload[
        "finding_ids"
    ]
    update_audit = db.scalar(
        select(AuditLog)
        .where(
            AuditLog.engagement_id == engagement.id,
            AuditLog.event_type == "finding_group.updated",
        )
        .order_by(AuditLog.created_at.desc())
    )
    assert update_audit is not None
    assert update_audit.payload["previous"]["finding_ids"] == [
        str(findings[0].id),
        str(findings[1].id),
    ]

    stale = client.put(
        f"/engagements/{engagement.slug}/finding-groups/{first['id']}",
        headers=_headers(),
        json={**update_payload, "name": "Stale"},
    )
    assert stale.status_code == 409
    stale_delete = client.delete(
        f"/engagements/{engagement.slug}/finding-groups/{first['id']}",
        headers=_headers(),
        params={"expected_row_version": 1},
    )
    assert stale_delete.status_code == 409
    assert db.query(FindingGroupMember).filter_by(finding_id=findings[1].id).count() == 2


def test_group_validation_and_dissolve_leave_findings_untouched(
    client: TestClient,
    db: Session,
    engagement: Engagement,
) -> None:
    findings = _seed_findings(db, engagement)
    other = Engagement(
        name="Other",
        slug=f"other-{uuid.uuid4().hex[:8]}",
        status=EngagementStatus.active,
    )
    db.add(other)
    db.flush()
    foreign = _finding(other.id, "Foreign")
    db.add(foreign)
    db.commit()

    duplicate = client.post(
        f"/engagements/{engagement.slug}/finding-groups",
        headers=_headers(),
        json={
            "name": "Duplicate",
            "rationale": "Should fail",
            "finding_ids": [str(findings[0].id), str(findings[0].id)],
            "idempotency_key": str(uuid.uuid4()),
        },
    )
    assert duplicate.status_code == 422
    cross = client.post(
        f"/engagements/{engagement.slug}/finding-groups",
        headers=_headers(),
        json={
            "name": "Cross engagement",
            "rationale": "Should fail atomically",
            "finding_ids": [str(findings[0].id), str(foreign.id)],
            "idempotency_key": str(uuid.uuid4()),
        },
    )
    assert cross.status_code == 422
    assert db.query(FindingGroup).filter_by(engagement_id=engagement.id).count() == 0

    created = client.post(
        f"/engagements/{engagement.slug}/finding-groups",
        headers=_headers(),
        json={
            "name": "Temporary",
            "rationale": "Presentation only",
            "finding_ids": [str(findings[0].id), str(findings[1].id)],
            "idempotency_key": str(uuid.uuid4()),
        },
    ).json()
    deleted = client.delete(
        f"/engagements/{engagement.slug}/finding-groups/{created['id']}",
        headers=_headers(),
        params={"expected_row_version": created["row_version"]},
    )
    assert deleted.status_code == 204
    assert db.get(FindingGroup, uuid.UUID(created["id"])) is None
    assert all(db.get(Finding, finding.id).deleted_at is None for finding in findings)
    delete_audit = db.scalar(
        select(AuditLog).where(
            AuditLog.engagement_id == engagement.id,
            AuditLog.event_type == "finding_group.deleted",
        )
    )
    assert delete_audit is not None
    assert delete_audit.payload["rationale"] == "Presentation only"

    db.execute(text("SELECT flush_engagement(:id)"), {"id": other.id})
    db.commit()


def test_guest_can_read_groups_but_cannot_create(
    client: TestClient,
    db: Session,
    engagement: Engagement,
) -> None:
    findings = _seed_findings(db, engagement)
    guest = User(
        email=f"guest-{uuid.uuid4().hex[:8]}@example.com",
        display_name="Guest",
        role=UserRole.guest,
        is_active=True,
    )
    db.add(guest)
    db.commit()
    payload = {
        "name": "Guest attempt",
        "rationale": "Must be denied",
        "finding_ids": [str(findings[0].id), str(findings[1].id)],
        "idempotency_key": str(uuid.uuid4()),
    }
    denied = client.post(
        f"/engagements/{engagement.slug}/finding-groups",
        headers=_headers(guest.email),
        json=payload,
    )
    assert denied.status_code == 403
    created = client.post(
        f"/engagements/{engagement.slug}/finding-groups",
        headers=_headers(),
        json={**payload, "idempotency_key": str(uuid.uuid4())},
    ).json()
    denied_update = client.put(
        f"/engagements/{engagement.slug}/finding-groups/{created['id']}",
        headers=_headers(guest.email),
        json={
            "expected_row_version": created["row_version"],
            "name": "Denied update",
            "rationale": payload["rationale"],
            "finding_ids": payload["finding_ids"],
        },
    )
    assert denied_update.status_code == 403
    denied_delete = client.delete(
        f"/engagements/{engagement.slug}/finding-groups/{created['id']}",
        headers=_headers(guest.email),
        params={"expected_row_version": created["row_version"]},
    )
    assert denied_delete.status_code == 403
    readable = client.get(
        f"/engagements/{engagement.slug}/finding-groups",
        headers=_headers(guest.email),
    )
    assert readable.status_code == 200
