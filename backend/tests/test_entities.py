"""Entity correlation derived from findings (CHARTER Idea 4)."""
from __future__ import annotations

import uuid
from collections.abc import Iterator
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.main import app
from app.models import (
    AuditLog,
    Engagement,
    EngagementStatus,
    EngagementWorkState,
    Entity,
    EntityFindingLink,
    EntityGroup,
    Finding,
    FindingPhase,
    FindingStatus,
    ScopeItem,
    Severity,
    User,
    UserRole,
)
from app.services import entity_store


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


def test_finding_context_can_promote_entity_and_found_scope(
    client: TestClient, db: Session, engagement: Engagement
) -> None:
    finding = Finding(
        engagement_id=engagement.id,
        title="Discovered api.acme.com at 172.18.0.5",
        summary="Contact admin@acme.com for ownership.",
        severity=Severity.info,
        details={},
        source_tool="manual",
        target="api.acme.com",
        phase=FindingPhase.osint,
        status=FindingStatus.validated,
    )
    db.add(finding)
    db.commit()
    db.refresh(finding)
    headers = {"X-User-Id": "ent@example.com"}

    candidates = client.get(
        f"/findings/{finding.id}/context-candidates", headers=headers
    )
    assert candidates.status_code == 200, candidates.text
    values = {(row["type"], row["value"]) for row in candidates.json()}
    assert ("domain", "api.acme.com") in values
    assert ("ip", "172.18.0.5") in values
    assert ("email", "admin@acme.com") in values

    body = {
        "items": [
            {
                "type": "domain",
                "value": "api.acme.com",
                "add_to_entities": True,
                "add_to_scope": True,
            }
        ]
    }
    promoted = client.post(
        f"/findings/{finding.id}/context/promote", json=body, headers=headers
    )
    assert promoted.status_code == 200, promoted.text
    assert promoted.json()["entities_created"] == 1
    assert promoted.json()["entity_links_created"] == 1
    assert promoted.json()["scope_items_created"] == 1

    scope = db.query(ScopeItem).filter_by(engagement_id=engagement.id).one()
    assert scope.value == "api.acme.com"
    assert scope.source == "found"
    assert db.query(EntityFindingLink).filter_by(finding_id=finding.id).count() == 1

    stored = client.get(
        f"/engagements/{engagement.slug}/entities/stored", headers=headers
    )
    assert stored.status_code == 200, stored.text
    promoted_entity = next(
        row for row in stored.json() if row["value"] == "api.acme.com"
    )
    assert promoted_entity["finding_refs"] == [
        {
            "id": str(finding.id),
            "title": finding.title,
            "tool": "manual",
            "severity": "info",
            "phase": "osint",
            "status": "validated",
        }
    ]

    repeated = client.post(
        f"/findings/{finding.id}/context/promote", json=body, headers=headers
    )
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["entities_created"] == 0
    assert repeated.json()["entity_links_created"] == 0
    assert repeated.json()["scope_items_created"] == 0


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


