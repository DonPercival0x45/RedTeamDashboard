"""Intelligence agent context + prompt-mode tests (v3 B4-part1).

The deterministic foundation of B4: the context assembler (Memory hot-set +
rollup + engagement basics) and the prompt-mode message builder. No LLM here
— that's a later sub-slice. Proves the structured input every prompt-mode
invocation feeds the model, and that the persona prompt switches by mode.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.agents.intelligence import (
    PROMPT_MODE_PROMPTS,
    build_intelligence_context,
    build_intelligence_messages,
)
from app.models import (
    ActorType,
    AgentPromptMode,
    Engagement,
    EngagementPhase,
    EngagementStatus,
    EngagementWorkState,
    Finding,
    FindingPhase,
    FindingStatus,
    MemoryKind,
    Severity,
)
from app.models import memory as _  # noqa: F401 — ensures mapper load order
from app.services import memory as mem

AGENT = ActorType.agent
ACTOR = "test-actor"


@pytest.fixture()
def engagement(db: Session) -> Engagement:
    eng = Engagement(
        name="Intel Test",
        slug=f"intel-{uuid.uuid4().hex[:8]}",
        status=EngagementStatus.active,
        work_state=EngagementWorkState.active,
    )
    db.add(eng)
    db.flush()
    return eng


def _mem(db: Session, engagement: Engagement, kind: MemoryKind, summary: str, **kw) -> None:
    mem.create_element(
        db, engagement_id=engagement.id, kind=kind, summary=summary,
        author_type=AGENT, author_id=ACTOR, **kw,
    )


def _finding(
    db: Session, engagement: Engagement, *, severity=Severity.info,
    status=FindingStatus.validated, created_at=None,
) -> Finding:
    f = Finding(
        engagement_id=engagement.id,
        title=f"f-{uuid.uuid4().hex[:6]}",
        target="example.com",
        severity=severity,
        status=status,
        phase=FindingPhase.osint,
    )
    if created_at is not None:
        f.created_at = created_at
    db.add(f)
    db.flush()
    return f


# ---------------------------------------------------------------------------
# Context assembler
# ---------------------------------------------------------------------------


def test_context_groups_memory_and_includes_rollup(db: Session, engagement: Engagement) -> None:
    _mem(db, engagement, MemoryKind.fact, "a fact")
    _mem(db, engagement, MemoryKind.decision, "a decision")
    _mem(db, engagement, MemoryKind.hypothesis, "a hypothesis", confidence=0.6)
    _finding(db, engagement, severity=Severity.high, status=FindingStatus.pending_validation)
    _finding(db, engagement, severity=Severity.low, status=FindingStatus.validated)

    ctx = build_intelligence_context(db, engagement_id=engagement.id)

    # Memory grouped by kind, compact summaries.
    assert len(ctx["memory"]["facts"]) == 1
    assert len(ctx["memory"]["decisions"]) == 1
    assert len(ctx["memory"]["hypotheses"]) == 1
    fact_el = ctx["memory"]["facts"][0]
    assert set(fact_el) == {"id", "kind", "summary", "status", "confidence"}

    # Finding rollup.
    assert ctx["findings"]["total"] == 2
    assert ctx["findings"]["high_severity"] == 1
    assert ctx["findings"]["unvalidated"] == 1

    # Engagement basics.
    assert ctx["engagement"]["phase"] == EngagementPhase.baseline.value
    assert ctx["engagement"]["scope_item_count"] == 0


def test_context_significant_batch_matches_predicate(
    db: Session, engagement: Engagement
) -> None:
    # A since window is required: with since=None every finding is "new" and
    # thus significant. Use a window + an old, low, validated finding that's
    # NOT significant under any branch (not new / not high / not unvalidated).
    cutoff = datetime.now(tz=UTC) - timedelta(hours=1)
    high = _finding(
        db, engagement, severity=Severity.critical, created_at=datetime.now(tz=UTC)
    )
    _finding(
        db, engagement, severity=Severity.low, status=FindingStatus.validated,
        created_at=datetime.now(tz=UTC) - timedelta(days=2),
    )

    ctx = build_intelligence_context(db, engagement_id=engagement.id, since=cutoff)

    batch = ctx["significant_findings"]
    assert [item["id"] for item in batch["items"]] == [str(high.id)]
    assert batch["items"][0]["title"] == high.title
    assert batch["items"][0]["severity"] == "critical"
    assert batch["included"] == 1
    assert batch["token_estimate"] <= batch["token_budget"]


def test_context_since_window_bounds_new_and_significant(
    db: Session, engagement: Engagement
) -> None:
    cutoff = datetime.now(tz=UTC) - timedelta(hours=1)
    old = _finding(
        db, engagement, severity=Severity.low, status=FindingStatus.validated,
        created_at=datetime.now(tz=UTC) - timedelta(days=2),
    )
    recent = _finding(db, engagement, severity=Severity.high, created_at=datetime.now(tz=UTC))

    ctx = build_intelligence_context(db, engagement_id=engagement.id, since=cutoff)

    # total counts both, but "new" respects the window.
    assert ctx["findings"]["total"] == 2
    assert ctx["findings"]["new"] == 1
    # old is low+validated+outside-window -> not significant under any branch;
    # recent is high -> significant (and also new within the window).
    item_ids = [item["id"] for item in ctx["significant_findings"]["items"]]
    assert str(old.id) not in item_ids
    assert item_ids == [str(recent.id)]


def test_context_includes_methodology_and_bounded_milestone_metadata(
    db: Session, engagement: Engagement
) -> None:
    engagement.methodology_snapshot = {"slug": "osint-minimal", "version": 1}
    db.flush()
    milestone = {
        "type": "coverage.gap.opened",
        "payload": {
            "node_id": "dns-enumeration",
            "asset_class": "domain",
            "reason": "coverage stale",
        },
    }

    ctx = build_intelligence_context(
        db,
        engagement_id=engagement.id,
        milestone=milestone,
    )

    assert ctx["engagement"]["methodology_slug"] == "osint-minimal"
    assert ctx["engagement"]["methodology_version"] == 1
    assert ctx["milestone"] == milestone


def test_context_empty_engagement_is_well_formed(db: Session, engagement: Engagement) -> None:
    ctx = build_intelligence_context(db, engagement_id=engagement.id)
    assert ctx["memory"]["facts"] == []
    assert ctx["findings"]["total"] == 0
    assert ctx["significant_findings"]["items"] == []
    assert ctx["significant_findings"]["total"] == 0
    assert ctx["coverage"] == {"baseline": {}, "exploration": {}}


# ---------------------------------------------------------------------------
# Prompt-mode message builder
# ---------------------------------------------------------------------------


def test_each_prompt_mode_has_a_distinct_system_prompt() -> None:
    prompts = {PROMPT_MODE_PROMPTS[m] for m in AgentPromptMode}
    assert len(prompts) == len(list(AgentPromptMode))  # all four distinct
    for mode in AgentPromptMode:
        assert mode in PROMPT_MODE_PROMPTS


def test_build_messages_picks_persona_prompt_per_mode(
    db: Session, engagement: Engagement
) -> None:
    ctx = build_intelligence_context(db, engagement_id=engagement.id)

    for mode in AgentPromptMode:
        messages = build_intelligence_messages(ctx, mode)
        assert len(messages) == 2
        assert messages[0][0] == "system"
        assert messages[0][1] == PROMPT_MODE_PROMPTS[mode]
        assert "untrusted engagement evidence" in messages[0][1]
        assert "Do not call tools" in messages[0][1]
        assert messages[1][0] == "user"
        # user message is the context rendered as JSON.
        import json
        assert json.loads(messages[1][1])["engagement"]["id"] == str(engagement.id)
