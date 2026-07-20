"""v2.5.0 — Analytics endpoints powering the Analytics page.

Aggregates findings + audit_log + scope_items into panel-ready shapes:

* GET /analytics/findings-over-time?engagement=<slug|all>&weeks=12
* GET /analytics/severity-breakdown?engagement=<slug|all>
* GET /analytics/scan-coverage?engagement=<slug|all>
* GET /analytics/top-findings?engagement=<slug|all>&limit=3
* GET /analytics/engagement-log?engagement=<slug|all>&limit=100

All aggregation runs against the primary DB — no separate warehouse. The
frontend calls these on mount + on engagement-picker change; polling is
window-focus revalidate only.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession
from app.models import (
    AuditLog,
    Engagement,
    Finding,
    ScopeItem,
    Severity,
    User,
)

router = APIRouter(tags=["analytics"])


# ---------------------------------------------------------------------------
# Response shapes
# ---------------------------------------------------------------------------


class WeekBucket(BaseModel):
    """One point on the Findings-over-time line/bar chart. `label` is the
    short axis tick (`W1`..`W12`); `week_start` is the ISO date the
    bucket opens on. `count` is total findings created in that week."""

    label: str
    week_start: str
    count: int


class SeverityBreakdown(BaseModel):
    """One tile of the Severity-breakdown bar chart."""

    severity: Severity
    count: int


class ScanCoverage(BaseModel):
    """Percent of in-scope items that appear in at least one finding.
    Rough proxy for "how much of scope has been touched" — good enough
    for the Analytics widget until we ship a first-class coverage model.
    """

    percent: int
    covered: int
    total: int


class TopFindingRow(BaseModel):
    """One row of the Top-findings mini-list."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    engagement_slug: str
    title: str
    severity: Severity
    created_at: datetime


