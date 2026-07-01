"""Contributions surface (v0.10.0).

GitHub-style activity heatmap for one engagement, sourced from two
append-only tables:

- ``audit_log`` — one row per authorization-relevant event (finding
  created / validated / summary recorded / delete / import / etc.).
  Actor is a user, an agent, or ``system``.
- ``agent_executions`` — one row per Strategic / Tactical / Triage /
  Planner LLM call. Actor is the agent name.

Two endpoints:

- ``GET /engagements/{slug}/contributions/heatmap`` → per-day counts
  over the engagement's lifetime, plus the ``max_count`` used to scale
  cell shading on the frontend, plus the actors seen (so the filter
  dropdown can render without a second call).

- ``GET /engagements/{slug}/contributions/entries`` → paginated rows
  matching the filter (single date, or ``start``/``end`` range, plus
  optional actor_id and source). Default when nothing selected =
  today (UTC).

Both endpoints observe the same filters (``actor_id`` / ``source``)
so the heatmap re-shades when a filter is applied and the detail
list stays in sync.
"""
from __future__ import annotations

import contextlib
import uuid
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.models import (
    AgentExecution,
    AuditLog,
    Engagement,
    User,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


ContributionSource = Literal["audit", "agent_exec"]


def _get_engagement_or_404(session: DbSession, slug: str) -> Engagement:
    eng = session.execute(
        select(Engagement).where(Engagement.slug == slug)
    ).scalar_one_or_none()
    if eng is None:
        raise HTTPException(status_code=404, detail="engagement not found")
    return eng


def _resolve_window(
    eng: Engagement,
    start: date | None,
    end: date | None,
) -> tuple[date, date]:
    """Return the (start_date, end_date) window as inclusive UTC dates.

    Defaults to engagement.created_at → today (or engagement.end_date if
    the engagement is time-boxed and it's already past). Callers can
    override with explicit ``start``/``end`` for narrower slices.
    """
    today = datetime.now(tz=UTC).date()
    default_start = eng.created_at.date()
    default_end = today
    if eng.end_date and eng.end_date < today:
        default_end = eng.end_date
    return (start or default_start, end or default_end)


def _window_bounds(start_date: date, end_date: date) -> tuple[datetime, datetime]:
    """Convert an inclusive date window to UTC ``[start_ts, end_ts)`` timestamps."""
    start_ts = datetime.combine(start_date, time.min, tzinfo=UTC)
    # end is exclusive so the last-day rows land in the bucket
    end_ts = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=UTC)
    return start_ts, end_ts


def _actor_matches(
    row_actor_id: str | None,
    row_actor_kind: str,
    filter_actor_id: str | None,
) -> bool:
    """Client filter is a single ``actor_id`` string. For users this is a
    UUID string; for agents it's the agent name; for system it's the
    literal ``system``. Compares as-is."""
    if not filter_actor_id:
        return True
    return row_actor_id == filter_actor_id


# ---------------------------------------------------------------------------
# Heatmap
# ---------------------------------------------------------------------------


