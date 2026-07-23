"""Coverage service — Track A step A2 (architecture-v3-tracker).

The write path for ``CoverageRecord`` plus the two computations Track B (B1/B2)
reads out of coverage: **baseline-complete detection** and **stale sweep**.
Track A owns the *lifecycle* of these rows; Track B reads them for the strategy
projection and the milestone runner. This file is the seam between them.

Layout:
  * ``record_coverage_attempt`` — append a new attempt row (never updates
    an existing one). Records accumulate per (node, asset_class, scope_key)
    = attempt history, so baseline-complete reads the **latest** record per
    triple (architecture-answers Q3 / #199 review). ``satisfied_at`` is stamped
    when ``status=satisfied``.
  * ``latest_by_node`` — one DB round-trip that returns the newest record per
    (node_id, asset_class, scope_key) tuple. Baseline-complete + B's coverage
    rollup both read this.
  * ``check_baseline_complete`` — pure function over the latest-by-node view:
    given a set of expected (node_id, asset_class, scope_key) triples (the
    methodology-derived baseline slice for this engagement), returns
    ``(is_complete, unsatisfied)``. Pure so A1 can inject its methodology tree
    once it lands without changing this signature.
  * ``mark_baseline_completed`` — transition side-effect: flip
    ``Engagement.phase`` to ``exploration`` + stamp ``baseline_completed_at``,
    stage the ``baseline.completed`` milestone. Idempotent — a second call on
    an already-completed engagement is a no-op.
  * ``open_coverage_gap`` — stage the ``coverage.gap.opened`` milestone. No
    persistence: gaps are a *signal*; the source-of-truth is the latest-by-node
    view. Callers deduplicate at higher levels (B3 owns that policy).
  * ``sweep_stale`` — append a new ``stale`` row for each ``satisfied`` node
    whose TTL has lapsed. Update-in-place would erase attempt history; append
    keeps the ledger honest and B's rollup sees ``stale`` as the latest status.

Milestone emission uses the shared ``command_outbox`` — the same durable
at-least-once path per-run events already use. The three milestone event types
are declared in ``app.runs.events.EVENT_TYPES`` (extended by this PR); payload
shapes live in ``app.engagement.milestones``. Callers publish the staged entries
after commit via ``publish_feedback_entries``-style flush or wait for the
relay to sweep them — whichever fits the caller's latency budget.

A1 (methodology catalog) has not landed yet, so ``methodology_id`` is optional
on the write path and callers of ``mark_baseline_completed`` must pass it
explicitly — the ``baseline.completed`` payload requires it non-null.
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engagement import milestones as ms
from app.models import (
    ActorType,
    AuditLog,
    CommandOutbox,
    CoverageNodeTier,
    CoverageRecord,
    CoverageRecordStatus,
    Engagement,
    EngagementPhase,
)
from app.runs.streams import outbound_stream
from app.services.command_outbox import enqueue_event

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Scope-subset canonicalization
# ---------------------------------------------------------------------------


ScopeKey = str
"""Canonical string form of a ``scope_subset`` — sorted JSON so two equal
scope selections hash to the same key regardless of insertion order. Used only
as an in-memory grouping key; the DB stores the original JSONB."""


def scope_key(scope_subset: Sequence[Any] | dict[str, Any] | None) -> ScopeKey:
    """Deterministic JSON of a scope subset. Sorted keys / stringified items so
    ``[b, a]`` and ``[a, b]`` collapse to the same key. Empty subset → ``"[]"``.
    """
    if scope_subset is None:
        return "[]"
    if isinstance(scope_subset, dict):
        return json.dumps(scope_subset, sort_keys=True, default=str)
    return json.dumps(sorted(scope_subset, key=str), default=str)


# ---------------------------------------------------------------------------
# Write path
# ---------------------------------------------------------------------------


def record_coverage_attempt(
    session: Session,
    *,
    engagement_id: uuid.UUID,
    node_id: str,
    node_tier: CoverageNodeTier,
    asset_class: str,
    scope_subset: Sequence[Any] | dict[str, Any],
    status: CoverageRecordStatus,
    methodology_id: uuid.UUID | None = None,
    playbook_run_id: uuid.UUID | None = None,
    notes: str | None = None,
    actor_type: ActorType = ActorType.system,
    actor_id: str | None = None,
    now: datetime | None = None,
) -> CoverageRecord:
    """Append a new coverage attempt. Never updates an existing row.

    ``satisfied_at`` is stamped when ``status=satisfied`` (and only then) — the
    stale sweep compares this timestamp against the node's TTL. The stamp uses
    ``now`` when passed (test injection) or wall-clock UTC. Every write emits an
    attributed ``AuditLog`` entry so who-recorded-what is queryable.
    """
    ts = now if now is not None else datetime.now(tz=UTC)
    satisfied_at = ts if status is CoverageRecordStatus.satisfied else None
    row = CoverageRecord(
        engagement_id=engagement_id,
        methodology_id=methodology_id,
        node_id=node_id,
        node_tier=node_tier,
        asset_class=asset_class,
        scope_subset=list(scope_subset) if not isinstance(scope_subset, dict) else scope_subset,
        status=status,
        playbook_run_id=playbook_run_id,
        satisfied_at=satisfied_at,
        notes=notes,
    )
    session.add(row)
    session.add(
        AuditLog(
            engagement_id=engagement_id,
            actor_type=actor_type,
            actor_id=actor_id,
            event_type="coverage.recorded",
            payload={
                "node_id": node_id,
                "node_tier": node_tier.value,
                "asset_class": asset_class,
                "status": status.value,
                "playbook_run_id": str(playbook_run_id) if playbook_run_id else None,
            },
        )
    )
    session.flush()
    return row


# ---------------------------------------------------------------------------
# Read path — latest per (node_id, asset_class, scope_key)
# ---------------------------------------------------------------------------


def latest_by_node(
    session: Session,
    *,
    engagement_id: uuid.UUID,
    node_tier: CoverageNodeTier | None = None,
) -> dict[tuple[str, str, ScopeKey], CoverageRecord]:
    """Return ``{(node_id, asset_class, scope_key): latest_record}`` for the
    engagement — the view baseline-complete + stale sweep both read.

    Reads all rows for the engagement (optionally filtered by tier) ordered by
    ``created_at DESC`` and collapses in-memory. At 100k entities this is still
    O(distinct nodes × attempt-depth); if attempt history ever balloons we
    swap to a window function, but the wire the tracker cares about is one
    row per (node, asset_class, scope) — small.
    """
    stmt = select(CoverageRecord).where(CoverageRecord.engagement_id == engagement_id)
    if node_tier is not None:
        stmt = stmt.where(CoverageRecord.node_tier == node_tier)
    stmt = stmt.order_by(CoverageRecord.created_at.desc(), CoverageRecord.id.desc())
    latest: dict[tuple[str, str, ScopeKey], CoverageRecord] = {}
    for row in session.execute(stmt).scalars():
        key = (row.node_id, row.asset_class, scope_key(row.scope_subset))
        latest.setdefault(key, row)
    return latest


# ---------------------------------------------------------------------------
# Baseline-complete
# ---------------------------------------------------------------------------


ExpectedNode = tuple[str, str, ScopeKey]
"""(node_id, asset_class, scope_key) — one methodology-derived baseline target
for this engagement. A1 will source these from the selected methodology tree
crossed with the engagement's scope selection; A2 accepts them as input so
neither track has to import the other's schema yet."""


