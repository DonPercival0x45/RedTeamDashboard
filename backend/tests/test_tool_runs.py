"""Security and persistence contract for deterministic v3 passive tools."""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.main import app
from app.models import (
    AuditLog,
    Engagement,
    EngagementArchitecture,
    EngagementStatus,
    EngagementWorkState,
    Finding,
    FindingStatus,
    ScopeItem,
    ScopeKind,
    User,
    UserRole,
)
from app.services.playbook.executor import StepResult


@pytest.fixture()
def direct_surface(db: Session):
    user = User(
        id=uuid.uuid4(),
        email=f"direct-{uuid.uuid4().hex[:8]}@example.com",
        display_name="Direct tool tester",
        role=UserRole.user,
        is_active=True,
    )
    engagement = Engagement(
        name="Direct tool",
        slug=f"direct-{uuid.uuid4().hex[:8]}",
        status=EngagementStatus.active,
        work_state=EngagementWorkState.active,
        intelligence_architecture=EngagementArchitecture.v3,
    )
    db.add_all([user, engagement])
    db.flush()
    db.add_all(
        [
            ScopeItem(
                engagement_id=engagement.id,
                kind=ScopeKind.domain,
                value="foo.example",
            ),
            ScopeItem(
                engagement_id=engagement.id,
                kind=ScopeKind.domain,
                value="blocked.foo.example",
                is_exclusion=True,
            ),
            ScopeItem(
                engagement_id=engagement.id,
                kind=ScopeKind.ip,
                value="203.0.113.10",
            ),
        ]
    )
    db.commit()
    try:
        yield user, engagement
    finally:
        db.rollback()
        db.execute(text("SELECT flush_engagement(:id)"), {"id": engagement.id})
        db.execute(delete(User).where(User.id == user.id))
        db.commit()


def _headers(user: User) -> dict[str, str]:
    return {"X-User-Id": user.email}


def _whois_result(*_args, **_kwargs) -> StepResult:
    return StepResult(
        ok=True,
        findings_new=99,  # raw counts must not leak into persisted counters
        findings_total=99,
        data={
            "record": {
                "registrar": "Example Registrar",
                "created": "2020-01-01",
            }
        },
    )


def test_direct_tool_rejects_out_of_scope_before_execution(
    db: Session,
    direct_surface,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, engagement = direct_surface
    called = False

    def should_not_run(*_args, **_kwargs):
        nonlocal called
        called = True
        return _whois_result()

    monkeypatch.setattr("app.services.playbook.tools.whois.run", should_not_run)
    with TestClient(app) as client:
        outside = client.post(
            f"/engagements/{engagement.slug}/tools/whois/run",
            headers=_headers(user),
            json={"scope": "evil.example"},
        )
        excluded = client.post(
            f"/engagements/{engagement.slug}/tools/whois/run",
            headers=_headers(user),
            json={"scope": "blocked.foo.example"},
        )
        wrong_kind = client.post(
            f"/engagements/{engagement.slug}/tools/whois/run",
            headers=_headers(user),
            json={"scope": "203.0.113.10"},
        )
    assert outside.status_code == 422
    assert excluded.status_code == 422
    assert wrong_kind.status_code == 422
    assert called is False


def test_direct_tool_cannot_override_validated_target_in_args(
    direct_surface,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, engagement = direct_surface
    called = False

    def should_not_run(*_args, **_kwargs):
        nonlocal called
        called = True
        return _whois_result()

    monkeypatch.setattr("app.services.playbook.tools.whois.run", should_not_run)
    with TestClient(app) as client:
        response = client.post(
            f"/engagements/{engagement.slug}/tools/whois/run",
            headers=_headers(user),
            json={
                "scope": "foo.example",
                "args": {"domain": "evil.example"},
            },
        )
    assert response.status_code == 422
    assert called is False


def test_direct_tool_requires_active_v3_engagement(
    db: Session,
    direct_surface,
) -> None:
    user, engagement = direct_surface
    engagement.intelligence_architecture = EngagementArchitecture.legacy
    db.commit()
    with TestClient(app) as client:
        response = client.post(
            f"/engagements/{engagement.slug}/tools/whois/run",
            headers=_headers(user),
            json={"scope": "foo.example"},
        )
    assert response.status_code == 409


def test_direct_tool_persists_canonical_counts_and_dedups(
    db: Session,
    direct_surface,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, engagement = direct_surface
    monkeypatch.setattr("app.services.playbook.tools.whois.run", _whois_result)

    with TestClient(app) as client:
        first = client.post(
            f"/engagements/{engagement.slug}/tools/whois/run",
            headers=_headers(user),
            json={"scope": "foo.example"},
        )
        second = client.post(
            f"/engagements/{engagement.slug}/tools/whois/run",
            headers=_headers(user),
            json={"scope": "foo.example"},
        )

    assert first.status_code == 200, first.text
    assert first.json()["findings_new"] == 1
    assert first.json()["findings_total"] == 1
    assert second.status_code == 200, second.text
    assert second.json()["findings_new"] == 0
    assert second.json()["findings_total"] == 1

    finding = db.execute(
        select(Finding).where(
            Finding.engagement_id == engagement.id,
            Finding.group_key == "whois:foo.example",
        )
    ).scalar_one()
    assert finding.status is FindingStatus.validated
    assert finding.source_tool == "whois_lookup"
    assert len((finding.details or {}).get("items") or []) == 1

    audits = db.execute(
        select(AuditLog).where(
            AuditLog.engagement_id == engagement.id,
            AuditLog.event_type == "tool.run.direct",
        )
    ).scalars().all()
    assert len(audits) == 2
