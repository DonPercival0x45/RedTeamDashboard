"""Engagement Strategist reassess closed loop.

Reassess reviews completed/in-flight work + findings and proposes NET-NEW work
items. It must not re-propose work that already exists as a WorkItem (any
status), and the reassess-specific prompt (not the generic one) must drive the
run.

The strategist services are imported lazily inside the test (see
test_strategy_work_targets.py) to avoid loading their import chain at pytest
collection time.
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import (
    Engagement,
    EngagementStatus,
    EngagementWorkState,
    Finding,
    FindingPhase,
    FindingStatus,
    ScopeItem,
    ScopeKind,
    Severity,
    User,
    UserRole,
    WorkItem,
    WorkItemExecutor,
    WorkItemPriority,
    WorkItemStatus,
)


@pytest.fixture()
def engagement(db: Session) -> Iterator[Engagement]:
    row = Engagement(
        name="Reassess Loop",
        slug=f"reassess-{uuid.uuid4().hex[:8]}",
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


def test_reassess_proposes_net_new_work_and_skips_existing(
    db: Session,
    engagement: Engagement,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import engagement_strategist as service
    from app.services.engagement_strategist import run_engagement_strategist

    scope = ScopeItem(
        engagement_id=engagement.id, kind=ScopeKind.domain, value="acme.example"
    )
    db.add(scope)
    db.flush()
    db.add(
        Finding(
            engagement_id=engagement.id,
            title="Live HTTP surface",
            target="acme.example",
            severity=Severity.info,
            status=FindingStatus.validated,
            phase=FindingPhase.osint,
        )
    )
    # A completed work item the reassess must NOT re-propose.
    db.add(
        WorkItem(
            engagement_id=engagement.id,
            title="Enumerate acme.example subdomains",
            scope_item_id=scope.id,
            executor_type=WorkItemExecutor.finding_agent,
            priority=WorkItemPriority.medium,
            status=WorkItemStatus.completed,
        )
    )
    user = User(email=f"reassess-{uuid.uuid4().hex[:8]}@example.com", role=UserRole.user)
    db.add(user)
    db.commit()

    captured: dict[str, list] = {}

    class FakeLLM:
        def invoke(self, messages: list[tuple[str, str]]) -> SimpleNamespace:
            captured["messages"] = messages
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "situation_summary": "Reassessed.",
                        "facts": [],
                        "inferences": [],
                        "hypotheses": [],
                        "work_item_proposals": [
                            {
                                # Duplicate of the completed work item -> deduped.
                                "proposal_key": "llm-dup-key",
                                "title": "Enumerate acme.example subdomains",
                                "scope_item_id": str(scope.id),
                                "executor_type": "finding_agent",
                                "priority": "medium",
                            },
                            {
                                # Net-new -> persisted as a suggestion.
                                "proposal_key": "llm-new-key",
                                "title": "Probe acme.example exposed services",
                                "scope_item_id": str(scope.id),
                                "executor_type": "analyst",
                                "priority": "high",
                            },
                        ],
                        "strategy_revision_proposal": None,
                        "coverage_gaps": [],
                        "warnings": [],
                    }
                ),
                response_metadata={},
            )

    monkeypatch.setattr(service, "_resolve_model", lambda *_a, **_k: ("test", "fake-model"))
    monkeypatch.setattr(
        service,
        "resolve_for_user",
        lambda *_a, **_k: SimpleNamespace(api_key="not-persisted", endpoint=None),
    )
    monkeypatch.setattr(service, "_make_chat_model", lambda *_a, **_k: FakeLLM())
    monkeypatch.setattr(service.pricing, "cost_usd", lambda *_a, **_k: 0.0)

    _execution, output, _hash, suggestions = run_engagement_strategist(
        db,
        object(),
        engagement=engagement,
        acting_user_id=user.id,
        mode="reassess",
    )

    # Only the net-new proposal survives; the duplicate of the completed work
    # item is deduped against the existing WorkItem.
    assert sorted(s.title for s in suggestions) == ["Probe acme.example exposed services"]
    assert len(output.work_item_proposals) == 2  # LLM still returned both; dedup is server-side

    # The reassess-specific prompt drove the run (not the generic _SYSTEM_PROMPT).
    system = next(content for role, content in captured["messages"] if role == "system")
    assert "reassessing" in system.lower()
    assert "NET-NEW" in system