def check_baseline_complete(
    session: Session,
    *,
    engagement_id: uuid.UUID,
    expected: Iterable[ExpectedNode],
) -> tuple[bool, list[ExpectedNode]]:
    """Pure predicate over the latest-by-node view.

    Baseline is complete iff every ``expected`` triple has a latest record
    with ``status=satisfied``. Anything else (missing entirely, ``pending`` /
    ``attempted`` / ``partial`` / ``failed`` / ``stale`` / ``stub``) counts as unsatisfied
    and is returned in the second position so callers can surface which nodes
    still need work. ``stale`` is treated as unsatisfied by design — the stored
    lapse is the mechanism that re-qualifies a lapsed satisfied node for
    re-collection (architecture-answers Q3).
    """
    latest = latest_by_node(
        session, engagement_id=engagement_id, node_tier=CoverageNodeTier.baseline
    )
    unsatisfied: list[ExpectedNode] = []
    for triple in expected:
        row = latest.get(triple)
        if row is None or row.status is not CoverageRecordStatus.satisfied:
            unsatisfied.append(triple)
    return (not unsatisfied, unsatisfied)


def mark_baseline_completed(
    session: Session,
    *,
    engagement_id: uuid.UUID,
    methodology_id: uuid.UUID,
    now: datetime | None = None,
    actor_type: ActorType = ActorType.system,
    actor_id: str | None = None,
) -> tuple[Engagement, CommandOutbox | None]:
    """Flip the engagement to exploration + stage the milestone.

    Idempotent: if ``baseline_completed_at`` is already set, this returns
    ``(engagement, None)`` without touching the phase or emitting a duplicate
    milestone. Callers who need at-most-once-emit rely on this shape.
    """
    eng = session.get(Engagement, engagement_id)
    if eng is None:
        raise ValueError(f"engagement {engagement_id} not found")
    if eng.baseline_completed_at is not None:
        return (eng, None)
    ts = now if now is not None else datetime.now(tz=UTC)
    eng.phase = EngagementPhase.exploration
    eng.baseline_completed_at = ts
    payload = ms.baseline_completed(
        engagement_id=str(engagement_id),
        methodology_id=str(methodology_id),
        baseline_completed_at=ts.isoformat(),
        acting_user_id=(actor_id if actor_type == ActorType.user else None),
    )
    outbox_key = f"baseline.completed:{engagement_id}"
    entry = enqueue_event(
        session,
        idempotency_key=outbox_key,
        engagement_id=engagement_id,
        stream_name=outbound_stream(engagement_id),
        payload=payload,
    )
    session.add(
        AuditLog(
            engagement_id=engagement_id,
            actor_type=actor_type,
            actor_id=actor_id,
            event_type="baseline.completed",
            payload={
                "methodology_id": str(methodology_id),
                "baseline_completed_at": ts.isoformat(),
                "outbox_id": str(entry.id),
            },
        )
    )
    return (eng, entry)


