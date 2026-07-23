"""Intelligence agent invocation tests (v3 B4-2).

Fake-LLM tests proving each prompt-mode persists what it naturally produces
(option a — per-mode schemas): analysis → Memory facts/hypotheses; ideation →
hypotheses + work items; strategy → decisions + work items; coverage_review →
folds hypotheses into a decision. Plus the LLM-failure path: failed execution,
no partial writes.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.intelligence import run_intelligence_analysis
from app.models import (
    ActorType,
    AgentExecutionStatus,
    AgentPromptMode,
    Engagement,
    EngagementStatus,
    EngagementWorkState,
    MemoryElement,
    MemoryKind,
    MemoryStatus,
    MemoryTier,
    User,
    UserRole,
    WorkItem,
    WorkItemDisposition,
)
from app.schemas.intelligence import (
    AnalysisOutput,
    CoverageReviewOutput,
    IdeationOutput,
    ProposedDecision,
    ProposedFact,
    ProposedFold,
    ProposedHypothesis,
    ProposedWorkItem,
    StrategyOutput,
)
from app.services import memory as mem


class _FakeStructured:
    def __init__(self, result, raise_on_invoke=None):
        self._result = result
        self._raise = raise_on_invoke

    def invoke(self, messages):
        if self._raise is not None:
            raise self._raise
        return self._result


class FakeLLM:
    """Minimal llm double: ``with_structured_output(schema).invoke(messages)``."""

    def __init__(self, result, raise_on_invoke=None):
        self._result = result
        self._raise = raise_on_invoke

    def with_structured_output(self, schema):
        return _FakeStructured(self._result, self._raise)


@pytest.fixture()
def engagement(db: Session) -> Engagement:
    eng = Engagement(
        name="Invoke Test",
        slug=f"inv-{uuid.uuid4().hex[:8]}",
        status=EngagementStatus.active,
        work_state=EngagementWorkState.active,
    )
    db.add(eng)
    db.flush()
    return eng


@pytest.fixture()
def user(db: Session) -> User:
    u = User(email=f"inv-{uuid.uuid4().hex[:6]}@example.com", role=UserRole.user)
    db.add(u)
    db.flush()
    return u


def _memory(db: Session, engagement: Engagement, kind: MemoryKind, summary: str) -> MemoryElement:
    return mem.create_element(
        db, engagement_id=engagement.id, kind=kind, summary=summary,
        author_type=ActorType.agent, author_id="test",
    )


# ---------------------------------------------------------------------------
# Per-mode persistence
# ---------------------------------------------------------------------------


def test_analysis_creates_memory_facts_and_hypotheses(
    db: Session, engagement: Engagement, user: User
) -> None:
    result = AnalysisOutput(
        proposed_facts=[ProposedFact(summary="exposed admin panel", confidence=0.8)],
        proposed_hypotheses=[ProposedHypothesis(summary="default creds likely", confidence=0.4)],
    )
    out, execution = run_intelligence_analysis(
        db, engagement_id=engagement.id, mode=AgentPromptMode.analysis,
        acting_user_id=user.id, llm=FakeLLM(result),
    )
    db.flush()

    assert execution.status is AgentExecutionStatus.completed
    facts = db.execute(
        select(MemoryElement).where(
            MemoryElement.engagement_id == engagement.id, MemoryElement.kind == MemoryKind.fact
        )
    ).scalars().all()
    hyps = db.execute(
        select(MemoryElement).where(
            MemoryElement.engagement_id == engagement.id,
            MemoryElement.kind == MemoryKind.hypothesis,
        )
    ).scalars().all()
    assert len(facts) == 1
    assert facts[0].summary == "exposed admin panel"
    assert facts[0].confidence == 0.8
    assert facts[0].author_id == "intelligence-agent"
    assert len(hyps) == 1


def test_ideation_creates_hypotheses_and_work_items(
    db: Session, engagement: Engagement, user: User
) -> None:
    result = IdeationOutput(
        proposed_hypotheses=[ProposedHypothesis(summary="lateral via shared creds")],
        proposed_work_items=[
            ProposedWorkItem(title="check SMB shares", disposition="manual_local"),
            ProposedWorkItem(title="build kerberoast tool", disposition="build"),
        ],
    )
    run_intelligence_analysis(
        db, engagement_id=engagement.id, mode=AgentPromptMode.ideation,
        acting_user_id=user.id, llm=FakeLLM(result),
    )
    db.flush()

    work_items = db.execute(
        select(WorkItem).where(WorkItem.engagement_id == engagement.id)
    ).scalars().all()
    assert len(work_items) == 2
    dispositions = {wi.disposition for wi in work_items}
    assert WorkItemDisposition.manual_local in dispositions
    assert WorkItemDisposition.build in dispositions


def test_strategy_creates_decisions_and_work_items(
    db: Session, engagement: Engagement, user: User
) -> None:
    result = StrategyOutput(
        situation_summary="narrowing to the auth surface",
        proposed_decisions=[
            ProposedDecision(summary="focus on identity", rationale="highest value"),
        ],
        proposed_work_items=[
            ProposedWorkItem(title="enumerate O365", disposition="tool_backed"),
        ],
    )
    run_intelligence_analysis(
        db, engagement_id=engagement.id, mode=AgentPromptMode.strategy,
        acting_user_id=user.id, llm=FakeLLM(result),
    )
    db.flush()

    decisions = db.execute(
        select(MemoryElement).where(
            MemoryElement.engagement_id == engagement.id, MemoryElement.kind == MemoryKind.decision
        )
    ).scalars().all()
    assert len(decisions) == 1
    assert decisions[0].summary == "focus on identity"
    work_items = db.execute(
        select(WorkItem).where(WorkItem.engagement_id == engagement.id)
    ).scalars().all()
    assert len(work_items) == 1
    assert work_items[0].disposition == WorkItemDisposition.tool_backed


def test_coverage_review_folds_hypotheses_into_decision(
    db: Session, engagement: Engagement, user: User
) -> None:
    h1 = _memory(db, engagement, MemoryKind.hypothesis, "h1")
    h2 = _memory(db, engagement, MemoryKind.hypothesis, "h2")
    _memory(db, engagement, MemoryKind.hypothesis, "unrelated")  # not folded

    result = CoverageReviewOutput(
        folds=[ProposedFold(
            hypothesis_ids=[h1.id, h2.id],
            decision_summary="auth surface confirmed",
            rationale="both validated",
        )],
        re_collection_node_ids=["recon.passive.cert"],
    )
    run_intelligence_analysis(
        db, engagement_id=engagement.id, mode=AgentPromptMode.coverage_review,
        acting_user_id=user.id, llm=FakeLLM(result),
    )
    db.flush()

    db.refresh(h1)
    db.refresh(h2)
    assert h1.status is MemoryStatus.superseded
    assert h1.tier is MemoryTier.archived
    assert h2.status is MemoryStatus.superseded
    decisions = db.execute(
        select(MemoryElement).where(
            MemoryElement.engagement_id == engagement.id, MemoryElement.kind == MemoryKind.decision
        )
    ).scalars().all()
    assert len(decisions) == 1
    assert decisions[0].summary == "auth surface confirmed"


# ---------------------------------------------------------------------------
# Failure path
# ---------------------------------------------------------------------------


def test_llm_failure_marks_execution_failed_no_partial_writes(
    db: Session, engagement: Engagement, user: User
) -> None:
    # A failing LLM mid-analysis must not leave partial Memory/work-item writes.
    result = AnalysisOutput(
        proposed_facts=[ProposedFact(summary="should not persist")],
    )
    out, execution = run_intelligence_analysis(
        db, engagement_id=engagement.id, mode=AgentPromptMode.analysis,
        acting_user_id=user.id,
        llm=FakeLLM(result, raise_on_invoke=RuntimeError("model timed out")),
    )

    assert out is None
    assert execution.status is AgentExecutionStatus.failed
    assert "model timed out" in (execution.error or "")
    # No partial fact was written.
    facts = db.execute(
        select(MemoryElement).where(
            MemoryElement.engagement_id == engagement.id, MemoryElement.kind == MemoryKind.fact
        )
    ).scalars().all()
    assert facts == []
