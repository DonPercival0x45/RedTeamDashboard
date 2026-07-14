"""Integration coverage for scanner import preview and selected commit."""
from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.main import app
from app.models import AuditLog, Engagement, EngagementStatus, Finding

_HEADERS = {"X-User-Id": "scanner-preview-test@example.com"}


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def engagement(db: Session) -> Iterator[Engagement]:
    row = Engagement(
        name="Scanner preview test",
        slug=f"scanner-preview-{uuid.uuid4().hex[:8]}",
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


def _nessus_item(
    plugin_id: str,
    name: str,
    *,
    severity: int = 2,
    port: int = 443,
) -> str:
    return f"""      <ReportItem
        port="{port}" protocol="tcp" severity="{severity}"
        pluginID="{plugin_id}" pluginName="{name}" pluginFamily="General">
        <synopsis>{name} synopsis</synopsis>
        <description>{name} description</description>
      </ReportItem>"""


def _nessus_xml(*items: str) -> bytes:
    report_items = "\n".join(items)
    return f"""<?xml version="1.0"?>
<NessusClientData_v2>
  <Report name="scanner-preview">
    <ReportHost name="scanner.example.test">
      <HostProperties>
        <tag name="host-fqdn">scanner.example.test</tag>
        <tag name="host-ip">192.0.2.10</tag>
      </HostProperties>
{report_items}
    </ReportHost>
  </Report>
</NessusClientData_v2>""".encode()


def _burp_xml(path: str) -> bytes:
    return f"""<issues exportTime="Mon, 30 Jun 2026 14:22:01 GMT">
      <issue><serialNumber>stable-42</serialNumber><type>1001</type>
      <name>Reflected input</name><host ip="192.0.2.10">https://scanner.example.test</host>
      <path>{path}</path><severity>Medium</severity></issue>
    </issues>""".encode()


def _preview(client: TestClient, engagement: Engagement, raw: bytes):
    return client.post(
        f"/engagements/{engagement.slug}/findings/import/nessus/preview",
        files={"file": ("scan.nessus", raw, "application/xml")},
        headers=_HEADERS,
    )


def _commit(
    client: TestClient,
    engagement: Engagement,
    raw: bytes,
    *,
    file_sha256: str,
    selected_keys: list[str],
):
    return client.post(
        f"/engagements/{engagement.slug}/findings/import/nessus/commit",
        files={"file": ("scan.nessus", raw, "application/xml")},
        data={
            "file_sha256": file_sha256,
            "selected_group_keys": json.dumps(selected_keys),
        },
        headers=_HEADERS,
    )


def _write_counts(db: Session, engagement: Engagement) -> tuple[int, int]:
    db.expire_all()
    finding_count = db.scalar(
        select(func.count())
        .select_from(Finding)
        .where(Finding.engagement_id == engagement.id)
    )
    audit_count = db.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(AuditLog.engagement_id == engagement.id)
    )
    return int(finding_count or 0), int(audit_count or 0)


def test_nessus_preview_is_stable_and_performs_no_writes(
    client: TestClient,
    db: Session,
    engagement: Engagement,
) -> None:
    raw = _nessus_xml(
        _nessus_item("1001", "TLS issue", severity=3),
        _nessus_item("1002", "Header issue", severity=1, port=80),
        _nessus_item("1003", "Informational issue", severity=0),
    )
    before = _write_counts(db, engagement)

    first = _preview(client, engagement, raw)
    second = _preview(client, engagement, raw)

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    first_body = first.json()
    second_body = second.json()
    assert first_body["file_sha256"] == hashlib.sha256(raw).hexdigest()
    assert second_body["file_sha256"] == first_body["file_sha256"]
    assert first_body["total_source_rows"] == 3
    assert first_body["parser_counts"]["total_items"] == 3
    assert first_body["counts"]["groups"] == 3
    assert [group["selection_key"] for group in second_body["groups"]] == [
        group["selection_key"] for group in first_body["groups"]
    ]

    groups = {group["selection_key"]: group for group in first_body["groups"]}
    assert groups["nessus:1001"]["default_selected"] is True
    assert groups["nessus:1002"]["default_selected"] is True
    assert groups["nessus:1003"]["severity"] == "info"
    assert groups["nessus:1003"]["default_selected"] is False
    assert _write_counts(db, engagement) == before == (0, 0)