# ---------------------------------------------------------------------------
# Coverage-gap signal
# ---------------------------------------------------------------------------


def open_coverage_gap(
    session: Session,
    *,
    engagement_id: uuid.UUID,
    node_id: str,
    node_tier: CoverageNodeTier,
    asset_class: str,
    reason: str,
    acting_user_id: uuid.UUID | None = None,
    dedupe_key: str | None = None,
) -> CommandOutbox:
    """Stage a ``coverage.gap.opened`` milestone.

    No persistence: the source-of-truth for gaps is the latest-by-node view.
    This is the *signal* B3 consumes to nudge the agent. ``dedupe_key`` is
    passed straight to the outbox idempotency key so callers can suppress
    duplicate emissions per (engagement, gap-identifier) window — omit it and
    each call is a distinct emission.
    """
    payload = ms.coverage_gap_opened(
        engagement_id=str(engagement_id),
        node_id=node_id,
        node_tier=node_tier.value,
        asset_class=asset_class,
        reason=reason,
        acting_user_id=str(acting_user_id) if acting_user_id is not None else None,
    )
    key = dedupe_key or f"coverage.gap.opened:{engagement_id}:{uuid.uuid4()}"
    return enqueue_event(
        session,
        idempotency_key=key,
        engagement_id=engagement_id,
        stream_name=outbound_stream(engagement_id),
        payload=payload,
    )


# ---------------------------------------------------------------------------
# Stale sweep
# ---------------------------------------------------------------------------


def sweep_stale(
    session: Session,
    *,
    engagement_id: uuid.UUID,
    node_ttls: dict[str, timedelta],
    now: datetime | None = None,
    actor_type: ActorType = ActorType.system,
    actor_id: str | None = None,
) -> list[CoverageRecord]:
    """Append a fresh ``stale`` row for each ``satisfied`` node past its TTL.

    ``node_ttls`` maps ``node_id`` to the TTL for that technique — A1 sources
    these from the methodology; A2 accepts them so the sweep is pure. Nodes
    not present in the map are skipped (no TTL configured → never lapses).

    Records with ``status != satisfied`` are skipped: an already-stale row's
    tier stays whatever the latest is, and re-collecting from
    ``pending``/``failed`` is the runner's job.

    Update-in-place would erase attempt history; append keeps the ledger
    honest and B's rollup sees ``stale`` as the latest status. Returns the
    freshly-appended rows so callers can wire coverage-gap emissions.
    """
    ts = now if now is not None else datetime.now(tz=UTC)
    latest = latest_by_node(session, engagement_id=engagement_id)
    lapsed: list[CoverageRecord] = []
    for (_node_id, _asset_class, _sk), row in latest.items():
        if row.status is not CoverageRecordStatus.satisfied:
            continue
        ttl = node_ttls.get(row.node_id)
        if ttl is None or row.satisfied_at is None:
            continue
        if ts - row.satisfied_at < ttl:
            continue
        stale_row = CoverageRecord(
            engagement_id=row.engagement_id,
            methodology_id=row.methodology_id,
            node_id=row.node_id,
            node_tier=row.node_tier,
            asset_class=row.asset_class,
            scope_subset=row.scope_subset,
            status=CoverageRecordStatus.stale,
            playbook_run_id=None,
            satisfied_at=None,
            notes=f"lapsed from record {row.id} (satisfied {row.satisfied_at.isoformat()})",
        )
        session.add(stale_row)
        lapsed.append(stale_row)
    if lapsed:
        session.add(
            AuditLog(
                engagement_id=engagement_id,
                actor_type=actor_type,
                actor_id=actor_id,
                event_type="coverage.stale_sweep",
                payload={
                    "count": len(lapsed),
                },
            )
        )
    session.flush()
    return lapsed
