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
    FindingStatus,
    ScopeItem,
    ScopeKind,
    Severity,
)


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def engagement(db: Session) -> Iterator[Engagement]:
    row = Engagement(
        name="Finding Hierarchy Test",
        slug=f"finding-hierarchy-{uuid.uuid4().hex[:8]}",
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


def _headers() -> dict[str, str]:
    return {"X-User-Id": "hierarchy@example.com"}


def _finding(
    engagement_id: uuid.UUID,
    *,
    title: str,
    tool: str,
    target: str,
    details: dict,
    severity: Severity = Severity.info,
    status: FindingStatus = FindingStatus.validated,
) -> Finding:
    return Finding(
        engagement_id=engagement_id,
        title=title,
        source_tool=tool,
        target=target,
        details=details,
        severity=severity,
        phase=FindingPhase.osint,
        status=status,
    )


def _seed_projection(db: Session, engagement: Engagement) -> None:
    db.add(
        ScopeItem(
            engagement_id=engagement.id,
            kind=ScopeKind.domain,
            value="example.co.uk",
            is_exclusion=False,
        )
    )
    db.add_all(
        [
            _finding(
                engagement.id,
                title="Open port",
                tool="portscan",
                target="192.0.2.10:443",
                details={"host": "192.0.2.10", "port": 443, "service": "https"},
            ),
            _finding(
                engagement.id,
                title="Service fingerprint",
                tool="service_detect",
                target="192.0.2.10:443",
                details={
                    "host": "192.0.2.10",
                    "port": 443,
                    "protocol": "tcp",
                    "service": "https",
                    "product": "nginx",
                    "version": "1.24",
                },
                severity=Severity.high,
                status=FindingStatus.needs_review,
            ),
            _finding(
                engagement.id,
                title="Subdomains",
                tool="subfinder",
                target="example.co.uk",
                details={
                    "domain": "example.co.uk",
                    "items": [
                        {"subdomain": "api.example.co.uk"},
                        {"subdomain": "admin.example.co.uk"},
                    ],
                },
            ),
        ]
    )
    db.commit()


def test_hierarchy_consolidates_services_and_domains_without_writes(
    client: TestClient, db: Session, engagement: Engagement
) -> None:
    _seed_projection(db, engagement)
    before = db.scalar(
        select(func.count()).select_from(Finding).where(Finding.engagement_id == engagement.id)
    )

    response = client.get(
        f"/engagements/{engagement.slug}/findings/hierarchy",
        headers=_headers(),
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    ip = next(row for row in payload["assets"] if row["kind"] == "ip")
    assert ip["label"] == "Service Detection: IP(192.0.2.10)"
    assert ip["rollup"]["max_severity"] == "high"
    assert ip["rollup"]["needs_review"] == 1
    assert len(ip["children"]) == 1
    service = ip["children"][0]
    assert service["protocol"] == "tcp"
    assert service["port"] == 443
    assert "nginx 1.24" in service["label"]
    assert len(service["finding_refs"]) == 2

    domain = next(row for row in payload["assets"] if row["kind"] == "domain")
    assert domain["label"] == "Domain: example.co.uk"
    assert domain["rollup"]["inventory"] == 1
    assert domain["rollup"]["actionable"] == 0
    assert {row["label"] for row in domain["children"]} == {
        "api.example.co.uk",
        "admin.example.co.uk",
    }
    after = db.scalar(
        select(func.count()).select_from(Finding).where(Finding.engagement_id == engagement.id)
    )
    assert before == after == 3


def test_ipv6_and_hostname_services_use_canonical_separate_assets(
    client: TestClient, db: Session, engagement: Engagement
) -> None:
    db.add_all(
        [
            _finding(
                engagement.id,
                title="IPv6 host",
                tool="ipinfo",
                target="2001:0db8:0:0:0:0:0:1",
                details={"ip": "2001:0db8:0:0:0:0:0:1"},
            ),
            _finding(
                engagement.id,
                title="IPv6 HTTPS",
                tool="service_detect",
                target="[2001:db8::1]:443",
                details={"target": "[2001:db8::1]:443", "service": "https"},
            ),
            _finding(
                engagement.id,
                title="Hostname HTTPS",
                tool="service_detect",
                target="api.example.com:443",
                details={
                    "host": "api.example.com",
                    "port": 443,
                    "protocol": "tcp",
                    "service": "https",
                },
            ),
        ]
    )
    db.commit()
    payload = client.get(
        f"/engagements/{engagement.slug}/findings/hierarchy",
        headers=_headers(),
    ).json()
    ipv6 = next(row for row in payload["assets"] if row["kind"] == "ip")
    assert ipv6["value"] == "2001:db8::1"
    assert ipv6["label"] == "Service Detection: IP(2001:db8::1)"
    assert ipv6["children"][0]["port"] == 443
    assert ipv6["children"][0]["protocol"] == "tcp"
    domain = next(row for row in payload["assets"] if row["kind"] == "domain")
    assert domain["label"] == "Domain: api.example.com"
    assert domain["children"][0]["kind"] == "service"
    assert domain["children"][0]["port"] == 443
    assert domain["children"][0]["hostname"] == "api.example.com"


def test_parent_promotion_captures_descendant_sources(
    client: TestClient, db: Session, engagement: Engagement
) -> None:
    _seed_projection(db, engagement)
    hierarchy = client.get(
        f"/engagements/{engagement.slug}/findings/hierarchy",
        headers=_headers(),
    ).json()
    parent = next(row for row in hierarchy["assets"] if row["kind"] == "ip")
    descendant_ids = {ref["id"] for child in parent["children"] for ref in child["finding_refs"]}
    response = client.post(
        f"/engagements/{engagement.slug}/findings/from-item",
        headers=_headers(),
        json={
            "item_id": parent["id"],
            "title": "Investigate prioritized IP",
            "severity": "high",
            "phase": "general",
            "target": "192.0.2.10",
            "idempotency_key": str(uuid.uuid4()),
            "duplicate_decision": "review",
        },
    )
    assert response.status_code == 200, response.text
    promoted = db.get(Finding, uuid.UUID(response.json()["finding"]["id"]))
    assert promoted is not None
    assert set(promoted.details["hierarchy_promotion"]["source_finding_ids"]) == descendant_ids


def test_duplicate_review_uses_canonical_ipv6_identity(
    client: TestClient, db: Session, engagement: Engagement
) -> None:
    db.add_all(
        [
            _finding(
                engagement.id,
                title="IPv6 inventory",
                tool="ipinfo",
                target="2001:0db8:0:0:0:0:0:1",
                details={"ip": "2001:0db8:0:0:0:0:0:1"},
            ),
            _finding(
                engagement.id,
                title="Existing analyst conclusion",
                tool="manual",
                target="2001:db8::1",
                details={},
                severity=Severity.high,
                status=FindingStatus.pending_validation,
            ),
        ]
    )
    db.commit()
    hierarchy = client.get(
        f"/engagements/{engagement.slug}/findings/hierarchy",
        headers=_headers(),
    ).json()
    item = next(row for row in hierarchy["assets"] if row["kind"] == "ip")
    response = client.post(
        f"/engagements/{engagement.slug}/findings/from-item",
        headers=_headers(),
        json={
            "item_id": item["id"],
            "title": "Another IPv6 conclusion",
            "severity": "high",
            "phase": "general",
            "target": "2001:0db8:0:0:0:0:0:1",
            "idempotency_key": str(uuid.uuid4()),
            "duplicate_decision": "review",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "duplicate_warning"
    assert response.json()["candidates"][0]["title"] == "Existing analyst conclusion"


def test_promotion_rejects_target_drift(
    client: TestClient, db: Session, engagement: Engagement
) -> None:
    _seed_projection(db, engagement)
    hierarchy = client.get(
        f"/engagements/{engagement.slug}/findings/hierarchy",
        headers=_headers(),
    ).json()
    ip = next(row for row in hierarchy["assets"] if row["kind"] == "ip")
    service = ip["children"][0]
    response = client.post(
        f"/engagements/{engagement.slug}/findings/from-item",
        headers=_headers(),
        json={
            "item_id": service["id"],
            "title": "Drifted target",
            "severity": "high",
            "phase": "general",
            "target": "192.0.2.11:443",
            "idempotency_key": str(uuid.uuid4()),
            "duplicate_decision": "review",
        },
    )
    assert response.status_code == 422, response.text
    assert "canonically equivalent" in response.json()["detail"]
    assert (
        db.scalar(
            select(func.count()).select_from(Finding).where(Finding.engagement_id == engagement.id)
        )
        == 3
    )


def test_promotion_is_duplicate_aware_and_preserves_sources(
    client: TestClient, db: Session, engagement: Engagement
) -> None:
    _seed_projection(db, engagement)
    hierarchy = client.get(
        f"/engagements/{engagement.slug}/findings/hierarchy",
        headers=_headers(),
    ).json()
    ip = next(row for row in hierarchy["assets"] if row["kind"] == "ip")
    service = ip["children"][0]
    source_ids = {row["id"] for row in service["finding_refs"]}
    original_details = {
        str(row.id): dict(row.details)
        for row in db.scalars(
            select(Finding).where(Finding.id.in_([uuid.UUID(value) for value in source_ids]))
        )
    }
    body = {
        "item_id": service["id"],
        "title": "Review exposed HTTPS service",
        "summary": "Analyst-promoted inventory item.",
        "severity": "high",
        "phase": "vuln_scan",
        "target": "192.0.2.10:443",
        "idempotency_key": str(uuid.uuid4()),
        "duplicate_decision": "review",
    }
    created = client.post(
        f"/engagements/{engagement.slug}/findings/from-item",
        headers=_headers(),
        json=body,
    )
    assert created.status_code == 200, created.text
    created_payload = created.json()
    assert created_payload["state"] == "created"
    assert created_payload["finding"]["status"] == "pending_validation"
    assert created_payload["finding"]["tool"] == "manual_promotion"
    promoted = db.get(Finding, uuid.UUID(created_payload["finding"]["id"]))
    assert promoted is not None
    assert set(promoted.details["hierarchy_promotion"]["source_finding_ids"]) == source_ids
    refreshed = client.get(
        f"/engagements/{engagement.slug}/findings/hierarchy",
        headers=_headers(),
    ).json()
    refreshed_ip = next(row for row in refreshed["assets"] if row["kind"] == "ip")
    service_rows = [
        row for row in refreshed_ip["children"] if row["port"] == 443 and row["protocol"] == "tcp"
    ]
    assert len(service_rows) == 1
    assert created_payload["finding"]["id"] in {
        row["id"] for row in service_rows[0]["finding_refs"]
    }
    assert not any(row["protocol"] == "unknown" for row in refreshed_ip["children"])

    retried = client.post(
        f"/engagements/{engagement.slug}/findings/from-item",
        headers=_headers(),
        json=body,
    )
    assert retried.status_code == 200
    assert retried.json()["finding"]["id"] == created_payload["finding"]["id"]

    duplicate = client.post(
        f"/engagements/{engagement.slug}/findings/from-item",
        headers=_headers(),
        json={**body, "idempotency_key": str(uuid.uuid4())},
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["state"] == "duplicate_warning"
    assert [row["id"] for row in duplicate.json()["candidates"]] == [
        created_payload["finding"]["id"]
    ]
    db.expire_all()
    for source_id, details in original_details.items():
        source = db.get(Finding, uuid.UUID(source_id))
        assert source is not None
        assert source.deleted_at is None
        assert source.details == details
