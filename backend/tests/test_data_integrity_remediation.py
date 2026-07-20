"""Regression coverage for finding visibility, input bounds, and audit archives."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.api.engagements import (
    MAX_FINDING_IMPORT_BATCH,
    MAX_FINDING_IMPORT_DETAILS_BYTES,
)
from app.main import app
from app.models import (
    Attachment,
    AuditLog,
    Engagement,
    EngagementStatus,
    Entity,
    Finding,
    FindingPhase,
    FindingStatus,
    FindingSummary,
    Observation,
    ObservationFindingLink,
    ScopeItem,
    Severity,
)
from app.schemas.finding import (
    MAX_FINDING_SUMMARY_CHARS,
    MAX_FINDING_TAG_CHARS,
    MAX_FINDING_TAGS,
)

HDR = {"X-User-Id": "data-integrity@example.com"}


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def engagement(db: Session) -> Iterator[Engagement]:
    row = Engagement(
        name="Data Integrity",
        slug=f"data-integrity-{uuid.uuid4().hex[:8]}",
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


def _finding(db: Session, engagement: Engagement) -> Finding:
    row = Finding(
        engagement_id=engagement.id,
        title="Active finding",
        severity=Severity.medium,
        details={"domain": "hidden.example.test"},
        source_tool="manual",
        target="hidden.example.test",
        phase=FindingPhase.general,
        status=FindingStatus.pending_validation,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_soft_deleted_finding_rejects_active_reads_and_mutations_without_orphans(
    client: TestClient,
    db: Session,
    engagement: Engagement,
) -> None:
    finding = _finding(db, engagement)
    observation = Observation(
        engagement_id=engagement.id,
        content="Potential supporting observation",
        phase=FindingPhase.general,
    )
    db.add(observation)
    db.commit()
    db.refresh(observation)

    deleted = client.delete(f"/findings/{finding.id}", headers=HDR)
    assert deleted.status_code == 204, deleted.text

    requests = [
        client.patch(f"/findings/{finding.id}", json={"title": "Must stay hidden"}, headers=HDR),
        client.post(
            f"/findings/{finding.id}/validate",
            json={"decision": "validated"},
            headers=HDR,
        ),
        client.post(
            f"/findings/{finding.id}/summaries",
            json={"body": "Must not be recorded"},
            headers=HDR,
        ),
        client.get(f"/findings/{finding.id}/summaries", headers=HDR),
        client.post(
            f"/findings/{finding.id}/attachments",
            files={"file": ("hidden.txt", b"hidden", "text/plain")},
            headers=HDR,
        ),
        client.get(f"/findings/{finding.id}/attachments", headers=HDR),
        client.get(f"/findings/{finding.id}/observations", headers=HDR),
        client.post(f"/observations/{observation.id}/findings/{finding.id}", headers=HDR),
        client.delete(f"/observations/{observation.id}/findings/{finding.id}", headers=HDR),
        client.get(f"/findings/{finding.id}", headers=HDR),
        client.get(f"/findings/{finding.id}/activity", headers=HDR),
        client.get(f"/findings/{finding.id}/chat", headers=HDR),
        client.get(f"/findings/{finding.id}/context-candidates", headers=HDR),
        client.post(
            f"/findings/{finding.id}/context/promote",
            json={
                "items": [
                    {
                        "type": "domain",
                        "value": "hidden.example.test",
                        "add_to_entities": True,
                        "add_to_scope": True,
                    }
                ]
            },
            headers=HDR,
        ),
    ]
    assert [response.status_code for response in requests] == [404] * len(requests)

    db.expire_all()
    persisted = db.get(Finding, finding.id)
    assert persisted is not None
    assert persisted.deleted_at is not None
    assert persisted.title == "Active finding"
    assert persisted.status is FindingStatus.pending_validation
    assert (
        db.scalar(
            select(func.count(FindingSummary.id)).where(FindingSummary.finding_id == finding.id)
        )
        == 0
    )
    assert (
        db.scalar(select(func.count(Attachment.id)).where(Attachment.finding_id == finding.id)) == 0
    )
    assert (
        db.scalar(
            select(func.count(ObservationFindingLink.finding_id)).where(
                ObservationFindingLink.finding_id == finding.id
            )
        )
        == 0
    )
    assert (
        db.scalar(select(func.count(Entity.id)).where(Entity.engagement_id == engagement.id)) == 0
    )
    assert (
        db.scalar(select(func.count(ScopeItem.id)).where(ScopeItem.engagement_id == engagement.id))
        == 0
    )


def _details_with_size(size: int) -> dict[str, str]:
    empty_size = len(json.dumps({"blob": ""}, separators=(",", ":")).encode())
    return {"blob": "x" * (size - empty_size)}


def test_generic_import_accepts_exact_database_boundaries(
    client: TestClient,
    db: Session,
    engagement: Engagement,
) -> None:
    payload = {
        "title": "t" * 300,
        "summary": "s" * MAX_FINDING_SUMMARY_CHARS,
        "source_tool": "x" * 120,
        "target": "y" * 500,
        "group_key": "g" * 200,
        "burp_serial_number": "b" * 64,
        "tags": [f"{index:02d}-" + "z" * 37 for index in range(MAX_FINDING_TAGS)],
        "details": _details_with_size(MAX_FINDING_IMPORT_DETAILS_BYTES),
    }
    response = client.post(
        f"/engagements/{engagement.slug}/findings/import",
        json=[payload],
        headers=HDR,
    )
    assert response.status_code == 201, response.text
    finding = db.get(Finding, uuid.UUID(response.json()[0]["id"]))
    assert finding is not None
    assert len(finding.title) == 300
    assert len(finding.source_tool or "") == 120
    assert len(finding.target or "") == 500
    assert len(finding.group_key or "") == 200
    assert len(finding.details["items"][0]["burp_serial_number"]) == 64
    assert len(finding.tags) == MAX_FINDING_TAGS
    assert all(len(tag) == MAX_FINDING_TAG_CHARS for tag in finding.tags)


@pytest.mark.parametrize(
    "override",
    [
        {"title": "x" * 301},
        {"title": "   "},
        {"source_tool": "x" * 121},
        {"source_tool": "   "},
        {"target": "x" * 501},
        {"target": "   "},
        {"group_key": "x" * 201},
        {"group_key": "   "},
        {"burp_serial_number": "x" * 65},
        {"burp_serial_number": "   "},
        {"tags": ["x"] * (MAX_FINDING_TAGS + 1)},
        {"tags": ["x" * (MAX_FINDING_TAG_CHARS + 1)]},
        {"details": _details_with_size(MAX_FINDING_IMPORT_DETAILS_BYTES + 1)},
    ],
)
def test_generic_import_rejects_max_plus_one_and_blank_values_with_422(
    client: TestClient,
    engagement: Engagement,
    override: dict[str, object],
) -> None:
    payload: dict[str, object] = {"title": "valid"}
    payload.update(override)
    response = client.post(
        f"/engagements/{engagement.slug}/findings/import",
        json=[payload],
        headers=HDR,
    )
    assert response.status_code == 422, response.text


def test_generic_import_rejects_oversized_batch_with_422(
    client: TestClient, engagement: Engagement
) -> None:
    response = client.post(
        f"/engagements/{engagement.slug}/findings/import",
        json=[{"title": f"finding-{index}"} for index in range(MAX_FINDING_IMPORT_BATCH + 1)],
        headers=HDR,
    )
    assert response.status_code == 422, response.text


def test_finding_update_accepts_boundaries_and_rejects_max_plus_one(
    client: TestClient,
    db: Session,
    engagement: Engagement,
) -> None:
    finding = _finding(db, engagement)
    exact = client.patch(
        f"/findings/{finding.id}",
        json={
            "title": "t" * 300,
            "summary": "s" * MAX_FINDING_SUMMARY_CHARS,
            "tags": [f"{index:02d}-" + "z" * 37 for index in range(MAX_FINDING_TAGS)],
        },
        headers=HDR,
    )
    assert exact.status_code == 200, exact.text

    invalid_payloads = [
        {"title": "t" * 301},
        {"title": "   "},
        {"summary": "s" * (MAX_FINDING_SUMMARY_CHARS + 1)},
        {"tags": ["x"] * (MAX_FINDING_TAGS + 1)},
        {"tags": ["x" * (MAX_FINDING_TAG_CHARS + 1)]},
    ]
    for payload in invalid_payloads:
        response = client.patch(f"/findings/{finding.id}", json=payload, headers=HDR)
        assert response.status_code == 422, (payload.keys(), response.text)


def test_audit_mutations_and_internal_export_include_structured_ledger(
    client: TestClient,
    db: Session,
) -> None:
    created = client.post(
        "/engagements",
        json={
            "name": "Audited engagement",
            "initial_scope": [{"kind": "domain", "value": "  example.test  ", "note": "Primary"}],
        },
        headers=HDR,
    )
    assert created.status_code == 201, created.text
    slug = created.json()["slug"]
    engagement_id = uuid.UUID(created.json()["id"])
    try:
        created_event = db.scalar(
            select(AuditLog).where(
                AuditLog.engagement_id == engagement_id,
                AuditLog.event_type == "engagement.created",
            )
        )
        assert created_event is not None
        assert created_event.actor_id
        assert created_event.payload["initial_scope"] == [
            {
                "kind": "domain",
                "value": "example.test",
                "is_exclusion": False,
                "note": "Primary",
                "source": "defined",
            }
        ]

        updated = client.patch(
            f"/engagements/{slug}",
            json={"name": "Audited renamed", "auto_assess_enabled": False},
            headers=HDR,
        )
        assert updated.status_code == 200, updated.text
        archived = client.patch(f"/engagements/{slug}", json={"status": "archived"}, headers=HDR)
        assert archived.status_code == 200, archived.text
        unarchived = client.patch(f"/engagements/{slug}", json={"status": "active"}, headers=HDR)
        assert unarchived.status_code == 200, unarchived.text

        scope_created = client.post(
            f"/engagements/{slug}/scope",
            json={"kind": "ip", "value": "192.0.2.10"},
            headers=HDR,
        )
        assert scope_created.status_code == 201, scope_created.text
        scope_id = scope_created.json()["id"]
        changed = client.patch(
            f"/engagements/{slug}/scope/{scope_id}",
            json={"is_exclusion": True},
            headers=HDR,
        )
        assert changed.status_code == 200, changed.text

        event_count_before_noops = db.scalar(
            select(func.count(AuditLog.id)).where(AuditLog.engagement_id == engagement_id)
        )
        assert (
            client.patch(
                f"/engagements/{slug}",
                json={"name": "Audited renamed", "auto_assess_enabled": False},
                headers=HDR,
            ).status_code
            == 200
        )
        assert (
            client.patch(
                f"/engagements/{slug}/scope/{scope_id}",
                json={"is_exclusion": True},
                headers=HDR,
            ).status_code
            == 200
        )
        assert (
            db.scalar(
                select(func.count(AuditLog.id)).where(AuditLog.engagement_id == engagement_id)
            )
            == event_count_before_noops
        )

        imported = client.post(
            f"/engagements/{slug}/scope/import",
            json={"text": "198.51.100.0/24\n"},
            headers=HDR,
        )
        assert imported.status_code == 200, imported.text
        removed = client.delete(f"/engagements/{slug}/scope/{scope_id}", headers=HDR)
        assert removed.status_code == 204, removed.text

        rows = list(
            db.scalars(
                select(AuditLog)
                .where(AuditLog.engagement_id == engagement_id)
                .order_by(AuditLog.created_at, AuditLog.id)
            )
        )
        by_type: dict[str, list[AuditLog]] = {}
        for row in rows:
            by_type.setdefault(row.event_type, []).append(row)
            assert row.actor_id
        assert {
            "engagement.updated",
            "engagement.auto_assess_updated",
            "engagement.archived",
            "engagement.unarchived",
            "scope.item.created",
            "scope.item.updated",
            "scope.item.deleted",
            "scope.imported",
        } <= set(by_type)
        assert by_type["engagement.updated"][0].payload == {
            "before": {"name": "Audited engagement"},
            "after": {"name": "Audited renamed"},
        }
        assert by_type["scope.item.updated"][0].payload["before"]["is_exclusion"] is False
        assert by_type["scope.item.updated"][0].payload["after"]["is_exclusion"] is True
        assert by_type["scope.item.deleted"][0].payload["after"] is None
        assert by_type["scope.imported"][0].payload["changes"][0]["after"]["value"] == (
            "198.51.100.0/24"
        )

        internal = client.get(f"/engagements/{slug}/export", headers=HDR)
        assert internal.status_code == 200, internal.text
        internal_body = internal.json()
        assert internal_body["audit_summary"]["count"] == len(rows)
        assert internal_body["audit_ledger_truncated"] is False
        assert internal_body["audit_ledger_limit"] == 1000
        assert [entry["event_id"] for entry in internal_body["audit_ledger"]] == [
            str(row.id) for row in rows
        ]
        assert all(
            set(entry) == {"event_id", "event_type", "actor", "timestamp", "payload"}
            for entry in internal_body["audit_ledger"]
        )

        client_export = client.get(f"/engagements/{slug}/export?omit_excluded=true", headers=HDR)
        assert client_export.status_code == 200, client_export.text
        assert "audit_ledger" not in client_export.json()
        assert "audit_ledger_truncated" not in client_export.json()
        assert "audit_summary" in client_export.json()
    finally:
        db.execute(text("SELECT flush_engagement(:id)"), {"id": engagement_id})
        db.commit()