class EngagementLogRow(BaseModel):
    """One row of the Engagement Log. `actor_display` resolves user
    UUID → display_name/email so the analyst sees "Nasir Christian
    made an engagement" instead of a UUID. `payload` carries the
    event-type-specific context (engagement name, scope value, etc.).
    Frontend picks a friendly verb from `event_type`."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    engagement_id: uuid.UUID | None
    engagement_slug: str | None
    engagement_name: str | None
    engagement_time_frame: str | None
    engagement_status: str | None
    actor_type: str
    actor_id: str | None
    actor_display: str | None
    event_type: str
    payload: dict[str, Any]
    created_at: datetime


# Event-type allowlist for the Engagement Log. Filters the raw audit_log
# down to entries that describe engagement-level actions an analyst
# cares about — not every "provider_key.updated" ping.
ENGAGEMENT_LOG_EVENT_TYPES = (
    "engagement.created",
    "engagement.archived",
    "engagement.unarchived",
    "engagement.flushed",
    "engagement.updated",
    "engagement.auto_assess_updated",
    "mcp.engagement.created",
    "mcp.engagement.archived",
    "scope.imported",
    "scope.item.created",
    "scope.item.updated",
    "scope.item.deleted",
    "mcp.scope.added",
    "findings.imported",
    "finding.created_manual",
    "finding.deleted",
    "finding.validated",
    "finding.triaged",
    "finding.updated",
    "findings.bulk_deleted",
    "findings.bulk_updated",
    "findings.merged",
    "entities.imported",
    "scanner_import.committed",
    "run.requested",
    "task.cancelled",
    "task.retried",
    "attachment.uploaded",
    "attachment.deleted",
    "approval.decided",
    "suggestion.accepted",
    "suggestion.dismissed",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_engagement_filter(
    session: DbSession, slug: str | None
) -> uuid.UUID | None:
    """Turn the `engagement=<slug|all|null>` query param into either an
    engagement UUID or None (meaning 'no filter — aggregate across all
    engagements'). Rejects unknown slugs with 404."""
    if slug is None or slug == "" or slug == "all":
        return None
    row = session.execute(
        select(Engagement.id).where(Engagement.slug == slug)
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="engagement not found")
    return row[0]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/analytics/findings-over-time", response_model=list[WeekBucket])
def findings_over_time(
    session: DbSession,
    _user: CurrentUser,
    engagement: Annotated[str | None, Query(description="Slug or 'all'.")] = None,
    period: Annotated[
        str,
        Query(description="'day' | 'week' | 'month' | 'custom'."),
    ] = "week",
    points: Annotated[int, Query(ge=1, le=180)] = 12,
    start: Annotated[str | None, Query(description="ISO date, custom mode.")] = None,
    end: Annotated[str | None, Query(description="ISO date, custom mode.")] = None,
) -> list[WeekBucket]:
    """v2.5.2 — daily/weekly/monthly buckets or a custom window.

    * ``period=day``   → last ``points`` days (default 30 elsewhere,
      12 here to keep the response small).
    * ``period=week``  → last ``points`` weeks, Monday-aligned (v2.5.0
      default; ``points`` alias for the old ``weeks`` param).
    * ``period=month`` → last ``points`` months, month-start-aligned.
    * ``period=custom`` → ``start`` + ``end`` required. Bucket size
      auto-picks: ≤45 days → day, ≤52 weeks → week, else month.

    Empty buckets are returned as zero so charts render a flat line
    instead of gapping.
    """
    eng_id = _resolve_engagement_filter(session, engagement)
    today = datetime.now(tz=UTC).date()

    def _month_add(d, months):
        # Simple month arithmetic without a calendar dep; clamps day
        # to the target month's length.
        m = d.month - 1 + months
        year = d.year + m // 12
        month = m % 12 + 1
        # Snap to the 1st of the month — we bucket by month-start.
        return d.replace(year=year, month=month, day=1)

    # Compute (unit, bucket_starts[]) for the requested range.
    if period == "custom":
        if not start or not end:
            raise HTTPException(
                status_code=400, detail="custom period requires start + end"
            )
        try:
            start_d = datetime.fromisoformat(start).date()
            end_d = datetime.fromisoformat(end).date()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"bad ISO date: {exc}") from exc
        if end_d < start_d:
            raise HTTPException(status_code=400, detail="end < start")
        span_days = (end_d - start_d).days
        if span_days <= 45:
            unit = "day"
        elif span_days <= 52 * 7:
            unit = "week"
        else:
            unit = "month"
        bucket_starts = []
        cursor = start_d
        if unit == "day":
            while cursor <= end_d:
                bucket_starts.append(cursor)
                cursor += timedelta(days=1)
        elif unit == "week":
            cursor -= timedelta(days=cursor.weekday())
            while cursor <= end_d:
                bucket_starts.append(cursor)
                cursor += timedelta(weeks=1)
        else:
            cursor = cursor.replace(day=1)
            while cursor <= end_d:
                bucket_starts.append(cursor)
                cursor = _month_add(cursor, 1)
    elif period == "day":
        unit = "day"
        first = today - timedelta(days=points - 1)
        bucket_starts = [first + timedelta(days=i) for i in range(points)]
    elif period == "month":
        unit = "month"
        first = today.replace(day=1)
        first = _month_add(first, -(points - 1))
        bucket_starts = [_month_add(first, i) for i in range(points)]
    else:  # week (default)
        unit = "week"
        current_monday = today - timedelta(days=today.weekday())
        first = current_monday - timedelta(weeks=points - 1)
        bucket_starts = [first + timedelta(weeks=i) for i in range(points)]

    start_dt = datetime.combine(bucket_starts[0], datetime.min.time()).replace(tzinfo=UTC)

    stmt = select(Finding.created_at).where(Finding.created_at >= start_dt)
    if eng_id is not None:
        stmt = stmt.where(Finding.engagement_id == eng_id)
    rows = list(session.execute(stmt).scalars())

    counts = {bs.isoformat(): 0 for bs in bucket_starts}

    def _bucket_key(created_date):
        if unit == "day":
            return created_date.isoformat()
        if unit == "week":
            monday = created_date - timedelta(days=created_date.weekday())
            return monday.isoformat()
        return created_date.replace(day=1).isoformat()

    for created in rows:
        key = _bucket_key(created.date())
        if key in counts:
            counts[key] += 1

    def _label(i, bs):
        if unit == "day":
            return bs.strftime("%b %d")
        if unit == "week":
            return f"W{i + 1}"
        return bs.strftime("%b %Y")

    return [
        WeekBucket(label=_label(i, bs), week_start=bs.isoformat(), count=counts[bs.isoformat()])
        for i, bs in enumerate(bucket_starts)
    ]