@router.get("/engagements/{slug}/contributions/heatmap")
def contributions_heatmap(
    slug: str,
    session: DbSession,
    _user: CurrentUser,
    actor_id: Annotated[
        str | None,
        Query(description="Filter to a single actor (user UUID, agent name, or 'system')."),
    ] = None,
    source: Annotated[
        ContributionSource | None,
        Query(description="Filter to 'audit' rows or 'agent_exec' rows only."),
    ] = None,
    start: Annotated[
        date | None,
        Query(description="Window start (UTC date, inclusive). Defaults to engagement.created_at."),
    ] = None,
    end: Annotated[
        date | None,
        Query(
            description=(
                "Window end (UTC date, inclusive). Defaults to today "
                "(or engagement.end_date)."
            ),
        ),
    ] = None,
) -> dict[str, Any]:
    """Per-day contribution counts + actor roster for the filter dropdown."""
    eng = _get_engagement_or_404(session, slug)
    start_date, end_date = _resolve_window(eng, start, end)
    start_ts, end_ts = _window_bounds(start_date, end_date)

    day_counts: dict[str, int] = defaultdict(int)
    # actor_kind is "analyst" (user) or "agent" or "system" — used to split
    # the dropdown into two groups on the frontend.
    actors_seen: dict[str, dict[str, Any]] = {}
    user_uuid_ids: set[str] = set()

    if source in (None, "audit"):
        audit_rows = session.execute(
            select(
                AuditLog.created_at,
                AuditLog.actor_type,
                AuditLog.actor_id,
            ).where(
                AuditLog.engagement_id == eng.id,
                AuditLog.created_at >= start_ts,
                AuditLog.created_at < end_ts,
            )
        ).all()
        for created_at, actor_type, row_actor_id in audit_rows:
            if not _actor_matches(row_actor_id, actor_type.value, actor_id):
                continue
            day_counts[created_at.date().isoformat()] += 1
            actor_kind = actor_type.value  # "user" | "agent" | "system"
            display_kind = "analyst" if actor_kind == "user" else actor_kind
            key = row_actor_id or "system"
            if key not in actors_seen:
                actors_seen[key] = {
                    "id": key,
                    "kind": display_kind,
                    "label": key,
                }
                if actor_kind == "user" and row_actor_id:
                    user_uuid_ids.add(row_actor_id)

    if source in (None, "agent_exec"):
        agent_rows = session.execute(
            select(
                AgentExecution.started_at,
                AgentExecution.agent,
            ).where(
                AgentExecution.engagement_id == eng.id,
                AgentExecution.started_at >= start_ts,
                AgentExecution.started_at < end_ts,
            )
        ).all()
        for started_at, agent in agent_rows:
            agent_name = agent.value if hasattr(agent, "value") else str(agent)
            if actor_id and actor_id != agent_name:
                continue
            day_counts[started_at.date().isoformat()] += 1
            if agent_name not in actors_seen:
                actors_seen[agent_name] = {
                    "id": agent_name,
                    "kind": "agent",
                    "label": agent_name,
                }

    # Resolve UUID-string user actors to human labels in one join
    if user_uuid_ids:
        try:
            uuid_objs = {uuid.UUID(s) for s in user_uuid_ids}
        except ValueError:
            uuid_objs = set()
        if uuid_objs:
            users = session.execute(
                select(User).where(User.id.in_(uuid_objs))
            ).scalars()
            for u in users:
                s = str(u.id)
                if s in actors_seen:
                    actors_seen[s]["label"] = u.display_name or u.email or s

    days = [{"date": d, "count": c} for d, c in sorted(day_counts.items())]
    max_count = max((d["count"] for d in days), default=0)

    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "max_count": max_count,
        "days": days,
        "actors": sorted(
            actors_seen.values(), key=lambda a: (a["kind"], a["label"].lower())
        ),
    }


# ---------------------------------------------------------------------------
# Entries
# ---------------------------------------------------------------------------


def _label_for_audit_actor(
    actor_type: str,
    actor_id: str | None,
    user_lookup: dict[str, User],
) -> tuple[str, str]:
    """Return (actor_kind, actor_label) for one audit row."""
    if actor_type == "user":
        if actor_id and actor_id in user_lookup:
            u = user_lookup[actor_id]
            return "analyst", (u.display_name or u.email or actor_id)
        return "analyst", actor_id or "(unknown)"
    if actor_type == "agent":
        return "agent", actor_id or "(agent)"
    return "system", actor_id or "system"


def _audit_summary(event_type: str, payload: dict[str, Any]) -> str:
    """One-line human summary for the activity table. Kept short."""
    if event_type == "finding.deleted":
        return f'deleted "{payload.get("title", "?")}"'
    if event_type == "finding.updated":
        changes = payload.get("changes", {}) or {}
        keys = ", ".join(sorted(changes.keys())) if changes else "no fields"
        return f"updated finding ({keys})"
    if event_type == "finding.validated":
        return f'validated → {payload.get("decision", "?")}'
    if event_type == "finding.summary_recorded":
        return f'recorded summary ({payload.get("body_chars", "?")} chars)'
    if event_type == "findings.imported":
        return (
            f'imported {payload.get("count", "?")} findings '
            f'via {payload.get("source", "?")}'
        )
    if event_type == "run.started":
        return f'started run: {payload.get("prompt", "?")[:60]}'
    if event_type == "run.completed":
        return "completed run"
    if event_type == "run.errored":
        return f'errored: {payload.get("error", "?")[:80]}'
    if event_type == "report.generated":
        return "generated PDF report"
    if event_type == "engagement.created":
        return f'created engagement "{payload.get("name", "?")}"'
    if event_type == "approval.decided":
        return f'approval {payload.get("decision", "?")}'
    if event_type == "scope.imported":
        return f'imported {payload.get("count", "?")} scope items'
    if event_type == "roadmap.pushed_to_github":
        return "pushed roadmap to GitHub"
    # Fallback: verbatim event name.
    return event_type


