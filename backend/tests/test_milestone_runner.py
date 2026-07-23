"""Milestone runner tests (v3 B3 + B5).

Proves milestone → prompt-mode batching plus the engagement-locked B5 cycle:
primary intelligence, deterministic compaction, and lazy coverage review when
the hot set remains over budget.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import (
    AgentExecutionStatus,
    AgentTrigger,
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
from app.services import milestone_runner as runner
from app.services.milestone_runner import (
    MILESTONE_MODES,
    handle_milestone,
    milestone_mode,
    run_milestone_cycle,
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
    factory_calls = 0

    def llm_factory() -> tuple[FakeLLM, str, str]:
        nonlocal factory_calls
        factory_calls += 1
        return llm, "test-provider", "test-model"

    result = handle_milestone(
        db, engagement_id=engagement.id, milestone_type="run.completed",
        acting_user_id=user.id, llm_factory=llm_factory,
    )
    db.flush()

    assert result is not None
    assert factory_calls == 1
    assert result[1].model_provider == "test-provider"
    assert result[1].model_name == "test-model"
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

    def unexpected_llm_factory() -> tuple[FakeLLM, str, str]:
        raise AssertionError("nothing-changed milestone must not resolve an LLM")

    result = handle_milestone(
        db, engagement_id=engagement.id, milestone_type="run.completed",
        acting_user_id=user.id, llm_factory=unexpected_llm_factory, since=cutoff,
    )

    assert result is None  # gather-then-analyze: nothing significant → no invocation


def test_analysis_milestone_propagates_llm_failure_for_retry(
    db: Session, engagement: Engagement, user: User
) -> None:
    _finding(db, engagement, severity=Severity.high)

    class FailingLLM:
        def with_structured_output(self, _schema):
            return self

        def invoke(self, _messages):
            raise RuntimeError("provider unavailable")

    with pytest.raises(RuntimeError, match="provider unavailable"):
        handle_milestone(
            db,
            engagement_id=engagement.id,
            milestone_type="run.completed",
            acting_user_id=user.id,
            llm=FailingLLM(),
        )


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


class _CycleSession:
    def __init__(self, calls: list[Any]) -> None:
        self.calls = calls

    def execute(self, statement: Any, params: dict[str, Any]) -> None:
        self.calls.append(("lock", str(statement), params))


def test_cycle_orders_lock_primary_compaction_and_keeps_review_lazy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Any] = []
    session = _CycleSession(calls)
    engagement_id = uuid.uuid4()
    actor_id = uuid.uuid4()
    primary_result = ("primary", object())

    def primary(*_args: Any, **_kwargs: Any) -> tuple[str, object]:
        calls.append("primary")
        return primary_result

    def compact(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        calls.append("compact")
        return {"still_over_budget": False, "moved_count": 0}

    monkeypatch.setattr(runner, "handle_milestone", primary)
    monkeypatch.setattr(runner, "compact_memory", compact)

    result = run_milestone_cycle(
        session,  # type: ignore[arg-type]
        engagement_id=engagement_id,
        milestone_type="run.completed",
        acting_user_id=actor_id,
        llm_factory=lambda: (object(), "primary-provider", "primary-model"),
        coverage_review_llm_factory=lambda: (_ for _ in ()).throw(
            AssertionError("under-budget cycle must not resolve coverage-review LLM")
        ),
    )

    assert calls[0][0] == "lock"
    assert calls[0][2] == {"key": f"engagement-memory:{engagement_id}"}
    assert calls[1:] == ["primary", "compact"]
    assert result.primary is primary_result
    assert result.coverage_review is None


def test_cycle_over_budget_runs_coverage_review_with_exact_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Any] = []
    session = _CycleSession(calls)
    execution = SimpleNamespace(
        id=uuid.uuid4(), status=AgentExecutionStatus.completed
    )
    llm = object()

    monkeypatch.setattr(runner, "handle_milestone", lambda *_a, **_k: None)
    monkeypatch.setattr(
        runner,
        "compact_memory",
        lambda *_a, **_k: {"still_over_budget": True, "moved_count": 0},
    )

    def analyze(*_args: Any, **kwargs: Any) -> tuple[str, Any]:
        calls.append(("coverage", kwargs))
        return "reviewed", execution

    monkeypatch.setattr(runner, "run_intelligence_analysis", analyze)
    monkeypatch.setattr(runner, "hot_token_total", lambda *_a, **_k: 17)

    result = run_milestone_cycle(
        session,  # type: ignore[arg-type]
        engagement_id=uuid.uuid4(),
        milestone_type="baseline.completed",
        acting_user_id=uuid.uuid4(),
        llm_factory=lambda: (object(), "primary-provider", "primary-model"),
        coverage_review_llm_factory=lambda: (llm, "review-provider", "review-model"),
    )

    coverage_kwargs = calls[-1][1]
    assert coverage_kwargs["mode"] is AgentPromptMode.coverage_review
    assert coverage_kwargs["llm"] is llm
    assert coverage_kwargs["model_provider"] == "review-provider"
    assert coverage_kwargs["model_name"] == "review-model"
    assert coverage_kwargs["trigger"] is AgentTrigger.tick
    assert result.coverage_review == ("reviewed", execution)
    assert result.compaction["token_after_review"] == 17
    assert result.compaction["coverage_review_status"] == "completed"


def test_cycle_records_coverage_setup_failure_without_replaying_primary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _CycleSession([])
    failed = SimpleNamespace(id=uuid.uuid4(), status=AgentExecutionStatus.failed)
    primary_calls = 0

    def primary(*_args: Any, **_kwargs: Any) -> tuple[str, object]:
        nonlocal primary_calls
        primary_calls += 1
        return "primary", object()

    monkeypatch.setattr(runner, "handle_milestone", primary)
    monkeypatch.setattr(
        runner,
        "compact_memory",
        lambda *_a, **_k: {"still_over_budget": True, "moved_count": 0},
    )
    monkeypatch.setattr(
        runner,
        "record_intelligence_failure",
        lambda *_a, **_k: failed,
    )
    monkeypatch.setattr(runner, "hot_token_total", lambda *_a, **_k: 99)

    result = run_milestone_cycle(
        session,  # type: ignore[arg-type]
        engagement_id=uuid.uuid4(),
        milestone_type="baseline.completed",
        acting_user_id=uuid.uuid4(),
        llm_factory=lambda: (object(), "primary-provider", "primary-model"),
        coverage_review_llm_factory=lambda: (_ for _ in ()).throw(
            RuntimeError("key expired")
        ),
    )

    assert primary_calls == 1
    assert result.coverage_review == (None, failed)
    assert result.compaction["coverage_review_status"] == "failed"


def test_cycle_propagates_deterministic_database_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _CycleSession([])
    monkeypatch.setattr(runner, "handle_milestone", lambda *_a, **_k: None)
    monkeypatch.setattr(
        runner,
        "compact_memory",
        lambda *_a, **_k: (_ for _ in ()).throw(SQLAlchemyError("db failed")),
    )

    with pytest.raises(SQLAlchemyError, match="db failed"):
        run_milestone_cycle(
            session,  # type: ignore[arg-type]
            engagement_id=uuid.uuid4(),
            milestone_type="run.completed",
            acting_user_id=uuid.uuid4(),
            llm_factory=lambda: (object(), "primary-provider", "primary-model"),
            coverage_review_llm_factory=lambda: (
                object(),
                "review-provider",
                "review-model",
            ),
        )


def test_cycle_executes_real_postgres_lock_and_compaction(
    db: Session,
    engagement: Engagement,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "hot_memory_token_budget", 1_000_000)

    result = run_milestone_cycle(
        db,
        engagement_id=engagement.id,
        milestone_type="run.completed",
        acting_user_id=user.id,
        llm_factory=lambda: (_ for _ in ()).throw(
            AssertionError("empty analysis batch must not resolve primary LLM")
        ),
        coverage_review_llm_factory=lambda: (_ for _ in ()).throw(
            AssertionError("under-budget cycle must not resolve coverage-review LLM")
        ),
    )

    assert result.primary is None
    assert result.compaction["still_over_budget"] is False
    assert result.coverage_review is None
