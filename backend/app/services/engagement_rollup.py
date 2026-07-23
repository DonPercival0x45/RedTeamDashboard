"""Engagement rollup service (v3 B2).

The deterministic aggregations the intelligence plane consumes: finding
counts, the milestone ``FindingsSummary`` (counts-only significance trigger),
the significant-finding gather (the IDs B3 batches for gather-then-analyze),
and a coverage-status rollup over ``CoverageRecord``.

Everything here is deterministic SQL — no LLM. The agent *interprets* these
compact structured inputs; it never generates them (architecture-answers Q5).
B3 (milestone runner) calls ``findings_summary`` for the trigger and
``significant_finding_ids`` for the gather. The strategy projection may layer
``finding_counts`` + ``coverage_rollup`` under the Memory view.

Soft-deleted findings (``deleted_at``) are excluded throughout — active-only,
matching the data-integrity enforcement already in main.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    CoverageNodeTier,
    CoverageRecord,
    Finding,
    FindingPhase,
    FindingStatus,
    Severity,
)

# Significance predicate (architecture-answers §B3): a finding is significant
# if it's new (created since the last analysis), not yet validated, or high
# severity. Configurable later — kept as named sets so the rule is one place.
_HIGH_SEVERITY = {Severity.high, Severity.critical}
# "Unvalidated" = still needs analyst sign-off. Resolved-disposed states
# (rejected / false_positive) are closed, not unvalidated.
_UNVALIDATED = {FindingStatus.pending_validation, FindingStatus.needs_review}


def findings_summary(
    session: Session, *, engagement_id: uuid.UUID, since: Any = None
) -> dict[str, int]:
    """Counts-only significance trigger (the milestone ``FindingsSummary``).

    ``since``: optional timestamp; findings created at/after it count as "new".
    Omit it for a first-pass / full-engagement summary (everything is "new").
    Excludes soft-deleted findings.
    """
    base = select(Finding).where(
        Finding.engagement_id == engagement_id,
        Finding.deleted_at.is_(None),
    )
    total = session.scalar(
        select(func.count()).select_from(base.subquery())
    ) or 0

    new_q = base
    if since is not None:
        new_q = base.where(Finding.created_at >= since)
    new = session.scalar(select(func.count()).select_from(new_q.subquery())) or 0

    unvalidated = session.scalar(
        select(func.count())
        .select_from(base.subquery())
        .where(Finding.status.in_(_UNVALIDATED))
    ) or 0
    high_severity = session.scalar(
        select(func.count())
        .select_from(base.subquery())
        .where(Finding.severity.in_(_HIGH_SEVERITY))
    ) or 0

    return {
        "new": int(new),
        "unvalidated": int(unvalidated),
        "high_severity": int(high_severity),
        "total": int(total),
    }


def significant_finding_ids(
    session: Session, *, engagement_id: uuid.UUID, since: Any = None
) -> list[uuid.UUID]:
    """The gather set for B3's gather-then-analyze: IDs of findings matching
    the significance predicate (``is_new OR not_validated OR high_severity``).

    Dedupes — a finding matching multiple predicates appears once. This is the
    re-query step: the milestone payload carries counts only (``findings_summary``),
    B3 calls this for the actual IDs to batch."""
    new_q = select(Finding.id).where(
        Finding.engagement_id == engagement_id, Finding.deleted_at.is_(None)
    )
    if since is not None:
        new_q = new_q.where(Finding.created_at >= since)
    new_ids = set(session.scalars(new_q).all())

    unvalidated_ids = set(
        session.scalars(
            select(Finding.id).where(
                Finding.engagement_id == engagement_id,
                Finding.deleted_at.is_(None),
                Finding.status.in_(_UNVALIDATED),
            )
        ).all()
    )
    high_ids = set(
        session.scalars(
            select(Finding.id).where(
                Finding.engagement_id == engagement_id,
                Finding.deleted_at.is_(None),
                Finding.severity.in_(_HIGH_SEVERITY),
            )
        ).all()
    )
    significant = new_ids | unvalidated_ids | high_ids
    return sorted(significant)


def finding_counts(
    session: Session, *, engagement_id: uuid.UUID
) -> dict[str, dict[str, int]]:
    """Counts grouped by severity / status / phase for display + rollups.
    Excludes soft-deleted findings."""
    out: dict[str, dict[str, int]] = {"by_severity": {}, "by_status": {}, "by_phase": {}}

    for col, enum_cls, key in (
        (Finding.severity, Severity, "by_severity"),
        (Finding.status, FindingStatus, "by_status"),
        (Finding.phase, FindingPhase, "by_phase"),
    ):
        rows = session.execute(
            select(col, func.count())
            .where(
                Finding.engagement_id == engagement_id,
                Finding.deleted_at.is_(None),
            )
            .group_by(col)
        ).all()
        out[key] = {str(enum_cls(value)): int(count) for value, count in rows if value is not None}
    return out


def coverage_rollup(
    session: Session, *, engagement_id: uuid.UUID
) -> dict[str, dict[str, int]]:
    """Status counts per node tier over ``CoverageRecord`` — the read-side view
    B1/B2 surface. Track A (A2) owns the *baseline-complete decision* (it emits
    ``baseline.completed``); this is just the deterministic status rollup that
    feeds the strategy projection and the significance of coverage gaps."""
    out: dict[str, dict[str, int]] = {
        CoverageNodeTier.baseline.value: {},
        CoverageNodeTier.exploration.value: {},
    }
    rows = session.execute(
        select(CoverageRecord.node_tier, CoverageRecord.status, func.count())
        .where(CoverageRecord.engagement_id == engagement_id)
        .group_by(CoverageRecord.node_tier, CoverageRecord.status)
    ).all()
    for tier, status, count in rows:
        out.setdefault(str(tier), {})[str(status)] = int(count)
    return out
