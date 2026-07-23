"""Engagement Memory service — CRUD + the compaction contract (v3, step 1).

The persistence-layer half of architecture step 1. Every mutation is
attributed (row columns + an ``AuditLog`` row) and reversible: compaction
*demotes tier and supersedes*, it never deletes, so ``restore`` can always
pull an element back to ``hot``.

Split of responsibility:
  * This service owns the *mechanics* — create/edit/link, the hot working-set
    read, the budget check, tier transitions, folding hypotheses into a
    decision, restore, and the deterministic staleness pass.
  * The *agent* (step 4) owns the judgement calls — which hypothesis is
    resolved, which threads to fold — by calling these primitives under the
    ``coverage-review`` prompt-mode.

Concurrency: the milestone cycle takes an engagement-scoped transaction lock;
``compact`` additionally locks the current HOT rows so a concurrent optimistic
edit blocks/rechecks rather than racing the batch. Future manual compaction or
Memory-mutation endpoints must preserve this lock/row-lock contract.
"""
from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import AuditLog
from app.models.audit_log import ActorType
from app.models.memory import (
    MemoryElement,
    MemoryKind,
    MemoryLink,
    MemoryLinkRelation,
    MemoryLinkTargetType,
    MemoryStatus,
    MemoryTier,
)

logger = structlog.get_logger(__name__)


class StaleMemoryElement(Exception):
    """Raised by ``edit_element`` when ``expected_version`` no longer matches
    the row — another writer edited it first. The caller re-reads and retries
    (or surfaces a conflict to the analyst)."""

    def __init__(self, element_id: str, expected_version: int) -> None:
        self.element_id = element_id
        self.expected_version = expected_version
        super().__init__(
            f"memory element {element_id} changed under you "
            f"(expected version {expected_version})"
        )


# ---------------------------------------------------------------------------
# Token estimate (O2: cheap char-based signal, swap for a tokenizer if needed)
# ---------------------------------------------------------------------------


def estimate_tokens(*parts: Any) -> int:
    """~len/4 over the concatenated text. A budget *signal*, not billing."""
    total = 0
    for p in parts:
        if p is None:
            continue
        total += len(str(p))
    return total // 4


