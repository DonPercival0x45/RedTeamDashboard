"""Engagement Memory service tests (architecture v3, step 1).

Proves the contract the service exists to guarantee: compaction only retires
*stale* elements, never touches hard floors, and never deletes — every move is
a reversible tier demotion. Also covers folding, the token-budget signal, and
the optimistic-lock guard on concurrent edits.

Time is driven via the ``now`` parameter on ``compact`` / ``mark_referenced``
(no wall-clock dependency). The migration up/down round-trip is exercised by the
standalone ``alembic upgrade head`` / ``downgrade`` check, not here.

Isolation: tests flush (not commit); the ``db`` fixture rolls the transaction
back, so nothing persists between tests.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import (
    ActorType,
    Engagement,
    EngagementStatus,
    EngagementWorkState,
    MemoryKind,
    MemoryLink,
    MemoryLinkRelation,
    MemoryStatus,
    MemoryTier,
)
from app.services import memory as mem
from app.services.memory import StaleMemoryElement

AGENT = ActorType.agent
ACTOR = "test-actor"


@pytest.fixture()
def engagement(db: Session) -> Engagement:
    eng = Engagement(
        name="Memory Test",
        slug=f"mem-{uuid.uuid4().hex[:8]}",
        status=EngagementStatus.active,
        work_state=EngagementWorkState.active,
    )
    db.add(eng)
    db.flush()
    return eng


def _future(days: int) -> datetime:
    return datetime.now(tz=UTC) + timedelta(days=days)


# ---------------------------------------------------------------------------
# Compaction: demote stale, protect hard floors, never delete
# ---------------------------------------------------------------------------


def test_compact_demotes_stale_thread_and_low_conf_fact(
    db: Session, engagement: Engagement
) -> None:
    thread = mem.create_element(
        db, engagement_id=engagement.id, kind=MemoryKind.thread,
        summary="an old thread", author_type=AGENT, author_id=ACTOR,
    )
    low_fact = mem.create_element(
        db, engagement_id=engagement.id, kind=MemoryKind.fact,
        summary="a shaky low-confidence fact", author_type=AGENT, author_id=ACTOR,
        confidence=0.3,
    )
    high_fact = mem.create_element(
        db, engagement_id=engagement.id, kind=MemoryKind.fact,
        summary="a solid high-confidence fact", author_type=AGENT, author_id=ACTOR,
        confidence=0.9,
    )
    decision = mem.create_element(
        db, engagement_id=engagement.id, kind=MemoryKind.decision,
        summary="a decision that must never be auto-demoted",
        author_type=AGENT, author_id=ACTOR,
    )

    result = mem.compact(db, engagement_id=engagement.id, now=_future(100))

    db.refresh(thread)
    db.refresh(low_fact)
    db.refresh(high_fact)
    db.refresh(decision)
    assert thread.tier == MemoryTier.cold          # stale thread demoted
    assert low_fact.tier == MemoryTier.cold         # low-confidence + stale demoted
    assert high_fact.tier == MemoryTier.hot         # high confidence protected
    assert decision.tier == MemoryTier.hot          # decision is a hard floor
    assert result["moved_count"] == 2
    # Nothing was deleted — the demoted rows still exist.
    assert db.get(type(thread), thread.id) is not None
    assert db.get(type(low_fact), low_fact.id) is not None


def test_recently_referenced_fact_is_a_hard_floor(
    db: Session, engagement: Engagement
) -> None:
    fact = mem.create_element(
        db, engagement_id=engagement.id, kind=MemoryKind.fact,
        summary="low-confidence but actively used", author_type=AGENT, author_id=ACTOR,
        confidence=0.2,
    )
    # Reference it inside the analysis window (same instant we compact at).
    mem.mark_referenced(db, [fact.id], now=_future(100))
    # mark_referenced is a Core UPDATE that bypasses the identity map — the
    # step-4 caller (and this test) must expire so the compaction re-read sees
    # the new last_referenced_at.
    db.expire_all()

    mem.compact(db, engagement_id=engagement.id, now=_future(100))

    db.refresh(fact)
    assert fact.tier == MemoryTier.hot  # protected despite low confidence


def test_blocked_open_question_is_a_hard_floor(
    db: Session, engagement: Engagement
) -> None:
    q = mem.create_element(
        db, engagement_id=engagement.id, kind=MemoryKind.open_question,
        summary="what is behind this?", author_type=AGENT, author_id=ACTOR,
        body={"blocked_on": [str(uuid.uuid4())]},
    )
    mem.compact(db, engagement_id=engagement.id, now=_future(100))
    db.refresh(q)
    assert q.tier == MemoryTier.hot


# ---------------------------------------------------------------------------
# Reversibility
# ---------------------------------------------------------------------------


def test_restore_recovers_archived_element(
    db: Session, engagement: Engagement
) -> None:
    el = mem.create_element(
        db, engagement_id=engagement.id, kind=MemoryKind.fact,
        summary="was archived", author_type=AGENT, author_id=ACTOR,
    )
    mem.set_tier(
        db, element=el, tier=MemoryTier.archived, actor_type=AGENT, actor_id=ACTOR
    )
    assert el.tier == MemoryTier.archived

    mem.restore(db, element=el, actor_type=AGENT, actor_id=ACTOR)
    assert el.tier == MemoryTier.hot


# ---------------------------------------------------------------------------
# Folding: lineage + status + tier on every folded element, decision stays hot
# ---------------------------------------------------------------------------


def test_fold_into_decision_sets_lineage_on_every_hypothesis(
    db: Session, engagement: Engagement
) -> None:
    h1 = mem.create_element(
        db, engagement_id=engagement.id, kind=MemoryKind.hypothesis,
        summary="hypothesis one", author_type=AGENT, author_id=ACTOR,
    )
    h2 = mem.create_element(
        db, engagement_id=engagement.id, kind=MemoryKind.hypothesis,
        summary="hypothesis two", author_type=AGENT, author_id=ACTOR,
    )

    decision = mem.fold_into_decision(
        db, engagement_id=engagement.id, hypotheses=[h1, h2],
        decision_summary="settled it", rationale="both confirmed",
        actor_type=AGENT, actor_id=ACTOR,
    )

    assert decision.kind == MemoryKind.decision
    assert decision.tier == MemoryTier.hot          # decision stays hot
    for h in (h1, h2):
        db.refresh(h)
        assert h.status == MemoryStatus.superseded
        assert h.tier == MemoryTier.archived
        assert h.superseded_by == decision.id       # reversible lineage
        links = db.execute(
            select(MemoryLink).where(
                MemoryLink.from_element_id == h.id,
                MemoryLink.relation == MemoryLinkRelation.folds_into,
            )
        ).scalars().all()
        assert len(links) == 1
        assert links[0].target_id == decision.id
        assert links[0].engagement_id == engagement.id  # scoped, not straddling


# ---------------------------------------------------------------------------
# Token budget signal
# ---------------------------------------------------------------------------


def test_hot_token_total_and_budget_reflect_tier_moves(
    db: Session, engagement: Engagement, monkeypatch: pytest.MonkeyPatch
) -> None:
    a = mem.create_element(
        db, engagement_id=engagement.id, kind=MemoryKind.fact,
        summary="x" * 400, author_type=AGENT, author_id=ACTOR,
    )
    mem.create_element(
        db, engagement_id=engagement.id, kind=MemoryKind.fact,
        summary="y" * 400, author_type=AGENT, author_id=ACTOR,
    )
    total_before = mem.hot_token_total(db, engagement.id)
    assert total_before > 0

    # Moving one element out of HOT drops the hot total.
    mem.set_tier(db, element=a, tier=MemoryTier.cold, actor_type=AGENT, actor_id=ACTOR)
    total_after = mem.hot_token_total(db, engagement.id)
    assert total_after < total_before

    monkeypatch.setattr(settings, "hot_memory_token_budget", 0)
    assert mem.is_over_budget(db, engagement.id) is True
    monkeypatch.setattr(settings, "hot_memory_token_budget", 10**9)
    assert mem.is_over_budget(db, engagement.id) is False


# ---------------------------------------------------------------------------
# Optimistic locking on concurrent edits
# ---------------------------------------------------------------------------


def test_edit_element_optimistic_version_conflict(
    db: Session, engagement: Engagement
) -> None:
    el = mem.create_element(
        db, engagement_id=engagement.id, kind=MemoryKind.fact,
        summary="v1", author_type=AGENT, author_id=ACTOR,
    )
    assert el.version == 1

    mem.edit_element(
        db, element=el, actor_type=AGENT, actor_id=ACTOR,
        summary="v2", expected_version=1,
    )
    assert el.version == 2

    # A second writer still holding version 1 is rejected.
    with pytest.raises(StaleMemoryElement):
        mem.edit_element(
            db, element=el, actor_type=AGENT, actor_id=ACTOR,
            summary="conflict", expected_version=1,
        )

    # Editing with the current version succeeds again.
    mem.edit_element(
        db, element=el, actor_type=AGENT, actor_id=ACTOR,
        summary="v3", expected_version=2,
    )
    assert el.version == 3

    # No expected_version → no check (internal/agent writes).
    mem.edit_element(db, element=el, actor_type=AGENT, actor_id=ACTOR, summary="v4")
    assert el.summary == "v4"
