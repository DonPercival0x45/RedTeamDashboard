"""PDF report endpoint.

Seeds an engagement with scope, a finding, an approval, and an audit-log
entry, then hits GET /engagements/{slug}/report and checks the response is
a non-trivial PDF.
"""
from __future__ import annotations

import sys
import uuid
from collections.abc import Iterator
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.main import app
from app.models import (
    ActorType,
    Approval,
    ApprovalStatus,
    AuditLog,
    Engagement,
    EngagementStatus,
    Finding,
    FindingExclusion,
    FindingPhase,
    FindingStatus,
    Observation,
    RiskLevel,
    ScopeItem,
    ScopeKind,
    Severity,
)

# WeasyPrint needs GTK shared libraries (libgobject-2.0, pango, etc.) which
# aren't available on Windows dev machines. Skip PDF-rendering tests there;
# they run cleanly in CI on the Ubuntu runner where GTK is installed.
_weasyprint_ok: bool
try:
    import weasyprint  # noqa: F401

    _weasyprint_ok = True
except OSError:
    _weasyprint_ok = False

_needs_gtk = pytest.mark.skipif(
    not _weasyprint_ok, reason="WeasyPrint GTK libraries not available on this host"
)


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def engagement(db: Session) -> Iterator[Engagement]:
    eng = Engagement(
        name="Acme Report Test",
        slug=f"report-test-{uuid.uuid4().hex[:8]}",
        status=EngagementStatus.active,
    )
    db.add(eng)
    db.commit()
    db.refresh(eng)
    try:
        yield eng
    finally:
        db.execute(
            text("DELETE FROM approvals WHERE engagement_id = :id"),
            {"id": eng.id},
        )
        db.commit()
        db.execute(text("SELECT flush_engagement(:id)"), {"id": eng.id})
        db.commit()


def _seed_data(db: Session, engagement_id: uuid.UUID) -> None:
    db.add(
        ScopeItem(
            engagement_id=engagement_id,
            kind=ScopeKind.domain,
            value="acme.com",
            is_exclusion=False,
        )
    )
    db.add(
        Finding(
            engagement_id=engagement_id,
            title="subfinder → acme.com",
            severity=Severity.info,
            details={"subdomains": ["www.acme.com", "mail.acme.com"]},
            source_tool="subfinder",
            target="acme.com",
            phase=FindingPhase.osint,
            # Report only includes validated findings (Phase 8 gate).
            status=FindingStatus.validated,
        )
    )
    db.add(
        Approval(
            engagement_id=engagement_id,
            thread_id=str(uuid.uuid4()),
            node="tool_dispatch",
            tool_name="portscan",
            tool_args={"ip": "10.0.0.5"},
            risk=RiskLevel.active,
            scope_check={"ok": True},
            status=ApprovalStatus.approved,
        )
    )
    db.add(
        AuditLog(
            engagement_id=engagement_id,
            actor_type=ActorType.agent,
            actor_id="worker",
            event_type="run.started",
            payload={"thread_id": "abc-123", "prompt": "enumerate acme.com"},
        )
    )
    db.commit()