def test_nessus_commit_hash_mismatch_performs_no_writes(
    client: TestClient,
    db: Session,
    engagement: Engagement,
) -> None:
    raw = _nessus_xml(_nessus_item("2001", "Hash-bound issue"))
    preview = _preview(client, engagement, raw)
    assert preview.status_code == 200, preview.text
    body = preview.json()

    response = _commit(
        client,
        engagement,
        raw + b"\n",
        file_sha256=body["file_sha256"],
        selected_keys=[body["groups"][0]["selection_key"]],
    )

    assert response.status_code == 400
    assert "does not match the preview SHA-256" in response.json()["detail"]
    assert _write_counts(db, engagement) == (0, 0)


def test_nessus_commit_persists_only_selected_group(
    client: TestClient,
    db: Session,
    engagement: Engagement,
) -> None:
    raw = _nessus_xml(
        _nessus_item("3001", "Selected issue", severity=4),
        _nessus_item("3002", "Unselected issue", severity=2, port=8443),
    )
    preview = _preview(client, engagement, raw)
    assert preview.status_code == 200, preview.text
    body = preview.json()

    response = _commit(
        client,
        engagement,
        raw,
        file_sha256=body["file_sha256"],
        selected_keys=["nessus:3001"],
    )

    assert response.status_code == 201, response.text
    committed = response.json()
    assert committed["selected_group_count"] == 1
    assert committed["selected_item_count"] == 1
    assert len(committed["imported"]) == 1
    assert committed["imported"][0]["group_key"] == "nessus:3001"

    db.expire_all()
    findings = list(
        db.scalars(
            select(Finding).where(Finding.engagement_id == engagement.id)
        )
    )
    assert [row.group_key for row in findings] == ["nessus:3001"]
    audit = db.scalars(
        select(AuditLog).where(
            AuditLog.engagement_id == engagement.id,
            AuditLog.event_type == "findings.imported",
        )
    ).one()
    assert audit.payload["count"] == 1
    assert audit.payload["source"] == "nessus_import"
    scanner_audit = db.scalars(
        select(AuditLog).where(
            AuditLog.engagement_id == engagement.id,
            AuditLog.event_type == "scanner_import.committed",
        )
    ).one()
    assert scanner_audit.payload["file_sha256"] == body["file_sha256"]
    assert scanner_audit.payload["selected_group_keys"] == ["nessus:3001"]
    assert scanner_audit.payload["selected_item_count"] == 1


def test_nessus_commit_unknown_key_rejects_atomically(
    client: TestClient,
    db: Session,
    engagement: Engagement,
) -> None:
    raw = _nessus_xml(_nessus_item("4001", "Known issue"))
    preview = _preview(client, engagement, raw)
    assert preview.status_code == 200, preview.text
    body = preview.json()

    response = _commit(
        client,
        engagement,
        raw,
        file_sha256=body["file_sha256"],
        selected_keys=["nessus:4001", "nessus:unknown"],
    )

    assert response.status_code == 400
    assert "unknown scanner preview selection key" in response.json()["detail"]
    assert _write_counts(db, engagement) == (0, 0)


def test_nessus_preview_marks_committed_group_existing_and_unselected(
    client: TestClient,
    db: Session,
    engagement: Engagement,
) -> None:
    raw = _nessus_xml(_nessus_item("5001", "Existing issue"))
    first_preview = _preview(client, engagement, raw)
    assert first_preview.status_code == 200, first_preview.text
    first_body = first_preview.json()
    commit = _commit(
        client,
        engagement,
        raw,
        file_sha256=first_body["file_sha256"],
        selected_keys=["nessus:5001"],
    )
    assert commit.status_code == 201, commit.text
    before_repreview = _write_counts(db, engagement)

    second_preview = _preview(client, engagement, raw)

    assert second_preview.status_code == 200, second_preview.text
    group = second_preview.json()["groups"][0]
    assert group["selection_key"] == "nessus:5001"
    assert group["duplicate_state"] == "existing"
    assert group["duplicate_item_count"] == 1
    assert group["default_selected"] is False
    assert _write_counts(db, engagement) == before_repreview


