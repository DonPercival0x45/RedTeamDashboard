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

import hashlib
import json
import uuid
from typing import Any

from sqlalchemy import case, exists, func, or_, select, true
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import (
    CoverageNodeTier,
    CoverageRecord,
    Finding,
    FindingOrigin,
    FindingPhase,
    FindingStatus,
    Severity,
)
from app.services.memory import estimate_tokens

# Significance predicate (architecture-answers §B3): a finding is significant
# if it's new (created since the last analysis), not yet validated, or high
# severity. Configurable later — kept as named sets so the rule is one place.
_HIGH_SEVERITY = {Severity.high, Severity.critical}
# "Unvalidated" = still needs analyst sign-off. Resolved-disposed states
# (rejected / false_positive) are closed, not unvalidated.
_UNVALIDATED = {FindingStatus.pending_validation, FindingStatus.needs_review}


def findings_summary(
    session: Session,
    *,
    engagement_id: uuid.UUID,
    since: Any = None,
    thread_id: uuid.UUID | None = None,
) -> dict[str, int]:
    """Counts-only significance trigger (the milestone ``FindingsSummary``).

    ``thread_id`` uses canonical run lineage for the "new" count. Otherwise,
    ``since`` optionally bounds new findings by creation time; with neither,
    every active finding counts as new. Excludes soft-deleted findings.
    """
    # All filters applied directly on the Finding table in one WHERE each —
    # NOT via select_from(subquery).where(...), whose outer .where() would
    # reference the bare Finding table and bypass the engagement filter
    # (the cross-engagement count leak CI caught on the first push).
    base_filters = [
        Finding.engagement_id == engagement_id,
        Finding.deleted_at.is_(None),
    ]
    new_q = select(func.count(func.distinct(Finding.id))).where(*base_filters)
    if thread_id is not None:
        new_q = new_q.join(
            FindingOrigin, FindingOrigin.finding_id == Finding.id
        ).where(FindingOrigin.thread_id == thread_id)
    elif since is not None:
        new_q = new_q.where(Finding.created_at >= since)

    total = session.scalar(select(func.count(Finding.id)).where(*base_filters)) or 0
    new = session.scalar(new_q) or 0
    unvalidated = session.scalar(
        select(func.count(Finding.id)).where(*base_filters, Finding.status.in_(_UNVALIDATED))
    ) or 0
    high_severity = session.scalar(
        select(func.count(Finding.id)).where(*base_filters, Finding.severity.in_(_HIGH_SEVERITY))
    ) or 0

    return {
        "new": int(new),
        "unvalidated": int(unvalidated),
        "high_severity": int(high_severity),
        "total": int(total),
    }


def significant_finding_ids(
    session: Session,
    *,
    engagement_id: uuid.UUID,
    since: Any = None,
    thread_id: uuid.UUID | None = None,
) -> list[uuid.UUID]:
    """The gather set for B3's gather-then-analyze: IDs of findings matching
    the significance predicate (``is_new OR not_validated OR high_severity``).

    For run milestones, ``thread_id`` defines ``is_new`` through canonical
    ``FindingOrigin`` lineage. Other milestone types may use ``since``. With
    neither supplied, all active findings are treated as new (legacy B3
    behavior). Dedupes findings that match more than one predicate.
    """
    new_q = select(Finding.id).where(
        Finding.engagement_id == engagement_id, Finding.deleted_at.is_(None)
    )
    if thread_id is not None:
        new_q = new_q.join(
            FindingOrigin, FindingOrigin.finding_id == Finding.id
        ).where(FindingOrigin.thread_id == thread_id)
    elif since is not None:
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


def significant_finding_batch(
    session: Session,
    *,
    engagement_id: uuid.UUID,
    since: Any = None,
    thread_id: uuid.UUID | None = None,
    token_budget: int | None = None,
    max_items: int | None = None,
) -> dict[str, Any]:
    """Return bounded finding evidence plus a stable automatic-run fingerprint.

    IDs alone do not give the intelligence model evidence to analyze. This
    projection includes only compact analyst-facing fields (never raw ``details``)
    and stops at both an item ceiling and token estimate. Critical/high and
    unvalidated findings sort first, then the most recently changed rows.

    The fingerprint covers the included state, total significant count, and
    latest update timestamp. A changed finding therefore produces a new batch,
    while an unchanged pending/high finding cannot burn tokens on every later
    milestone.
    """
    budget = max(
        1,
        settings.intelligence_finding_token_budget
        if token_budget is None
        else token_budget,
    )
    limit = max(
        1,
        settings.intelligence_max_significant_findings
        if max_items is None
        else max_items,
    )
    base = [
        Finding.engagement_id == engagement_id,
        Finding.deleted_at.is_(None),
    ]
    if thread_id is not None:
        is_new = exists(
            select(FindingOrigin.id).where(
                FindingOrigin.finding_id == Finding.id,
                FindingOrigin.thread_id == thread_id,
            )
        )
    elif since is not None:
        is_new = Finding.created_at >= since
    else:
        is_new = true()
    significant = or_(
        is_new,
        Finding.status.in_(_UNVALIDATED),
        Finding.severity.in_(_HIGH_SEVERITY),
    )
    total, latest_updated_at = session.execute(
        select(func.count(Finding.id), func.max(Finding.updated_at)).where(
            *base, significant
        )
    ).one()
    severity_order = case(
        (Finding.severity == Severity.critical, 0),
        (Finding.severity == Severity.high, 1),
        (Finding.severity == Severity.medium, 2),
        (Finding.severity == Severity.low, 3),
        else_=4,
    )
    validation_order = case(
        (Finding.status.in_(_UNVALIDATED), 0),
        else_=1,
    )
    rows = session.execute(
        select(Finding, is_new.label("is_new"))
        .where(*base, significant)
        .order_by(
            severity_order,
            validation_order,
            Finding.updated_at.desc(),
            Finding.id,
        )
        .limit(limit)
    ).all()

    items: list[dict[str, Any]] = []
    running_tokens = 0
    for finding, row_is_new in rows:
        item = {
            "id": str(finding.id),
            "title": finding.title[:300],
            "summary": (finding.summary or "")[:600] or None,
            "severity": finding.severity.value,
            "status": finding.status.value,
            "phase": finding.phase.value,
            "target": (finding.target or "")[:300] or None,
            "source_tool": finding.source_tool,
            "tags": list((finding.tags or [])[:10]),
            "is_new": bool(row_is_new),
            "updated_at": finding.updated_at.isoformat(),
        }
        item_tokens = estimate_tokens(*item.values())
        # Preserve a one-item evidence guarantee, matching the Memory projection.
        # Production summaries are truncated above, so the default 4k budget
        # still hard-bounds that first item; tiny test/operator budgets may
        # intentionally report token_estimate > token_budget for this one row.
        if items and running_tokens + item_tokens > budget:
            break
        items.append(item)
        running_tokens += item_tokens

    fingerprint_payload = {
        "total": int(total or 0),
        "latest_updated_at": (
            latest_updated_at.isoformat() if latest_updated_at is not None else None
        ),
        "included": [
            {
                "id": item["id"],
                "severity": item["severity"],
                "status": item["status"],
                "updated_at": item["updated_at"],
            }
            for item in items
        ],
    }
    fingerprint = hashlib.sha256(
        json.dumps(fingerprint_payload, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return {
        "items": items,
        "total": int(total or 0),
        "included": len(items),
        "capped": len(items) < int(total or 0),
        "token_estimate": running_tokens,
        "token_budget": budget,
        "fingerprint": fingerprint,
    }


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