@_needs_gtk
def test_report_renders_pdf(
    client: TestClient, db: Session, engagement: Engagement
) -> None:
    _seed_data(db, engagement.id)

    response = client.get(
        f"/engagements/{engagement.slug}/report",
        headers={"X-User-Id": "report-test@example.com"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"].startswith("attachment;")
    # PDF magic bytes
    assert response.content.startswith(b"%PDF-")
    # And it's at least a few KB — real content, not a stub.
    assert len(response.content) > 2_000


@_needs_gtk
def test_report_includes_observations(
    client: TestClient, db: Session, engagement: Engagement
) -> None:
    db.add(
        Observation(
            engagement_id=engagement.id,
            content="Certificate expires in 14 days",
            phase=FindingPhase.osint,
        )
    )
    db.commit()

    resp = client.get(
        f"/engagements/{engagement.slug}/report",
        headers={"X-User-Id": "report-test@example.com"},
    )
    assert resp.status_code == 200
    assert resp.content.startswith(b"%PDF-")


def _seed_export_profile_findings(db: Session, engagement_id: uuid.UUID) -> None:
    db.add_all(
        [
            Finding(
                engagement_id=engagement_id,
                title="Client-safe finding",
                severity=Severity.low,
                details={},
                source_tool="manual",
                target="acme.com",
                phase=FindingPhase.general,
                status=FindingStatus.validated,
            ),
            Finding(
                engagement_id=engagement_id,
                title="Internal-only excluded finding",
                severity=Severity.info,
                details={},
                source_tool="manual",
                target="archive.acme.com",
                phase=FindingPhase.general,
                status=FindingStatus.validated,
                exclusion=FindingExclusion.out_of_scope,
            ),
        ]
    )
    db.commit()


def test_json_export_preserves_internal_default_and_supports_client_profile(
    client: TestClient, db: Session, engagement: Engagement
) -> None:
    _seed_export_profile_findings(db, engagement.id)
    headers = {"X-User-Id": "report-test@example.com"}

    internal_export = client.get(
        f"/engagements/{engagement.slug}/export", headers=headers
    )
    assert internal_export.status_code == 200, internal_export.text
    internal_body = internal_export.json()
    assert internal_body["export_profile"] == "internal"
    assert internal_body["omit_excluded"] is False
    assert {row["title"] for row in internal_body["findings"]} == {
        "Client-safe finding",
        "Internal-only excluded finding",
    }

    client_export = client.get(
        f"/engagements/{engagement.slug}/export?omit_excluded=true",
        headers=headers,
    )
    assert client_export.status_code == 200, client_export.text
    client_body = client_export.json()
    assert client_body["export_profile"] == "client"
    assert client_body["omit_excluded"] is True
    assert client_body["excluded_count"] == 1
    assert [row["title"] for row in client_body["findings"]] == [
        "Client-safe finding"
    ]


def test_pdf_export_defaults_client_safe_and_internal_is_explicit(
    client: TestClient,
    db: Session,
    engagement: Engagement,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_export_profile_findings(db, engagement.id)
    rendered_titles: list[list[str]] = []

    class FakeTemplate:
        def render(self, **context: object) -> str:
            findings = context["findings"]
            rendered_titles.append([row.title for row in findings])  # type: ignore[attr-defined]
            return "<html></html>"

    class FakeHTML:
        def __init__(self, *, string: str) -> None:
            assert string == "<html></html>"

        def write_pdf(self) -> bytes:
            return b"%PDF-fake"

    from app.api import reports as reports_api

    monkeypatch.setattr(reports_api._env, "get_template", lambda _name: FakeTemplate())
    monkeypatch.setitem(sys.modules, "weasyprint", SimpleNamespace(HTML=FakeHTML))
    headers = {"X-User-Id": "report-test@example.com"}

    client_report = client.get(
        f"/engagements/{engagement.slug}/report", headers=headers
    )
    assert client_report.status_code == 200, client_report.text
    assert "client-report" in client_report.headers["content-disposition"]
    assert rendered_titles[-1] == ["Client-safe finding"]

    internal_report = client.get(
        f"/engagements/{engagement.slug}/report?omit_excluded=false",
        headers=headers,
    )
    assert internal_report.status_code == 200, internal_report.text
    assert "internal-report" in internal_report.headers["content-disposition"]
    assert set(rendered_titles[-1]) == {
        "Client-safe finding",
        "Internal-only excluded finding",
    }


def test_report_404_for_unknown_engagement(client: TestClient) -> None:
    response = client.get(
        f"/engagements/does-not-exist-{uuid.uuid4().hex[:6]}/report",
        headers={"X-User-Id": "report-test@example.com"},
    )
    assert response.status_code == 404


def test_report_requires_x_user_id(
    client: TestClient, engagement: Engagement
) -> None:
    response = client.get(f"/engagements/{engagement.slug}/report")
    assert response.status_code == 401