@router.get(
    "/analytics/severity-breakdown", response_model=list[SeverityBreakdown]
)
def severity_breakdown(
    session: DbSession,
    _user: CurrentUser,
    engagement: Annotated[str | None, Query(description="Slug or 'all'.")] = None,
) -> list[SeverityBreakdown]:
    """Findings grouped by severity. Always returns all five severities
    (with count=0 when empty) so the chart X-axis is stable."""
    eng_id = _resolve_engagement_filter(session, engagement)
    stmt = (
        select(Finding.severity, func.count(Finding.id))
        .where(Finding.deleted_at.is_(None))
        .group_by(Finding.severity)
    )
    if eng_id is not None:
        stmt = stmt.where(Finding.engagement_id == eng_id)
    counts = {row[0]: int(row[1]) for row in session.execute(stmt).all()}
    return [
        SeverityBreakdown(severity=sev, count=counts.get(sev, 0))
        for sev in (
            Severity.critical,
            Severity.high,
            Severity.medium,
            Severity.low,
            Severity.info,
        )
    ]


@router.get("/analytics/scan-coverage", response_model=ScanCoverage)
def scan_coverage(
    session: DbSession,
    _user: CurrentUser,
    engagement: Annotated[str | None, Query(description="Slug or 'all'.")] = None,
) -> ScanCoverage:
    """Rough scope-touched percentage. Numerator: distinct in-scope
    values that appear as `target` on at least one finding. Denominator:
    total non-exclusion scope items. Not a real coverage model — enough
    signal for the widget until we ship one."""
    eng_id = _resolve_engagement_filter(session, engagement)

    total_stmt = select(func.count(ScopeItem.id)).where(
        ScopeItem.is_exclusion.is_(False)
    )
    if eng_id is not None:
        total_stmt = total_stmt.where(ScopeItem.engagement_id == eng_id)
    total = int(session.execute(total_stmt).scalar_one() or 0)
    if total == 0:
        return ScanCoverage(percent=0, covered=0, total=0)

    values_stmt = select(ScopeItem.value).where(ScopeItem.is_exclusion.is_(False))
    if eng_id is not None:
        values_stmt = values_stmt.where(ScopeItem.engagement_id == eng_id)
    scope_values = {v.lower() for v in session.execute(values_stmt).scalars() if v}

    finding_stmt = select(Finding.target).where(Finding.target.is_not(None))
    if eng_id is not None:
        finding_stmt = finding_stmt.where(Finding.engagement_id == eng_id)
    finding_targets = {
        t.lower() for t in session.execute(finding_stmt).scalars() if t
    }

    covered = len(scope_values & finding_targets)
    percent = int(round(covered / total * 100)) if total else 0
    percent = min(100, percent)
    return ScanCoverage(percent=percent, covered=covered, total=total)