def _audit(
    session: Session,
    *,
    engagement_id: uuid.UUID,
    actor_type: ActorType,
    actor_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    session.add(
        AuditLog(
            engagement_id=engagement_id,
            actor_type=actor_type,
            actor_id=actor_id,
            event_type=event_type,
            payload=payload,
        )
    )


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def create_element(
    session: Session,
    *,
    engagement_id: uuid.UUID,
    kind: MemoryKind,
    summary: str,
    author_type: ActorType,
    author_id: str,
    body: dict[str, Any] | None = None,
    confidence: float | None = None,
    status: MemoryStatus = MemoryStatus.open,
    tier: MemoryTier = MemoryTier.hot,
) -> MemoryElement:
    """Create one element (hot by default). Caller commits."""
    body = body or {}
    el = MemoryElement(
        engagement_id=engagement_id,
        kind=kind,
        tier=tier,
        status=status,
        summary=summary,
        body=body,
        confidence=confidence,
        token_estimate=estimate_tokens(summary, body.values()),
        author_type=author_type,
        author_id=author_id,
    )
    session.add(el)
    session.flush()
    _audit(
        session,
        engagement_id=engagement_id,
        actor_type=author_type,
        actor_id=author_id,
        event_type="memory.element_created",
        payload={"element_id": str(el.id), "kind": kind.value, "tier": tier.value},
    )
    return el


def edit_element(
    session: Session,
    *,
    element: MemoryElement,
    actor_type: ActorType,
    actor_id: str,
    summary: str | None = None,
    body: dict[str, Any] | None = None,
    confidence: float | None = None,
    status: MemoryStatus | None = None,
    expected_version: int | None = None,
) -> MemoryElement:
    """Edit an element in place (analyst or agent). Re-estimates tokens.

    Pass ``expected_version`` (the version the caller read) to guard against a
    concurrent same-element edit: the version bump is applied with a
    ``WHERE version = expected`` predicate, and a miss raises
    :class:`StaleMemoryElement`. Omit it for internal/agent writes that don't
    need the check (compaction already serializes under the engagement lock).
    """
    if expected_version is not None:
        result = session.execute(
            update(MemoryElement)
            .where(
                MemoryElement.id == element.id,
                MemoryElement.version == expected_version,
            )
            .values(version=MemoryElement.version + 1)
        )
        if result.rowcount == 0:
            raise StaleMemoryElement(str(element.id), expected_version)
        # Resync the ORM object to the bumped version + any concurrent change.
        session.refresh(element)

    changed: dict[str, Any] = {}
    if summary is not None and summary != element.summary:
        element.summary = summary
        changed["summary"] = True
    if body is not None:
        element.body = body
        changed["body"] = True
    if confidence is not None:
        element.confidence = confidence
        changed["confidence"] = confidence
    if status is not None:
        element.status = status
        changed["status"] = status.value
    element.token_estimate = estimate_tokens(element.summary, (element.body or {}).values())
    session.flush()
    _audit(
        session,
        engagement_id=element.engagement_id,
        actor_type=actor_type,
        actor_id=actor_id,
        event_type="memory.element_edited",
        payload={"element_id": str(element.id), "changed": changed},
    )
    return element


def add_link(
    session: Session,
    *,
    from_element: MemoryElement,
    relation: MemoryLinkRelation,
    target_type: MemoryLinkTargetType,
    target_id: uuid.UUID,
) -> MemoryLink:
    """Add a typed edge from ``from_element``. ``engagement_id`` is taken from
    the source element (never passed independently) so a link can't straddle
    engagements. The polymorphic target FK is validated by the caller."""
    link = MemoryLink(
        engagement_id=from_element.engagement_id,
        from_element_id=from_element.id,
        relation=relation,
        target_type=target_type,
        target_id=target_id,
    )
    session.add(link)
    session.flush()
    return link


# ---------------------------------------------------------------------------
# Hot working set + budget
# ---------------------------------------------------------------------------


def get_hot_set(
    session: Session,
    engagement_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> list[MemoryElement]:
    """The elements serialized into an agent invocation: everything HOT.

    Decisions first (the accumulating foundation the agent should always see),
    then everything else newest-first. ``for_update`` is reserved for batch
    mutation paths such as compaction; ordinary prompt reads remain lock-free.
    """
    stmt = (
        select(MemoryElement)
        .where(
            MemoryElement.engagement_id == engagement_id,
            MemoryElement.tier == MemoryTier.hot,
        )
        .order_by(
            (MemoryElement.kind == MemoryKind.decision).desc(),
            MemoryElement.created_at.desc(),
        )
    )
    if for_update:
        stmt = stmt.with_for_update()
    return list(session.execute(stmt).scalars())


def hot_token_total(session: Session, engagement_id: uuid.UUID) -> int:
    """SUM(token_estimate) over the HOT set — the cheap budget signal."""
    total = session.execute(
        select(func.coalesce(func.sum(MemoryElement.token_estimate), 0)).where(
            MemoryElement.engagement_id == engagement_id,
            MemoryElement.tier == MemoryTier.hot,
        )
    ).scalar_one()
    return int(total or 0)


def is_over_budget(session: Session, engagement_id: uuid.UUID) -> bool:
    return hot_token_total(session, engagement_id) > settings.hot_memory_token_budget


def mark_referenced(
    session: Session, element_ids: Sequence[uuid.UUID], *, now: datetime | None = None
) -> None:
    """Stamp last_referenced_at so recently-used elements survive the staleness
    pass. Call when the agent cites elements in an invocation.

    Note: this is a bulk Core UPDATE that bypasses the ORM identity map — it
    writes no audit row (it's a derived signal, not a content edit) and does
    NOT refresh already-loaded objects. A caller that re-reads those elements
    in the same session must ``session.expire_all()`` first, or pass ids rather
    than loaded objects."""
    if not element_ids:
        return
    ts = now or datetime.now(tz=UTC)
    session.execute(
        MemoryElement.__table__.update()
        .where(MemoryElement.id.in_(list(element_ids)))
        .values(last_referenced_at=ts)
    )


# ---------------------------------------------------------------------------
# Tier transitions (reversible — never delete)
# ---------------------------------------------------------------------------


def set_tier(
    session: Session,
    *,
    element: MemoryElement,
    tier: MemoryTier,
    actor_type: ActorType,
    actor_id: str,
    reason: str | None = None,
) -> MemoryElement:
    prev = element.tier
    if prev == tier:
        return element
    element.tier = tier
    session.flush()
    _audit(
        session,
        engagement_id=element.engagement_id,
        actor_type=actor_type,
        actor_id=actor_id,
        event_type="memory.tier_changed",
        payload={
            "element_id": str(element.id),
            "from": prev.value,
            "to": tier.value,
            "reason": reason,
        },
    )
    return element


def restore(
    session: Session,
    *,
    element: MemoryElement,
    actor_type: ActorType,
    actor_id: str,
) -> MemoryElement:
    """Pull an archived/cold element back to HOT — the safety net for a bad
    compaction pass. Nothing is ever lost, so this always works."""
    return set_tier(
        session,
        element=element,
        tier=MemoryTier.hot,
        actor_type=actor_type,
        actor_id=actor_id,
        reason="restore",
    )


def fold_into_decision(
    session: Session,
    *,
    engagement_id: uuid.UUID,
    hypotheses: Sequence[MemoryElement],
    decision_summary: str,
    rationale: str,
    actor_type: ActorType,
    actor_id: str,
) -> MemoryElement:
    """Compaction primitive: settle a set of hypotheses into one decision.

    The decision is created HOT; each folded element is linked
    ``folds_into`` the decision, marked ``superseded`` + ``archived``, and
    given ``superseded_by`` lineage so it stays reversible.

    Defense in depth: callers may pass model-selected rows, so only unique HOT
    hypotheses belonging to this engagement and not already closed may fold.
    """
    if not hypotheses:
        raise ValueError("fold requires at least one hypothesis")
    seen: set[uuid.UUID] = set()
    for hypothesis in hypotheses:
        if hypothesis.id in seen:
            raise ValueError(f"duplicate hypothesis in fold: {hypothesis.id}")
        seen.add(hypothesis.id)
        if hypothesis.engagement_id != engagement_id:
            raise ValueError(f"hypothesis {hypothesis.id} belongs to another engagement")
        if hypothesis.kind is not MemoryKind.hypothesis:
            raise ValueError(f"memory element {hypothesis.id} is not a hypothesis")
        if hypothesis.tier is not MemoryTier.hot:
            raise ValueError(f"hypothesis {hypothesis.id} is not in the hot set")
        if hypothesis.status not in {MemoryStatus.open, MemoryStatus.resolved}:
            raise ValueError(f"hypothesis {hypothesis.id} is already closed")

    decision = create_element(
        session,
        engagement_id=engagement_id,
        kind=MemoryKind.decision,
        summary=decision_summary,
        author_type=actor_type,
        author_id=actor_id,
        body={"rationale": rationale, "folds": [str(h.id) for h in hypotheses]},
    )
    for h in hypotheses:
        add_link(
            session,
            from_element=h,
            relation=MemoryLinkRelation.folds_into,
            target_type=MemoryLinkTargetType.memory_element,
            target_id=decision.id,
        )
        h.status = MemoryStatus.superseded
        h.superseded_by = decision.id
        set_tier(
            session,
            element=h,
            tier=MemoryTier.archived,
            actor_type=actor_type,
            actor_id=actor_id,
            reason="folded_into_decision",
        )
    session.flush()
    return decision


# ---------------------------------------------------------------------------
# Deterministic compaction pass (O5: milestone + manual both call this)
# ---------------------------------------------------------------------------


def _is_hard_floor(element: MemoryElement, *, window_start: datetime) -> bool:
    """Elements compaction must never touch:
      - an open_question still blocked on something,
      - anything referenced inside the current analysis window,
      - a decision (decisions are the compacted form).
    """
    if element.kind == MemoryKind.decision:
        return True
    if (
        element.kind == MemoryKind.open_question
        and element.status == MemoryStatus.open
        and (element.body or {}).get("blocked_on")
    ):
        return True
    return (
        element.last_referenced_at is not None
        and element.last_referenced_at >= window_start
    )


def compact(
    session: Session,
    *,
    engagement_id: uuid.UUID,
    actor_type: ActorType = ActorType.agent,
    actor_id: str = "engagement-memory-compactor",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Run the DETERMINISTIC staleness pass over the hot set — the ONE
    deterministic entry point (O5). Agent-driven folds
    (``fold_into_decision``) are separate, judgement-based primitives.

    Rules (all demote, never delete; hard floors in ``_is_hard_floor``):
      - stale threads (no activity for ``memory_thread_stale_days``): hot->cold
      - low-confidence facts not referenced within ``memory_fact_stale_days``:
        hot->cold

    This pass only retires elements that have gone *stale*. An engagement that
    is over budget but whose hot elements are all fresh is intentionally a
    no-op here — retiring fresh-but-resolved elements is the agent's job via
    ``fold_into_decision``, driven by the step-4 milestone runner that reads
    ``is_over_budget`` as its trigger signal.

    CONCURRENCY CONTRACT: the caller MUST hold the per-engagement lock. This is
    a batch over many elements that has to serialize against concurrent edits;
    the hot rows are also selected ``FOR UPDATE`` so optimistic single-row
    edits block and re-check their version. ``run_milestone_cycle`` is the
    production lock-acquiring caller; future manual entry points must use the
    same contract.

    Returns a summary dict and writes one ``memory.compacted`` audit row.
    Idempotent-ish: a second immediate call is a no-op (nothing newly stale).
    """
    ts = now or datetime.now(tz=UTC)
    thread_cutoff = ts - timedelta(days=settings.memory_thread_stale_days)
    fact_cutoff = ts - timedelta(days=settings.memory_fact_stale_days)
    # "current analysis window" == the fact window; anything referenced inside
    # it is a hard floor.
    window_start = fact_cutoff

    hot = get_hot_set(session, engagement_id, for_update=True)
    before_total = hot_token_total(session, engagement_id)
    moved: list[dict[str, str]] = []

    for el in hot:
        if _is_hard_floor(el, window_start=window_start):
            continue

        demote = False
        if el.kind == MemoryKind.thread:
            # Staleness leans on ``updated_at`` (maintained by TimestampMixin)
            # and ``last_referenced_at`` — both real signals — rather than a
            # ``body.last_activity_at`` field that nothing writes.
            ref = el.last_referenced_at
            if (ref is None or ref < thread_cutoff) and el.updated_at < thread_cutoff:
                demote = True
        elif el.kind == MemoryKind.fact:
            low_conf = (
                el.confidence is not None
                and el.confidence < settings.memory_low_confidence_threshold
            )
            stale_ref = el.last_referenced_at is None or el.last_referenced_at < fact_cutoff
            stale_age = el.updated_at < fact_cutoff
            if low_conf and stale_ref and stale_age:
                demote = True

        if demote:
            set_tier(
                session,
                element=el,
                tier=MemoryTier.cold,
                actor_type=actor_type,
                actor_id=actor_id,
                reason="stale_compaction",
            )
            moved.append({"element_id": str(el.id), "kind": el.kind.value})

    after_total = hot_token_total(session, engagement_id)
    result = {
        "moved": moved,
        "moved_count": len(moved),
        "token_before": before_total,
        "token_after": after_total,
        "token_delta": before_total - after_total,
        "budget": settings.hot_memory_token_budget,
        "still_over_budget": after_total > settings.hot_memory_token_budget,
    }
    _audit(
        session,
        engagement_id=engagement_id,
        actor_type=actor_type,
        actor_id=actor_id,
        event_type="memory.compacted",
        payload=result,
    )
    logger.info("memory.compacted", engagement_id=str(engagement_id), **{
        k: v for k, v in result.items() if k != "moved"
    })
    return result