@router.get("/engagements/{slug}/contributions/entries")
def contributions_entries(
    slug: str,
    session: DbSession,
    _user: CurrentUser,
    date_: Annotated[
        date | None,
        Query(
            alias="date",
            description="Single UTC date. Overrides start/end if set. Default = today.",
        ),
    ] = None,
    start: Annotated[
        date | None,
        Query(description="Range start (UTC, inclusive). Ignored if 'date' set."),
    ] = None,
    end: Annotated[
        date | None,
        Query(description="Range end (UTC, inclusive). Ignored if 'date' set."),
    ] = None,
    actor_id: Annotated[
        str | None,
        Query(description="Filter to a single actor (user UUID, agent name, or 'system')."),
    ] = None,
    source: Annotated[
        ContributionSource | None,
        Query(description="Filter to 'audit' rows or 'agent_exec' rows only."),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    """Merged, newest-first activity list for one engagement.

    If neither ``date`` nor ``start``/``end`` is provided, defaults to
    today (UTC). This matches the tab's "no cell clicked" state so the
    analyst always lands on something.
    """
    eng = _get_engagement_or_404(session, slug)
    today = datetime.now(tz=UTC).date()

    if date_:
        start_date = end_date = date_
    elif start or end:
        start_date = start or eng.created_at.date()
        end_date = end or today
    else:
        start_date = end_date = today

    start_ts, end_ts = _window_bounds(start_date, end_date)

    entries: list[dict[str, Any]] = []
    user_ids_to_lookup: set[uuid.UUID] = set()

    if source in (None, "audit"):
        audit_rows = list(
            session.execute(
                select(AuditLog).where(
                    AuditLog.engagement_id == eng.id,
                    AuditLog.created_at >= start_ts,
                    AuditLog.created_at < end_ts,
                )
            ).scalars()
        )
        for row in audit_rows:
            if actor_id and row.actor_id != actor_id:
                continue
            if row.actor_type.value == "user" and row.actor_id:
                with contextlib.suppress(ValueError):
                    user_ids_to_lookup.add(uuid.UUID(row.actor_id))
        # Resolve user labels once
        user_lookup: dict[str, User] = {}
        if user_ids_to_lookup:
            for u in session.execute(
                select(User).where(User.id.in_(user_ids_to_lookup))
            ).scalars():
                user_lookup[str(u.id)] = u
        for row in audit_rows:
            if actor_id and row.actor_id != actor_id:
                continue
            actor_kind, actor_label = _label_for_audit_actor(
                row.actor_type.value, row.actor_id, user_lookup
            )
            entries.append(
                {
                    "when": row.created_at.isoformat(),
                    "actor_id": row.actor_id,
                    "actor_kind": actor_kind,
                    "actor_label": actor_label,
                    "source": "audit",
                    "action": row.event_type,
                    "summary": _audit_summary(row.event_type, row.payload or {}),
                }
            )

    if source in (None, "agent_exec"):
        agent_rows = list(
            session.execute(
                select(AgentExecution).where(
                    AgentExecution.engagement_id == eng.id,
                    AgentExecution.started_at >= start_ts,
                    AgentExecution.started_at < end_ts,
                )
            ).scalars()
        )
        for row in agent_rows:
            agent_name = (
                row.agent.value if hasattr(row.agent, "value") else str(row.agent)
            )
            if actor_id and actor_id != agent_name:
                continue
            entries.append(
                {
                    "when": row.started_at.isoformat(),
                    "actor_id": agent_name,
                    "actor_kind": "agent",
                    "actor_label": agent_name,
                    "source": "agent_exec",
                    "action": f"{agent_name}.{row.trigger.value}",
                    "summary": (
                        f"{agent_name} LLM call ({row.status.value})"
                        + (
                            f" — {row.tokens_in or 0}in/{row.tokens_out or 0}out"
                            if row.tokens_in or row.tokens_out
                            else ""
                        )
                    ),
                }
            )

    entries.sort(key=lambda e: e["when"], reverse=True)
    total = len(entries)
    page = entries[offset : offset + limit]

    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "total": total,
        "limit": limit,
        "offset": offset,
        "entries": page,
    }