def test_commit_revives_soft_deleted_group_parent(
    client: TestClient,
    db: Session,
    engagement: Engagement,
) -> None:
    raw = _nessus_xml(_nessus_item("5501", "Revived issue"))
    preview = _preview(client, engagement, raw).json()
    first_commit = _commit(
        client,
        engagement,
        raw,
        file_sha256=preview["file_sha256"],
        selected_keys=["nessus:5501"],
    )
    assert first_commit.status_code == 201, first_commit.text
    row = db.scalars(
        select(Finding).where(
            Finding.engagement_id == engagement.id,
            Finding.group_key == "nessus:5501",
        )
    ).one()
    row.deleted_at = datetime.now(tz=UTC)
    db.commit()

    revived_preview = _preview(client, engagement, raw)
    assert revived_preview.status_code == 200, revived_preview.text
    revived_group = revived_preview.json()["groups"][0]
    assert revived_group["duplicate_state"] == "new"
    assert revived_group["default_selected"] is True
    revived_commit = _commit(
        client,
        engagement,
        raw,
        file_sha256=revived_preview.json()["file_sha256"],
        selected_keys=["nessus:5501"],
    )

    assert revived_commit.status_code == 201, revived_commit.text
    db.refresh(row)
    assert row.deleted_at is None


def test_burp_grouped_serial_dedup_survives_changed_target(
    client: TestClient,
    db: Session,
    engagement: Engagement,
) -> None:
    first_raw = _burp_xml("/first")
    first_preview = client.post(
        f"/engagements/{engagement.slug}/findings/import/burp/preview",
        files={"file": ("burp.xml", first_raw, "application/xml")},
        headers=_HEADERS,
    )
    assert first_preview.status_code == 200, first_preview.text
    first_body = first_preview.json()
    first_commit = client.post(
        f"/engagements/{engagement.slug}/findings/import/burp/commit",
        files={"file": ("burp.xml", first_raw, "application/xml")},
        data={
            "file_sha256": first_body["file_sha256"],
            "selected_group_keys": json.dumps(["burp:1001"]),
        },
        headers=_HEADERS,
    )
    assert first_commit.status_code == 201, first_commit.text

    changed_raw = _burp_xml("/changed")
    changed_preview = client.post(
        f"/engagements/{engagement.slug}/findings/import/burp/preview",
        files={"file": ("burp.xml", changed_raw, "application/xml")},
        headers=_HEADERS,
    )

    assert changed_preview.status_code == 200, changed_preview.text
    group = changed_preview.json()["groups"][0]
    assert group["duplicate_state"] == "existing"
    assert group["duplicate_item_count"] == 1
    assert group["default_selected"] is False


def test_scanner_preview_rejects_oversized_file_before_parsing(
    client: TestClient,
    db: Session,
    engagement: Engagement,
) -> None:
    response = client.post(
        f"/engagements/{engagement.slug}/findings/import/nmap/preview",
        files={"file": ("scan.xml", b"x" * (20 * 1024 * 1024 + 1), "application/xml")},
        headers=_HEADERS,
    )

    assert response.status_code == 413
    assert "20 MB limit" in response.json()["detail"]
    assert _write_counts(db, engagement) == (0, 0)


@pytest.mark.parametrize("source", ["nessus", "burp", "nmap"])
def test_scanner_preview_rejects_malformed_xml(
    source: str,
    client: TestClient,
    db: Session,
    engagement: Engagement,
) -> None:
    response = client.post(
        f"/engagements/{engagement.slug}/findings/import/{source}/preview",
        files={"file": (f"scan.{source}.xml", b"<not-valid-xml", "application/xml")},
        headers=_HEADERS,
    )

    assert response.status_code == 400
    assert "invalid" in response.json()["detail"].lower()
    assert _write_counts(db, engagement) == (0, 0)
