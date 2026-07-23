"""Milestone runner tests (v3 B3).

Proves the gather-then-analyze trigger that replaces per-finding Strategic:
milestone → prompt-mode mapping, analysis modes only fire when significant
findings exist (no tokens burned on nothing-changed), and strategy/ideation
always fire.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import Session

from app.models import (
    Engagement,
    EngagementStatus,
    EngagementWorkState,
    Finding,
    FindingPhase,
    FindingStatus,
    Severity,
    User,
    UserRole,
)
from app.models.agent_mode_model_preference import AgentPromptMode
from app.schemas.intelligence import AnalysisOutput, IdeationOutput, StrategyOutput
from app.services.milestone_runner import (
    MILESTONE_MODES,
    handle_milestone,
    milestone_mode,
)


class _FakeStructured:
    def __init__(self, result):
        self._result = result

    def invoke(self, messages):
        return self._result


class FakeLLM:
    def __init__(self, result):
        self._result = result
        self.invoked = False

    def with_structured_output(self, schema):
        outer = self

        class _S:
            def invoke(self, messages):
                outer.invoked = True
                return outer._result

        return _S()


@pytest.fixture()
def engagement(db: Session) -> Engagement:
    eng = Engagement(
        name="Milestone Test",
        slug=f"ms-{uuid.uuid4().hex[:8]}",
        status=EngagementStatus.active,
        work_state=EngagementWorkState.active,
    )
    db.add(eng)
    db.flush()
    return eng


@pytest.fixture()
def user(db: Session) -> User:
    u = User(email=f"ms-{uuid.uuid4().hex[:6]}@example.com", role=UserRole.user)
    db.add(u)
    db.flush()
    return u


def _finding(db: Session, engagement: Engagement, *, severity=Severity.info) -> Finding:
    f = Finding(
        engagement_id=engagement.id,
        title=f"f-{uuid.uuid4().hex[:6]}",
        target="example.com",
        severity=severity,
        status=FindingStatus.pending_validation,
        phase=FindingPhase.osint,
    )
    db.add(f)
    db.flush()
    return f


# ---------------------------------------------------------------------------
# Mapping
# ---------------------------------------------------------------------------


def test_milestone_mode_mapping() -> None:
    assert milestone_mode("collection.job.completed") is AgentPromptMode.analysis
    assert milestone_mode("run.completed") is AgentPromptMode.analysis
    assert milestone_mode("coverage.gap.opened") is AgentPromptMode.strategy
    assert milestone_mode("baseline.completed") is AgentPromptMode.ideation
    assert milestone_mode("unknown.milestone") is None
    assert len(MILESTONE_MODES) == 4


# ---------------------------------------------------------------------------
# Gather-then-analyze: analysis modes only fire on significant findings
# ---------------------------------------------------------------------------


def test_analysis_milestone_fires_when_significant_findings_exist(
    db: Session, engagement: Engagement, user: User
) -> None:
    _finding(db, engagement, severity=Severity.high)  # significant (high + unvalidated)
    llm = FakeLLM(AnalysisOutput())

    result = handle_milestone(
        db, engagement_id=engagement.id, milestone_type="run.completed",
        acting_user_id=user.id, llm=llm,
    )
    db.flush()

    assert result is not None
    assert llm.invoked is True  # the agent was actually called


def test_analysis_milestone_skips_when_nothing_significant(
    db: Session, engagement: Engagement, user: User
) -> None:
    # Only a low, validated finding — not significant (no since window → it's
    # "new", but low+validated is still significant via is_new!). Use a since
    # window + an old finding so it's not new either.
    from datetime import UTC, datetime, timedelta
    f = _finding(db, engagement, severity=Severity.low)
    f.status = FindingStatus.validated
    f.created_at = datetime.now(tz=UTC) - timedelta(days=2)
    db.flush()
    cutoff = datetime.now(tz=UTC) - timedelta(hours=1)

    llm = FakeLLM(AnalysisOutput())
    result = handle_milestone(
        db, engagement_id=engagement.id, milestone_type="run.completed",
        acting_user_id=user.id, llm=llm, since=cutoff,
    )

    assert result is None  # gather-then-analyze: nothing significant → no invocation
    assert llm.invoked is False  # no tokens burned


# ---------------------------------------------------------------------------
# Strategy / ideation always fire (they propose, regardless of findings)
# ---------------------------------------------------------------------------


def test_coverage_gap_milestone_fires_strategy_mode(
    db: Session, engagement: Engagement, user: User
) -> None:
    llm = FakeLLM(StrategyOutput())
    result = handle_milestone(
        db, engagement_id=engagement.id, milestone_type="coverage.gap.opened",
        acting_user_id=user.id, llm=llm,
    )
    db.flush()

    assert result is not None  # fires even with no findings
    assert llm.invoked is True


def test_baseline_completed_milestone_fires_ideation_mode(
    db: Session, engagement: Engagement, user: User
) -> None:
    llm = FakeLLM(IdeationOutput())
    result = handle_milestone(
        db, engagement_id=engagement.id, milestone_type="baseline.completed",
        acting_user_id=user.id, llm=llm,
    )
    db.flush()

    assert result is not None
    assert llm.invoked is True


def test_unknown_milestone_is_ignored(
    db: Session, engagement: Engagement, user: User
) -> None:
    llm = FakeLLM(AnalysisOutput())
    result = handle_milestone(
        db, engagement_id=engagement.id, milestone_type="something.else",
        acting_user_id=user.id, llm=llm,
    )
    assert result is None
    assert llm.invoked is False
