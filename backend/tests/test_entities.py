"""Entity correlation derived from findings (CHARTER Idea 4)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from types import SimpleNamespace

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
    Entity,
    EntityFindingLink,
    EntityGroup,
    EntityReview,
    EntityReviewScopeLink,
    Finding,
    FindingExclusion,
    FindingPhase,
    FindingStatus,
    ScopeItem,
    ScopeKind,
    Severity,
    User,
    UserRole,
)
from app.services import entity_store
from app.services.entities import classify_entity_relevance


def test_relevance_conservatively_collapses_only_clear_vendor_role_mailboxes() -> None:
    assert classify_entity_relevance("email", "abuse@godaddy.com", "oos") == (
        "likely_third_party",
        "Role mailbox on a domain outside current scope",
    )
    assert (
        classify_entity_relevance("email", "security@client-supplier.example", "oos")[0] == "review"
    )
    assert classify_entity_relevance("email", "abuse@godaddy.com", "live") == ("in_scope", None)


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
        db,
        engagement.id,
        tool="subnet_sweep",
        target="172.18.0.0/28",
        details={"live_hosts": [{"host": "172.18.0.1", "open_ports": [6379]}]},
    )
    _seed(
        db,
        engagement.id,
        tool="subfinder",
        target="acme.com",
        details={"subdomains": ["www.acme.com", "mail.acme.com"]},
    )
    _seed(
        db,
        engagement.id,
        tool="crt_sh",
        target="acme.com",
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


def test_scope_targets_populate_entities_without_duplicate_entry(
    client: TestClient, db: Session, engagement: Engagement
) -> None:
    db.add_all(
        [
            ScopeItem(
                engagement_id=engagement.id,
                kind=ScopeKind.domain,
                value="Scope.Example.",
            ),
            ScopeItem(
                engagement_id=engagement.id,
                kind=ScopeKind.email,
                value="Analyst@Scope.Example",
            ),
            ScopeItem(
                engagement_id=engagement.id,
                kind=ScopeKind.ip,
                value="203.0.113.9",
                is_exclusion=True,
            ),
        ]
    )
    db.commit()

    before = _entities(client, engagement.slug)
    scope_entity = next(
        entity
        for entity in before
        if entity["type"] == "domain" and entity["value"] == "scope.example"
    )
    assert scope_entity["scope_status"] == "live"
    assert scope_entity["count"] == 0
    assert scope_entity["findings"] == []
    email_entity = next(
        entity
        for entity in before
        if entity["type"] == "email" and entity["value"] == "Analyst@scope.example"
    )
    assert email_entity["scope_status"] == "live"
    assert email_entity["count"] == 0
    assert not any(entity["value"] == "203.0.113.9" for entity in before)

    _seed(
        db,
        engagement.id,
        tool="whois_lookup",
        target="scope.example",
        details={"items": [{"domain": "scope.example"}]},
    )
    after = [
        entity
        for entity in _entities(client, engagement.slug)
        if entity["type"] == "domain" and entity["value"] == "scope.example"
    ]
    assert len(after) == 1
    assert after[0]["scope_status"] == "live"
    assert after[0]["count"] == 1
    assert len(after[0]["findings"]) == 1


def test_exclusion_does_not_label_finding_derived_entity_as_live(
    client: TestClient, db: Session, engagement: Engagement
) -> None:
    db.add(
        ScopeItem(
            engagement_id=engagement.id,
            kind=ScopeKind.ip,
            value="203.0.113.9",
            is_exclusion=True,
        )
    )
    _seed(
        db,
        engagement.id,
        tool="freeipapi",
        target="203.0.113.9",
        details={"ip": "203.0.113.9"},
    )

    entity = next(
        item for item in _entities(client, engagement.slug) if item["value"] == "203.0.113.9"
    )
    assert entity["scope_status"] == "excluded"
    assert entity["relevance"] == "excluded"


def test_cascade_exclusion_persists_review_and_marks_related_findings(
    client: TestClient, db: Session, engagement: Engagement
) -> None:
    db.add(
        ScopeItem(
            engagement_id=engagement.id,
            kind=ScopeKind.domain,
            value="target.example",
            is_exclusion=False,
        )
    )
    _seed(
        db,
        engagement.id,
        tool="dns_inventory",
        target="target.example",
        details={"items": [{"domain": "target.example", "a": ["203.0.113.10", "203.0.113.11"]}]},
    )
    _seed(
        db,
        engagement.id,
        tool="freeipapi",
        target="203.0.113.10",
        details={"ip": "203.0.113.10"},
    )
    request = {
        "targets": [{"type": "domain", "value": "target.example"}],
        "action": "exclude",
        "cascade": True,
    }
    preview = client.post(
        f"/engagements/{engagement.slug}/entity-reviews/preview",
        headers={"X-User-Id": "ent@example.com"},
        json=request,
    )
    assert preview.status_code == 200, preview.text
    plan = preview.json()
    assert {row["value"] for row in plan["entities"]} >= {
        "target.example",
        "203.0.113.10",
        "203.0.113.11",
    }
    assert plan["findings_to_mark_out_of_scope"] == 2
    assert plan["exact_include_conflicts"] == 1

    applied = client.post(
        f"/engagements/{engagement.slug}/entity-reviews/apply",
        headers={"X-User-Id": "ent@example.com"},
        json={
            **request,
            "reason": "Vendor-operated reverse DNS branch",
            "preview_sha256": plan["preview_sha256"],
            "remove_conflicting_exact_includes": False,
        },
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["findings_marked_out_of_scope"] == 2
    findings = list(
        db.execute(select(Finding).where(Finding.engagement_id == engagement.id)).scalars()
    )
    assert all(row.exclusion is FindingExclusion.out_of_scope for row in findings)
    reviews = list(
        db.execute(
            select(EntityReview).where(EntityReview.engagement_id == engagement.id)
        ).scalars()
    )
    assert len(reviews) >= 3
    scope = list(
        db.execute(select(ScopeItem).where(ScopeItem.engagement_id == engagement.id)).scalars()
    )
    assert sum(not row.is_exclusion for row in scope) == 1
    assert sum(row.is_exclusion for row in scope) >= 3
    refreshed = _entities(client, engagement.slug)
    root = next(row for row in refreshed if row["value"] == "target.example")
    assert root["scope_status"] == "excluded"
    assert root["review_disposition"] == "excluded"

    restore_request = {**request, "action": "keep"}
    restore_preview = client.post(
        f"/engagements/{engagement.slug}/entity-reviews/preview",
        headers={"X-User-Id": "ent@example.com"},
        json=restore_request,
    )
    assert restore_preview.status_code == 200, restore_preview.text
    restore_plan = restore_preview.json()
    assert restore_plan["managed_exclusions_to_remove"] >= 3
    assert restore_plan["findings_to_restore"] == 2
    restored = client.post(
        f"/engagements/{engagement.slug}/entity-reviews/apply",
        headers={"X-User-Id": "ent@example.com"},
        json={
            **restore_request,
            "reason": "Client confirmed this branch is retained",
            "preview_sha256": restore_plan["preview_sha256"],
            "remove_conflicting_exact_includes": False,
        },
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["findings_restored"] == 2
    assert restored.json()["managed_exclusions_removed"] >= 3
    db.expire_all()
    findings = list(
        db.execute(select(Finding).where(Finding.engagement_id == engagement.id)).scalars()
    )
    assert all(row.exclusion is None for row in findings)
    reviews = list(
        db.execute(
            select(EntityReview).where(EntityReview.engagement_id == engagement.id)
        ).scalars()
    )
    assert all(row.disposition.value == "kept" for row in reviews)
    restored_scope = list(
        db.execute(select(ScopeItem).where(ScopeItem.engagement_id == engagement.id)).scalars()
    )
    assert len(restored_scope) == 1
    assert restored_scope[0].value == "target.example"
    assert restored_scope[0].is_exclusion is False


def test_entity_review_does_not_capture_incidental_or_downgrade_outside_roe(
    client: TestClient, db: Session, engagement: Engagement
) -> None:
    direct = Finding(
        engagement_id=engagement.id,
        title="Direct protected finding",
        severity=Severity.info,
        details={"domain": "target.example"},
        source_tool="dns_lookup",
        target="target.example",
        phase=FindingPhase.osint,
        status=FindingStatus.validated,
        exclusion=FindingExclusion.outside_roe,
    )
    incidental = Finding(
        engagement_id=engagement.id,
        title="Unrelated target mentions shared evidence",
        severity=Severity.info,
        details={"related_domain": "target.example"},
        source_tool="whois_lookup",
        target="unrelated.example",
        phase=FindingPhase.osint,
        status=FindingStatus.validated,
    )
    db.add_all([direct, incidental])
    db.commit()
    request = {
        "targets": [{"type": "domain", "value": "target.example"}],
        "action": "exclude",
        "cascade": False,
    }
    preview = client.post(
        f"/engagements/{engagement.slug}/entity-reviews/preview",
        headers={"X-User-Id": "ent@example.com"},
        json=request,
    )
    assert preview.status_code == 200, preview.text
    plan = preview.json()
    assert plan["finding_ids"] == [str(direct.id)]
    assert plan["findings_to_mark_out_of_scope"] == 0
    applied = client.post(
        f"/engagements/{engagement.slug}/entity-reviews/apply",
        headers={"X-User-Id": "ent@example.com"},
        json={
            **request,
            "reason": "Target is outside the engagement",
            "preview_sha256": plan["preview_sha256"],
        },
    )
    assert applied.status_code == 200, applied.text
    db.expire_all()
    assert db.get(Finding, direct.id).exclusion is FindingExclusion.outside_roe
    assert db.get(Finding, incidental.id).exclusion is None


def test_entity_review_rejects_stale_finding_state(
    client: TestClient, db: Session, engagement: Engagement
) -> None:
    _seed(
        db,
        engagement.id,
        tool="dns_lookup",
        target="target.example",
        details={"domain": "target.example"},
    )
    finding = db.execute(select(Finding).where(Finding.engagement_id == engagement.id)).scalar_one()
    request = {
        "targets": [{"type": "domain", "value": "target.example"}],
        "action": "exclude",
        "cascade": False,
    }
    preview = client.post(
        f"/engagements/{engagement.slug}/entity-reviews/preview",
        headers={"X-User-Id": "ent@example.com"},
        json=request,
    ).json()
    finding.exclusion = FindingExclusion.outside_roe
    db.commit()
    applied = client.post(
        f"/engagements/{engagement.slug}/entity-reviews/apply",
        headers={"X-User-Id": "ent@example.com"},
        json={
            **request,
            "reason": "Stale attempt",
            "preview_sha256": preview["preview_sha256"],
        },
    )
    assert applied.status_code == 409
    assert "preview again" in applied.json()["detail"]


def test_bulk_entity_review_preview_accepts_operator_queue_size(
    client: TestClient, engagement: Engagement
) -> None:
    response = client.post(
        f"/engagements/{engagement.slug}/entity-reviews/preview",
        headers={"X-User-Id": "ent@example.com"},
        json={
            "targets": [
                {"type": "domain", "value": f"host-{index}.example"} for index in range(557)
            ],
            "action": "keep",
            "cascade": False,
        },
    )
    assert response.status_code == 200, response.text
    assert len(response.json()["entities"]) == 557
    assert response.json()["truncated"] is False


def test_colliding_entity_types_share_managed_exclusion_ownership(
    client: TestClient, db: Session, engagement: Engagement
) -> None:
    exclude = {
        "targets": [
            {"type": "domain", "value": "shared.example"},
            {"type": "host", "value": "shared.example"},
        ],
        "action": "exclude",
        "cascade": False,
    }
    preview = client.post(
        f"/engagements/{engagement.slug}/entity-reviews/preview",
        headers={"X-User-Id": "ent@example.com"},
        json=exclude,
    ).json()
    assert preview["exclusions_to_create"] == 1
    applied = client.post(
        f"/engagements/{engagement.slug}/entity-reviews/apply",
        headers={"X-User-Id": "ent@example.com"},
        json={
            **exclude,
            "reason": "Shared vendor identity",
            "preview_sha256": preview["preview_sha256"],
        },
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["exclusions_created"] == 1
    assert (
        len(
            list(
                db.execute(
                    select(ScopeItem).where(ScopeItem.engagement_id == engagement.id)
                ).scalars()
            )
        )
        == 1
    )
    assert len(list(db.execute(select(EntityReviewScopeLink)).scalars())) == 2

    keep_domain = {
        "targets": [{"type": "domain", "value": "shared.example"}],
        "action": "keep",
        "cascade": False,
    }
    keep_preview = client.post(
        f"/engagements/{engagement.slug}/entity-reviews/preview",
        headers={"X-User-Id": "ent@example.com"},
        json=keep_domain,
    ).json()
    assert keep_preview["managed_exclusions_to_remove"] == 0

    keep_host = {
        "targets": [{"type": "host", "value": "shared.example"}],
        "action": "keep",
        "cascade": False,
    }
    host_preview = client.post(
        f"/engagements/{engagement.slug}/entity-reviews/preview",
        headers={"X-User-Id": "ent@example.com"},
        json=keep_host,
    ).json()
    assert host_preview["managed_exclusions_to_remove"] == 0
    host_kept = client.post(
        f"/engagements/{engagement.slug}/entity-reviews/apply",
        headers={"X-User-Id": "ent@example.com"},
        json={
            **keep_host,
            "reason": "Host identity retained",
            "preview_sha256": host_preview["preview_sha256"],
        },
    )
    assert host_kept.status_code == 200, host_kept.text
    stale_domain = client.post(
        f"/engagements/{engagement.slug}/entity-reviews/apply",
        headers={"X-User-Id": "ent@example.com"},
        json={
            **keep_domain,
            "reason": "Domain identity retained",
            "preview_sha256": keep_preview["preview_sha256"],
        },
    )
    assert stale_domain.status_code == 409

    keep_preview = client.post(
        f"/engagements/{engagement.slug}/entity-reviews/preview",
        headers={"X-User-Id": "ent@example.com"},
        json=keep_domain,
    ).json()
    assert keep_preview["managed_exclusions_to_remove"] == 1
    kept = client.post(
        f"/engagements/{engagement.slug}/entity-reviews/apply",
        headers={"X-User-Id": "ent@example.com"},
        json={
            **keep_domain,
            "reason": "Domain identity retained",
            "preview_sha256": keep_preview["preview_sha256"],
        },
    )
    assert kept.status_code == 200, kept.text
    db.expire_all()
    assert (
        db.execute(
            select(ScopeItem).where(ScopeItem.engagement_id == engagement.id)
        ).scalar_one_or_none()
        is None
    )


def test_email_target_is_directly_included_in_entity_review(
    client: TestClient, db: Session, engagement: Engagement
) -> None:
    _seed(
        db,
        engagement.id,
        tool="breach_lookup",
        target="Analyst@Example.com",
        details={"email": "analyst@example.com"},
    )
    finding = db.execute(select(Finding).where(Finding.engagement_id == engagement.id)).scalar_one()
    preview = client.post(
        f"/engagements/{engagement.slug}/entity-reviews/preview",
        headers={"X-User-Id": "ent@example.com"},
        json={
            "targets": [{"type": "email", "value": "analyst@example.com"}],
            "action": "exclude",
            "cascade": False,
        },
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["finding_ids"] == [str(finding.id)]
    assert preview.json()["findings_to_mark_out_of_scope"] == 1


def test_dns_host_values_deduplicate_trailing_dot_variants(
    client: TestClient, db: Session, engagement: Engagement
) -> None:
    _seed(
        db,
        engagement.id,
        tool="dns_lookup",
        target="scope.example",
        details={
            "items": [
                {"type": "CNAME", "value": "Alias.Example."},
                {"cname": ["alias.example."]},
            ]
        },
    )

    aliases = [
        item for item in _entities(client, engagement.slug) if item["value"] == "alias.example"
    ]
    assert len(aliases) == 1


def test_correlates_same_value_across_findings(
    client: TestClient, db: Session, engagement: Engagement
) -> None:
    _seed(
        db,
        engagement.id,
        tool="portscan",
        target="172.18.0.5",
        details={"open_ports": [80]},
        severity=Severity.low,
    )
    _seed(
        db,
        engagement.id,
        tool="service_detect",
        target="172.18.0.5",
        details={"services": [{"port": 80, "service": "http"}]},
        severity=Severity.high,
    )

    ip = next(
        e
        for e in _entities(client, engagement.slug)
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

    candidates = client.get(f"/findings/{finding.id}/context-candidates", headers=headers)
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
    promoted = client.post(f"/findings/{finding.id}/context/promote", json=body, headers=headers)
    assert promoted.status_code == 200, promoted.text
    assert promoted.json()["entities_created"] == 1
    assert promoted.json()["entity_links_created"] == 1
    assert promoted.json()["scope_items_created"] == 1

    scope = db.query(ScopeItem).filter_by(engagement_id=engagement.id).one()
    assert scope.value == "api.acme.com"
    assert scope.source == "found"
    assert db.query(EntityFindingLink).filter_by(finding_id=finding.id).count() == 1

    stored = client.get(f"/engagements/{engagement.slug}/entities/stored", headers=headers)
    assert stored.status_code == 200, stored.text
    promoted_entity = next(row for row in stored.json() if row["value"] == "api.acme.com")
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

    repeated = client.post(f"/findings/{finding.id}/context/promote", json=body, headers=headers)
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["entities_created"] == 0
    assert repeated.json()["entity_links_created"] == 0
    assert repeated.json()["scope_items_created"] == 0


def test_type_and_query_filters(client: TestClient, db: Session, engagement: Engagement) -> None:
    _seed(
        db,
        engagement.id,
        tool="subfinder",
        target="acme.com",
        details={"subdomains": ["api.acme.com"]},
    )
    _seed(
        db,
        engagement.id,
        tool="crt_sh",
        target="other.com",
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
    assert canonical.row_version == 2
    assert canonical.source_tool == "test_import"
    assert canonical.source_attribution == "new.json"
    assert canonical.properties["_rtd_source_history"] == [
        {"source_tool": "legacy", "source_attribution": "legacy-two.json"},
        {"source_tool": "test_import", "source_attribution": "new.json"},
    ]
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

    active = client.get(f"/engagements/{engagement.slug}/entities/stored", headers=headers)
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

    stale_restore = client.post(
        f"/entities/{older.id}/restore",
        headers=headers,
        json={
            "expected_row_version": removed.json()["row_version"],
            "reason": "Stale version must not restore",
        },
    )
    assert stale_restore.status_code == 409
    refreshed_rows = client.get(
        f"/engagements/{engagement.slug}/entities/stored?include_suppressed=true",
        headers=headers,
    )
    refreshed_older = next(row for row in refreshed_rows.json() if row["id"] == str(older.id))
    restored = client.post(
        f"/entities/{older.id}/restore",
        headers=headers,
        json={
            "expected_row_version": refreshed_older["row_version"],
            "reason": "Analyst confirmed this representation is still useful",
        },
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["suppressed"] is False
    assert db.query(Entity).filter_by(id=older.id).one().properties["legacy"] is True
    event_types = {
        row.event_type for row in db.query(AuditLog).filter(AuditLog.engagement_id == engagement.id)
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
    assert (
        db.query(AuditLog)
        .filter_by(
            engagement_id=engagement.id,
            event_type="entities.group_merged_deleted",
        )
        .count()
        == 1
    )

    repeated = client.post(
        f"/entity-groups/{grouped.json()['id']}/merge-delete",
        headers=headers,
        json={
            "expected_row_version": body["canonical_entity"]["group"]["row_version"],
            "reason": "Must not repeat a resolved merge",
        },
    )
    assert repeated.status_code == 409
    assert db.query(EntityGroup).filter_by(engagement_id=engagement.id).count() == 1

    removed_rows = client.get(
        f"/engagements/{engagement.slug}/entities/stored?include_suppressed=true",
        headers=headers,
    )
    removed_legacy = next(row for row in removed_rows.json() if row["id"] == str(legacy.id))
    restored = client.post(
        f"/entities/{legacy.id}/restore",
        headers=headers,
        json={
            "expected_row_version": removed_legacy["row_version"],
            "reason": "Analyst needs the grouped representation active again",
        },
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["suppressed"] is False
    assert restored.json()["group"]["id"] == grouped.json()["id"]
    assert restored.json()["group"]["suppressed_member_count"] == 0


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
    assert (
        client.get(
            f"/engagements/{engagement.slug}/entities/stored", headers=guest_headers
        ).status_code
        == 200
    )
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

    candidates = client.get(f"/findings/{finding.id}/context-candidates", headers=headers)
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


def test_entity_group_rejects_cross_engagement_members(
    client: TestClient, db: Session, engagement: Engagement
) -> None:
    other_engagement = Engagement(
        name="Other entity engagement",
        slug=f"other-entities-{uuid.uuid4().hex[:8]}",
        status=EngagementStatus.active,
    )
    local = Entity(
        engagement_id=engagement.id,
        type="domain",
        value="example.com",
        source_tool="manual",
        properties={},
    )
    db.add(other_engagement)
    db.flush()
    foreign = Entity(
        engagement_id=other_engagement.id,
        type="domain",
        value="Example.COM.",
        source_tool="manual",
        properties={},
    )
    db.add_all([local, foreign])
    db.commit()
    try:
        response = client.post(
            f"/engagements/{engagement.slug}/entity-groups",
            headers={"X-User-Id": "cross-engagement@example.com"},
            json={
                "entity_ids": [str(local.id), str(foreign.id)],
                "canonical_entity_id": str(local.id),
                "reason": "Must not cross engagement boundaries",
            },
        )
        assert response.status_code == 422
        assert db.query(EntityGroup).filter_by(engagement_id=engagement.id).count() == 0
    finally:
        db.execute(text("SELECT flush_engagement(:id)"), {"id": other_engagement.id})
        db.commit()
