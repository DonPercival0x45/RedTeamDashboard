"""WorkItem structured targets + sparse-state discovery seeding.

A WorkItem should reference a concrete in-scope record (scope item / entity /
finding) so it is actionable/dispatchable. When an engagement has scope but no
findings, the strategist must seed discovery work that targets declared scope to
*generate* findings, rather than an empty/review-only queue.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.main import app
from app.models import (
    AgentName,
    Engagement,
    EngagementStatus,
    EngagementWorkState,
    ScopeItem,
    ScopeKind,
    Suggestion,
    SuggestionKind,
    SuggestionStatus,
    User,
    UserRole,
    WorkItem,
    WorkItemExecutor,
)
from app.services.engagement_strategist import _fallback_initial_output
from app.services.suggestion_router import _bootstrap_workspace_from_initial_strategy

HDR = {"X-User-Id": "work-targets@example.com"}


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def engagement(db: Session) -> Iterator[Engagement]:
    row = Engagement(
        name="Work Targets",
        slug=f"work-targets-{uuid.uuid4().hex[:8]}",
        status=EngagementStatus.active,
        work_state=EngagementWorkState.active,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    try:
        yield row
    finally:
        db.execute(text("SELECT flush_engagement(:id)"), {"id": row.id})
        db.commit()


def _scope(db: Session, engagement: Engagement, *, kind: ScopeKind, value: str) -> ScopeItem:
    row = ScopeItem(engagement_id=engagement.id, kind=kind, value=value)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_fallback_proposes_discovery_work_targeting_scope_when_sparse() -> None:
    scope_id = uuid.uuid4()
    dossier = {
        "engagement": {"id": str(uuid.uuid4()), "name": "Freshco"},
        "finding_counts": {},
        "selected_findings": [],
        "scope": [
            {"id": str(scope_id), "kind": "domain", "value": "freshco.example", "excluded": False},
            {"id": str(uuid.uuid4()), "kind": "domain", "value": "out.example", "excluded": True},
        ],
    }
    output = _fallback_initial_output(dossier, "ctxhash", RuntimeError("truncated"))

    discovery = output.work_item_proposals
    assert len(discovery) == 1  # excluded scope filtered out
    assert discovery[0].scope_item_id == scope_id
    assert discovery[0].executor_type == "finding_agent"
    assert discovery[0].priority == "high"
    assert "freshco.example" in discovery[0].title


def test_fallback_skips_discovery_when_findings_present() -> None:
    dossier = {
        "engagement": {"id": str(uuid.uuid4()), "name": "Loaded"},
        "finding_counts": {"validated:high:osint": 3},
        "selected_findings": [{"id": str(uuid.uuid4()), "severity": "high"} for _ in range(3)],
        "scope": [
            {"id": str(uuid.uuid4()), "kind": "domain", "value": "loaded.example", "excluded": False}
        ],
    }
    output = _fallback_initial_output(dossier, "ctxhash", RuntimeError("truncated"))
    assert output.work_item_proposals == []


def test_bootstrap_seeds_discovery_work_items_targeting_scope(
    db: Session, engagement: Engagement
) -> None:
    # Scope present, NO findings -> bootstrap must seed discovery WorkItems that
    # point at concrete scope items and are dispatchable (finding_agent).
    a = _scope(db, engagement, kind=ScopeKind.domain, value="embrey.com")
    b = _scope(db, engagement, kind=ScopeKind.cidr, value="10.0.0.0/24")
    user = User(email=f"seeder-{uuid.uuid4().hex[:8]}@example.com", role=UserRole.user)
    db.add(user)
    db.flush()
    suggestion = Suggestion(
        engagement_id=engagement.id,
        title="Initial strategy",
        kind=SuggestionKind.strategy_revision,
        status=SuggestionStatus.open,
        created_by_agent=AgentName.strategic,
        payload={},
    )
    db.add(suggestion)
    db.flush()

    counts = _bootstrap_workspace_from_initial_strategy(db, suggestion, user_id=user.id)
    db.commit()

    seeded = list(
        db.execute(
            select(WorkItem).where(
                WorkItem.engagement_id == engagement.id,
                WorkItem.scope_item_id.is_not(None),
            )
        ).scalars()
    )
    assert counts["work_items"] >= 2
    assert {row.scope_item_id for row in seeded} == {a.id, b.id}
    assert all(row.executor_type == WorkItemExecutor.finding_agent for row in seeded)
    assert all(row.is_bootstrap is True for row in seeded)