@router.get("/analytics/top-findings", response_model=list[TopFindingRow])
def top_findings(
    session: DbSession,
    _user: CurrentUser,
    engagement: Annotated[str | None, Query(description="Slug or 'all'.")] = None,
    limit: Annotated[int, Query(ge=1, le=25)] = 3,
) -> list[TopFindingRow]:
    """Recent high-severity findings for the mini-list on the Analytics
    page. Ordered by severity rank DESC, then created_at DESC. Skips
    deleted findings."""
    eng_id = _resolve_engagement_filter(session, engagement)
    stmt = (
        select(Finding, Engagement.slug)
        .join(Engagement, Engagement.id == Finding.engagement_id)
        .where(Finding.deleted_at.is_(None))
        .order_by(
            # Severity enum stores string values; we can't ORDER BY on the
            # enum directly and get semantic order, so filter to top
            # severities and then created_at desc.
            Finding.created_at.desc()
        )
    )
    if eng_id is not None:
        stmt = stmt.where(Finding.engagement_id == eng_id)

    rows = list(session.execute(stmt).all())
    # In-Python severity ranking so we don't rely on Postgres enum sort
    # order (which is definition order).
    rank = {
        Severity.critical: 5,
        Severity.high: 4,
        Severity.medium: 3,
        Severity.low: 2,
        Severity.info: 1,
    }
    rows.sort(key=lambda pair: (rank.get(pair[0].severity, 0), pair[0].created_at), reverse=True)
    return [
        TopFindingRow(
            id=finding.id,
            engagement_slug=slug,
            title=finding.title or finding.tool or "untitled",
            severity=finding.severity,
            created_at=finding.created_at,
        )
        for finding, slug in rows[:limit]
    ]


@router.get(
    "/analytics/engagement-log", response_model=list[EngagementLogRow]
)
def engagement_log(
    session: DbSession,
    _user: CurrentUser,
    engagement: Annotated[str | None, Query(description="Slug or 'all'.")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[EngagementLogRow]:
    """Scrollable feed of engagement-level actions. Filtered by
    `ENGAGEMENT_LOG_EVENT_TYPES` so we skip low-signal noise (provider
    key CRUD, test events, etc). Joins actor UUID → user display_name
    and engagement UUID → slug/name/time_frame/status so the frontend
    can render rich context without extra lookups."""
    eng_id = _resolve_engagement_filter(session, engagement)

    stmt = (
        select(AuditLog, Engagement.slug, Engagement.name, Engagement.time_frame, Engagement.status)
        .outerjoin(Engagement, Engagement.id == AuditLog.engagement_id)
        .where(AuditLog.event_type.in_(ENGAGEMENT_LOG_EVENT_TYPES))
        .order_by(AuditLog.created_at.desc())
        .limit(limit)
    )
    if eng_id is not None:
        stmt = stmt.where(AuditLog.engagement_id == eng_id)

    rows = list(session.execute(stmt).all())

    # Resolve actor UUIDs → display names in one query.
    actor_ids: set[uuid.UUID] = set()
    for audit, _slug, _name, _tf, _status in rows:
        if audit.actor_type.value == "user" and audit.actor_id:
            try:
                actor_ids.add(uuid.UUID(audit.actor_id))
            except (ValueError, TypeError):
                continue
    display_by_id: dict[str, str] = {}
    if actor_ids:
        for user_row in session.execute(
            select(User).where(User.id.in_(actor_ids))
        ).scalars():
            display_by_id[str(user_row.id)] = (
                user_row.display_name or user_row.email or str(user_row.id)
            )

    return [
        EngagementLogRow(
            id=audit.id,
            engagement_id=audit.engagement_id,
            engagement_slug=slug,
            engagement_name=name,
            engagement_time_frame=time_frame.value if time_frame else None,
            engagement_status=status_val.value if status_val else None,
            actor_type=audit.actor_type.value,
            actor_id=audit.actor_id,
            actor_display=display_by_id.get(audit.actor_id) if audit.actor_id else None,
            event_type=audit.event_type,
            payload=audit.payload or {},
            created_at=audit.created_at,
        )
        for audit, slug, name, time_frame, status_val in rows
    ]
