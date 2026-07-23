"""Strategy projection tests (v3 B1).

Proves the read path's two contracts: (1) the hot set is grouped for the
strategy screen with decisions first; (2) the token-budget fetch cap (review
item #3 on #198) truncates an oversized hot set rather than loading it all
into a prompt. Coexists with the legacy /strategy revision endpoint.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import (
    ActorType,
    Engagement,
    EngagementStatus,
    EngagementWorkState,
    MemoryKind,
    MemoryTier,
)
from app.schemas.strategy_projection import StrategyProjectionRead
from app.services import memory as mem
from app.services.strategy_projection import build_strategy_projection

AGENT = ActorType.agent
ACTOR = "test-actor"


@pytest.fixture()
def engagement(db: Session) -> Engagement:
    eng = Engagement(
        name="Projection Test",
        slug=f"proj-{uuid.uuid4().hex[:8]}",
        status=EngagementStatus.active,
        work_state=EngagementWorkState.active,
    )
    db.add(eng)
    db.flush()
    return eng


def _el(db: Session, engagement: Engagement, kind: MemoryKind, summary: str, **kw) -> None:
    mem.create_element(
        db,
        engagement_id=engagement.id,
        kind=kind,
        summary=summary,
        author_type=AGENT,
        author_id=ACTOR,
        **kw,
    )


def test_projection_groups_hot_set_with_decisions_first(
    db: Session, engagement: Engagement
) -> None:
    _el(db, engagement, MemoryKind.fact, "a fact")
    _el(db, engagement, MemoryKind.hypothesis, "a hypothesis")
    _el(db, engagement, MemoryKind.decision, "a decision")
    _el(db, engagement, MemoryKind.open_question, "an open question")
    _el(db, engagement, MemoryKind.thread, "a thread")

    proj = build_strategy_projection(db, engagement_id=engagement.id)

    assert len(proj["decisions"]) == 1
    assert len(proj["facts"]) == 1
    assert len(proj["hypotheses"]) == 1
    assert len(proj["open_questions"]) == 1
    assert len(proj["threads"]) == 1
    assert proj["capped"] is False
    assert proj["hot_count_remaining"] == 0
    assert proj["token_total"] > 0
    assert proj["token_budget"] == settings.hot_memory_token_budget


def test_token_budget_cap_truncates_oversized_hot_set(
    db: Session, engagement: Engagement
) -> None:
    # Force a tiny budget so a few normal elements blow past it.
    tiny_budget = 8  # ~2 tokens per summary => caps after ~2-3 elements

    for i in range(10):
        _el(db, engagement, MemoryKind.fact, f"fact number {i} with some words")

    proj = build_strategy_projection(db, engagement_id=engagement.id, token_budget=tiny_budget)

    assert proj["capped"] is True
    assert proj["token_budget"] == tiny_budget
    assert proj["hot_count_remaining"] > 0
    # At least one element is always included (the >=1 guarantee) even if it
    # alone exceeds the budget.
    included = (
        len(proj["decisions"])
        + len(proj["facts"])
        + len(proj["hypotheses"])
        + len(proj["open_questions"])
        + len(proj["threads"])
    )
    assert included >= 1
    assert included < 10  # ...but not all of them


def test_first_element_included_even_if_alone_exceeds_budget(
    db: Session, engagement: Engagement
) -> None:
    _el(db, engagement, MemoryKind.decision, "x" * 200)  # ~50 tokens, budget 1

    proj = build_strategy_projection(db, engagement_id=engagement.id, token_budget=1)

    # The single oversized element is still included (>=1 guarantee) — the cap
    # can't return an empty projection.
    assert len(proj["decisions"]) == 1
    assert proj["capped"] is False  # nothing left to cap against
    assert proj["hot_count_remaining"] == 0


def test_cold_and_archived_excluded_from_projection(
    db: Session, engagement: Engagement
) -> None:
    hot = mem.create_element(
        db, engagement_id=engagement.id, kind=MemoryKind.fact,
        summary="hot fact", author_type=AGENT, author_id=ACTOR,
    )
    cold = mem.create_element(
        db, engagement_id=engagement.id, kind=MemoryKind.fact,
        summary="cold fact", author_type=AGENT, author_id=ACTOR,
    )
    archived = mem.create_element(
        db, engagement_id=engagement.id, kind=MemoryKind.fact,
        summary="archived fact", author_type=AGENT, author_id=ACTOR,
    )
    mem.set_tier(
        db, element=cold, tier=MemoryTier.cold, actor_type=AGENT, actor_id=ACTOR
    )
    mem.set_tier(
        db, element=archived, tier=MemoryTier.archived, actor_type=AGENT, actor_id=ACTOR
    )

    proj = build_strategy_projection(db, engagement_id=engagement.id)

    summaries = [e.summary for e in proj["facts"]]
    assert summaries == ["hot fact"]
    assert hot.tier == MemoryTier.hot


def test_empty_engagement_returns_empty_projection(db: Session, engagement: Engagement) -> None:
    proj = build_strategy_projection(db, engagement_id=engagement.id)
    assert proj["decisions"] == []
    assert proj["facts"] == []
    assert proj["token_total"] == 0
    assert proj["capped"] is False
    assert proj["hot_count_remaining"] == 0


def test_projection_serializes_to_read_schema(db: Session, engagement: Engagement) -> None:
    _el(db, engagement, MemoryKind.decision, "a decision", confidence=0.9)
    proj = build_strategy_projection(db, engagement_id=engagement.id)
    # The dict shape round-trips through the response model.
    read = StrategyProjectionRead(**proj)
    assert len(read.decisions) == 1
    assert read.decisions[0].summary == "a decision"
    assert read.token_budget == settings.hot_memory_token_budget
