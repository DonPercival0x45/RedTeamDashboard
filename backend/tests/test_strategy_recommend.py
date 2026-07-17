"""Engagement Strategist recommend loop + honest non-initial fallback.

recommend must use the recommend-specific prompt (not the generic one), propose
NET-NEW work deduped against existing work items. And when a non-initial mode
(recommend/reassess/review_completion) fails, the fallback must NOT emit the
initial-strategy "this engagement has no strategy" text — it must report the
real failure honestly so the analyst isn't misled.

The strategist services are imported lazily inside the tests (see
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
        name="Recommend",
        slug=f"recommend-{uuid.uuid4().hex[:8]}",
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


def _seed(db: Session, engagement: Engagement) -> tuple[ScopeItem, User]:
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
    # Completed work recommend must NOT re-propose.
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
    user = User(email=f"rec-{uuid.uuid4().hex[:8]}@example.com", role=UserRole.user)
    db.add(user)
    db.commit()
    return scope, user


def test_recommend_uses_dedicated_prompt_and_dedups(
    db: Session,
    engagement: Engagement,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import engagement_strategist as service
    from app.services.engagement_strategist import run_engagement_strategist

    scope, user = _seed(db, engagement)
    captured: dict[str, list] = {}

    class FakeLLM:
        def invoke(self, messages: list[tuple[str, str]]) -> SimpleNamespace:
            captured["messages"] = messages
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "situation_summary": "Recommend one net-new validation step.",
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
                                "title": "Validate acme.example TLS certificate chain",
                                "scope_item_id": str(scope.id),
                                "executor_type": "finding_agent",
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
        mode="recommend",
    )

    # Only the net-new recommendation survives; the duplicate of the completed
    # work item is deduped against the existing WorkItem.
    assert sorted(s.title for s in suggestions) == [
        "Validate acme.example TLS certificate chain"
    ]
    assert len(output.work_item_proposals) == 2  # LLM returned both; dedup is server-side

    # The recommend-specific prompt drove the run (not the generic / reassess /
    # review_completion one).
    system = next(content for role, content in captured["messages"] if role == "system")
    assert "NEXT concrete work" in system
    assert "recommending the" in system


def test_non_initial_mode_fallback_is_honest_not_initial_strategy(
    db: Session,
    engagement: Engagement,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import engagement_strategist as service
    from app.services.engagement_strategist import run_engagement_strategist

    _scope, user = _seed(db, engagement)

    class BoomLLM:
        def invoke(self, _messages: list[tuple[str, str]]) -> SimpleNamespace:
            raise RuntimeError("provider timed out")

    monkeypatch.setattr(service, "_resolve_model", lambda *_a, **_k: ("test", "fake-model"))
    monkeypatch.setattr(
        service,
        "resolve_for_user",
        lambda *_a, **_k: SimpleNamespace(api_key="not-persisted", endpoint=None),
    )
    monkeypatch.setattr(service, "_make_chat_model", lambda *_a, **_k: BoomLLM())
    monkeypatch.setattr(service.pricing, "cost_usd", lambda *_a, **_k: 0.0)

    _execution, output, _hash, _suggestions = run_engagement_strategist(
        db,
        object(),
        engagement=engagement,
        acting_user_id=user.id,
        mode="recommend",
    )

    # Honest failure: names the mode + a real error, and carries no proposals
    # or strategy revision (no misleading "this engagement has no strategy").
    assert output.situation_summary.startswith(
        "Couldn't complete the recommend strategist run"
    )
    assert "No proposals were generated" in output.situation_summary
    assert output.work_item_proposals == []
    assert output.strategy_revision_proposal is None