def test_duplicate_grouping_and_reversible_suppression_preserve_provenance(
    client: TestClient, db: Session, engagement: Engagement
) -> None:
    finding = Finding(
        engagement_id=engagement.id,
        title="Legacy duplicate provenance",
        severity=Severity.info,
        details={},
        source_tool="manual",
        target="Example.COM.",
        phase=FindingPhase.osint,
        status=FindingStatus.validated,
    )
    older = Entity(
        engagement_id=engagement.id,
        type="domain",
        value="Example.COM.",
        source_tool="legacy",
        source_attribution="legacy-one.json",
        properties={"legacy": True},
    )
    canonical = Entity(
        engagement_id=engagement.id,
        type="domain",
        value="example.com",
        source_tool="legacy",
        source_attribution="legacy-two.json",
        properties={"canonical": True},
    )
    db.add_all([finding, older, canonical])
    db.flush()
    db.add(EntityFindingLink(entity_id=older.id, finding_id=finding.id))
    db.commit()
    headers = {"X-User-Id": "entity-manager@example.com"}

    candidates = client.get(
        f"/engagements/{engagement.slug}/entities/duplicate-candidates",
        headers=headers,
    )
    assert candidates.status_code == 200, candidates.text
    candidate = candidates.json()[0]
    assert candidate["normalized_value"] == "example.com"
    assert {row["id"] for row in candidate["entities"]} == {
        str(older.id),
        str(canonical.id),
    }

    grouped = client.post(
        f"/engagements/{engagement.slug}/entity-groups",
        headers=headers,
        json={
            "entity_ids": [str(older.id), str(canonical.id)],
            "canonical_entity_id": str(canonical.id),
            "reason": "Same DNS identity with legacy formatting",
        },
    )
    assert grouped.status_code == 201, grouped.text
    group = grouped.json()
    assert group["canonical_entity_id"] == str(canonical.id)
    assert db.query(Entity).filter_by(engagement_id=engagement.id).count() == 2
    assert db.query(EntityFindingLink).filter_by(finding_id=finding.id).count() == 1

    # Once grouped, imports resolve to the analyst-selected canonical row.
    inserted, merged = entity_store.persist_entities(
        db,
        engagement=engagement,
        items=[
            SimpleNamespace(
                type="domain",
                value="EXAMPLE.com.",
                properties={"fresh": True},
            )
        ],
        source_tool="test_import",
        source_attribution="new.json",
    )
    db.commit()
    assert (inserted, merged) == (0, 1)
    db.refresh(canonical)
    assert canonical.properties["fresh"] is True
    assert older.properties == {"legacy": True}

    grouped_remove = client.post(
        f"/entities/{older.id}/suppress",
        headers=headers,
        json={"expected_row_version": older.row_version, "reason": "Hide duplicate"},
    )
    assert grouped_remove.status_code == 409

    dissolved = client.post(
        f"/entity-groups/{group['id']}/dissolve",
        headers=headers,
        json={
            "expected_row_version": group["row_version"],
            "reason": "Keep records separate but hide the legacy representation",
        },
    )
    assert dissolved.status_code == 200, dissolved.text

    removed = client.post(
        f"/entities/{older.id}/suppress",
        headers=headers,
        json={"expected_row_version": older.row_version, "reason": "Legacy formatting"},
    )
    assert removed.status_code == 200, removed.text
    assert removed.json()["suppressed"] is True
    assert db.query(EntityFindingLink).filter_by(entity_id=older.id).count() == 1

    active = client.get(
        f"/engagements/{engagement.slug}/entities/stored", headers=headers
    )
    assert str(older.id) not in {row["id"] for row in active.json()}
    including_removed = client.get(
        f"/engagements/{engagement.slug}/entities/stored?include_suppressed=true",
        headers=headers,
    )
    removed_row = next(row for row in including_removed.json() if row["id"] == str(older.id))
    assert removed_row["suppression_reason"] == "Legacy formatting"
    assert removed_row["finding_refs"][0]["id"] == str(finding.id)

    # An exact re-import reuses but never silently restores the suppressed row.
    inserted, merged = entity_store.persist_entities(
        db,
        engagement=engagement,
        items=[SimpleNamespace(type="domain", value="Example.COM.", properties={"seen": 2})],
        source_tool="test_import",
        source_attribution="again.json",
    )
    db.commit()
    assert (inserted, merged) == (0, 1)
    db.refresh(older)
    assert older.suppressed_at is not None
    assert older.properties["seen"] == 2

    restored = client.post(
        f"/entities/{older.id}/restore",
        headers=headers,
        json={
            "expected_row_version": removed.json()["row_version"],
            "reason": "Analyst confirmed this representation is still useful",
        },
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["suppressed"] is False
    assert db.query(Entity).filter_by(id=older.id).one().properties["legacy"] is True
    event_types = {
        row.event_type
        for row in db.query(AuditLog).filter(AuditLog.engagement_id == engagement.id)
    }
    assert {
        "entities.grouped",
        "entities.group_dissolved",
        "entity.suppressed",
        "entity.restored",
    } <= event_types
    assert db.query(EntityGroup).filter_by(engagement_id=engagement.id).count() == 0


def test_group_merge_delete_suppresses_duplicates_and_transfers_provenance(
    client: TestClient, db: Session, engagement: Engagement
) -> None:
    canonical_finding = Finding(
        engagement_id=engagement.id,
        title="Canonical source",
        severity=Severity.info,
        details={},
        source_tool="manual",
        target="example.com",
        phase=FindingPhase.osint,
        status=FindingStatus.validated,
    )
    legacy_finding = Finding(
        engagement_id=engagement.id,
        title="Legacy source",
        severity=Severity.low,
        details={},
        source_tool="manual",
        target="Example.COM.",
        phase=FindingPhase.osint,
        status=FindingStatus.validated,
    )
    canonical = Entity(
        engagement_id=engagement.id,
        type="domain",
        value="example.com",
        source_tool="legacy",
        properties={"canonical": True},
    )
    legacy = Entity(
        engagement_id=engagement.id,
        type="domain",
        value="Example.COM.",
        source_tool="legacy",
        properties={"legacy": True, "canonical": False},
    )
    db.add_all([canonical_finding, legacy_finding, canonical, legacy])
    db.flush()
    db.add_all(
        [
            EntityFindingLink(entity_id=canonical.id, finding_id=canonical_finding.id),
            EntityFindingLink(entity_id=legacy.id, finding_id=legacy_finding.id),
        ]
    )
    db.commit()
    headers = {"X-User-Id": "entity-merge-delete@example.com"}

    grouped = client.post(
        f"/engagements/{engagement.slug}/entity-groups",
        headers=headers,
        json={
            "entity_ids": [str(legacy.id), str(canonical.id)],
            "canonical_entity_id": str(canonical.id),
            "reason": "Same domain identity",
        },
    )
    assert grouped.status_code == 201, grouped.text

    merged = client.post(
        f"/entity-groups/{grouped.json()['id']}/merge-delete",
        headers=headers,
        json={
            "expected_row_version": grouped.json()["row_version"],
            "reason": "Keep canonical active and remove duplicate representation",
        },
    )
    assert merged.status_code == 200, merged.text
    body = merged.json()
    assert body["status"] == "merged_deleted"
    assert body["canonical_entity_id"] == str(canonical.id)
    assert body["suppressed_entity_ids"] == [str(legacy.id)]
    assert body["transferred_link_count"] == 1
    assert body["merged_property_keys"] == ["legacy"]
    assert {ref["id"] for ref in body["canonical_entity"]["finding_refs"]} == {
        str(canonical_finding.id),
        str(legacy_finding.id),
    }

    db.refresh(canonical)
    db.refresh(legacy)
    assert canonical.properties == {"canonical": True, "legacy": True}
    assert legacy.suppressed_at is not None
    assert db.query(EntityFindingLink).filter_by(entity_id=legacy.id).count() == 1
    assert db.query(EntityFindingLink).filter_by(entity_id=canonical.id).count() == 2

    active = client.get(
        f"/engagements/{engagement.slug}/entities/stored",
        headers=headers,
    )
    assert active.status_code == 200, active.text
    active_ids = {row["id"] for row in active.json()}
    assert str(canonical.id) in active_ids
    assert str(legacy.id) not in active_ids
    assert db.query(AuditLog).filter_by(
        engagement_id=engagement.id,
        event_type="entities.group_merged_deleted",
    ).count() == 1


def test_entity_disposition_requires_analyst_current_version_and_mutable_engagement(
    client: TestClient, db: Session, engagement: Engagement
) -> None:
    entity = Entity(
        engagement_id=engagement.id,
        type="ip",
        value="192.0.2.10",
        source_tool="manual",
        properties={},
    )
    db.add(entity)
    db.commit()
    db.refresh(entity)

    guest_headers = {"X-User-Id": "entity-guest@example.com"}
    assert client.get(
        f"/engagements/{engagement.slug}/entities/stored", headers=guest_headers
    ).status_code == 200
    guest = db.query(User).filter_by(email="entity-guest@example.com").one()
    guest.role = UserRole.guest
    db.commit()
    forbidden = client.post(
        f"/entities/{entity.id}/suppress",
        headers=guest_headers,
        json={"expected_row_version": 1, "reason": "Guest must not mutate"},
    )
    assert forbidden.status_code == 403

    analyst_headers = {"X-User-Id": "entity-analyst@example.com"}
    removed = client.post(
        f"/entities/{entity.id}/suppress",
        headers=analyst_headers,
        json={"expected_row_version": 1, "reason": "Outdated record"},
    )
    assert removed.status_code == 200, removed.text
    stale = client.post(
        f"/entities/{entity.id}/restore",
        headers=analyst_headers,
        json={"expected_row_version": 1, "reason": "Stale restore"},
    )
    assert stale.status_code == 409

    engagement.work_state = EngagementWorkState.completed
    db.commit()
    completed = client.post(
        f"/entities/{entity.id}/restore",
        headers=analyst_headers,
        json={
            "expected_row_version": removed.json()["row_version"],
            "reason": "Completed mutation",
        },
    )
    assert completed.status_code == 409

    engagement.work_state = EngagementWorkState.active
    engagement.status = EngagementStatus.archived
    db.commit()
    archived = client.post(
        f"/entities/{entity.id}/restore",
        headers=analyst_headers,
        json={
            "expected_row_version": removed.json()["row_version"],
            "reason": "Archived mutation",
        },
    )
    assert archived.status_code == 409


def test_ambiguous_legacy_identity_blocks_promotion_until_grouped(
    client: TestClient, db: Session, engagement: Engagement
) -> None:
    db.add_all(
        [
            Entity(
                engagement_id=engagement.id,
                type="domain",
                value="API.Example.com.",
                source_tool="legacy",
                properties={},
            ),
            Entity(
                engagement_id=engagement.id,
                type="domain",
                value="api.EXAMPLE.com",
                source_tool="legacy",
                properties={},
            ),
        ]
    )
    finding = Finding(
        engagement_id=engagement.id,
        title="api.example.com discovered",
        severity=Severity.info,
        details={},
        source_tool="manual",
        target="api.example.com",
        phase=FindingPhase.osint,
        status=FindingStatus.validated,
    )
    db.add(finding)
    db.commit()
    db.refresh(finding)
    headers = {"X-User-Id": "ambiguity@example.com"}

    candidates = client.get(
        f"/findings/{finding.id}/context-candidates", headers=headers
    )
    assert candidates.status_code == 200, candidates.text
    domain = next(row for row in candidates.json() if row["value"] == "api.example.com")
    assert domain["entity_id"] is None
    assert len(domain["duplicate_entity_ids"]) == 2

    promoted = client.post(
        f"/findings/{finding.id}/context/promote",
        headers=headers,
        json={
            "items": [
                {
                    "type": "domain",
                    "value": "api.example.com",
                    "add_to_entities": True,
                    "add_to_scope": False,
                }
            ]
        },
    )
    assert promoted.status_code == 409
    assert len(promoted.json()["detail"]["entity_ids"]) == 2
    assert db.query(EntityFindingLink).filter_by(finding_id=finding.id).count() == 0
