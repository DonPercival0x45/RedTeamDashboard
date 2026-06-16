"""Entity correlation derived from findings (CHARTER Idea 4)."""
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
    eng = Engagement(
        name="Entities Test",
        slug=f"entities-{uuid.uuid4().hex[:8]}",
        status=EngagementStatus.active,
    )
    db.add(eng)
    db.commit()
    db.refresh(eng)
    try:
        yield eng
    finally:
        db.execute(text("SELECT flush_engagement(:id)"), {"id": eng.id})
        db.commit()


def _seed(
    db: Session,
    engagement_id: uuid.UUID,
    *,
    tool: str,
    target: str | None,
    details: dict,
    severity: Severity = Severity.info,
) -> None:
    db.add(
        Finding(
            engagement_id=engagement_id,
            title=f"{tool} → {target}",
            severity=severity,
            details=details,
            source_tool=tool,
            target=target,
            phase=FindingPhase.osint,
            status=FindingStatus.validated,
        )
    )
    db.commit()


def _entities(client: TestClient, slug: str, qs: str = "") -> list[dict]:
    r = client.get(
        f"/engagements/{slug}/entities{qs}",
        headers={"X-User-Id": "ent@example.com"},
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_extracts_ip_cidr_domain_subdomain_email(
    client: TestClient, db: Session, engagement: Engagement
) -> None:
    _seed(
        db, engagement.id, tool="subnet_sweep", target="172.18.0.0/28",
        details={"live_hosts": [{"host": "172.18.0.1", "open_ports": [6379]}]},
    )
    _seed(
        db, engagement.id, tool="subfinder", target="acme.com",
        details={"subdomains": ["www.acme.com", "mail.acme.com"]},
    )
    _seed(
        db, engagement.id, tool="crt_sh", target="acme.com",
        details={"contacts": ["admin@acme.com"]},
    )

    ents = _entities(client, engagement.slug)
    by_type: dict[str, set[str]] = {}
    for e in ents:
        by_type.setdefault(e["type"], set()).add(e["value"])

    assert "172.18.0.0/28" in by_type.get("cidr", set())
    assert "172.18.0.1" in by_type.get("ip", set())
    assert "acme.com" in by_type.get("domain", set())
    assert {"www.acme.com", "mail.acme.com"} <= by_type.get("subdomain", set())
    assert "admin@acme.com" in by_type.get("email", set())


def test_correlates_same_value_across_findings(
    client: TestClient, db: Session, engagement: Engagement
) -> None:
    _seed(
        db, engagement.id, tool="portscan", target="172.18.0.5",
        details={"open_ports": [80]}, severity=Severity.low,
    )
    _seed(
        db, engagement.id, tool="service_detect", target="172.18.0.5",
        details={"services": [{"port": 80, "service": "http"}]},
        severity=Severity.high,
    )

    ip = next(
        e for e in _entities(client, engagement.slug)
        if e["type"] == "ip" and e["value"] == "172.18.0.5"
    )
    assert ip["count"] == 2
    assert len(ip["findings"]) == 2
    # Aggregated severity is the max across disclosing findings.
    assert ip["severity"] == "high"


def test_type_and_query_filters(
    client: TestClient, db: Session, engagement: Engagement
) -> None:
    _seed(
        db, engagement.id, tool="subfinder", target="acme.com",
        details={"subdomains": ["api.acme.com"]},
    )
    _seed(
        db, engagement.id, tool="crt_sh", target="other.com",
        details={"contacts": ["root@other.com"]},
    )

    emails = _entities(client, engagement.slug, "?type=email")
    assert emails and all(e["type"] == "email" for e in emails)

    acme = _entities(client, engagement.slug, "?q=acme")
    assert acme and all("acme" in e["value"] for e in acme)
