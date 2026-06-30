"""HTTP surface for the engagement Status tab (v0.8.0).

One read endpoint::

    GET /engagements/{slug}/status -> agents + tasks + approvals

The Status tab on each engagement page calls this once on mount and
again whenever the analyst flips back to it. Each native status enum is
mapped to one of four display colours that the slide-over uses to render
boxes (green=active, blue=pending, red=failed, purple=completed).

Retry endpoints live next to their source entities:
- POST /agent-executions/{id}/retry (in this file)
- POST /tasks/{id}/retry            (in this file)

Approvals are not retried — they're decided via the existing approvals
endpoints.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import CurrentNonGuestUser, CurrentUser, DbSession
from app.models import (
    AgentExecution,
    AgentExecutionStatus,
    Approval,
    ApprovalStatus,
    Engagement,
    Task,
    TaskStatus,
)
from app.schemas.status import (
    EngagementStatusResponse,
    StatusColor,
    StatusEntity,
)

router = APIRouter()


def _engagement_by_slug(session: Session, slug: str) -> Engagement:
    eng = session.execute(
        select(Engagement).where(Engagement.slug == slug)
    ).scalar_one_or_none()
    if eng is None:
        raise HTTPException(status_code=404, detail=f"engagement '{slug}' not found")
    return eng


# ── status colour mappers ────────────────────────────────────────────────


def _agent_color(s: AgentExecutionStatus) -> StatusColor:
    if s == AgentExecutionStatus.running:
        return "active"
    if s == AgentExecutionStatus.completed:
        return "completed"
    return "failed"  # AgentExecutionStatus only has running/completed/failed


def _task_color(s: TaskStatus) -> StatusColor:
    if s in (TaskStatus.pending, TaskStatus.deferred):
        return "pending"
    if s in (TaskStatus.dispatched, TaskStatus.running):
        return "active"
    if s == TaskStatus.completed:
        return "completed"
    return "failed"  # failed or cancelled


def _approval_color(s: ApprovalStatus) -> StatusColor:
    if s == ApprovalStatus.pending:
        return "pending"
    if s == ApprovalStatus.denied:
        return "failed"
    return "completed"  # approved | edited | auto


# ── entity → StatusEntity adapters ───────────────────────────────────────


def _agent_to_entity(row: AgentExecution) -> StatusEntity:
    agent_label = row.agent.value.capitalize()
    color = _agent_color(row.status)
    return StatusEntity(
        id=row.id,
        kind="agent",
        title=f"{agent_label} agent",
        subtitle=(
            f"{row.model_provider}/{row.model_name}"
            if row.model_provider or row.model_name
            else None
        ),
        color=color,
        raw_status=row.status.value,
        started_at=row.started_at,
        completed_at=row.completed_at,
        # Agent retry needs per-kind dispatch logic (Strategic ↔ finding,
        # Tactical ↔ task, Triage ↔ finding). Coming in a follow-up commit
        # — for now the box surfaces the failure but the Retry button
        # only renders on tasks. Planner has its own re-evaluate button
        # on /settings/feedback.
        retryable=False,
        log={
            "agent": row.agent.value,
            "trigger": row.trigger.value,
            "model_provider": row.model_provider,
            "model_name": row.model_name,
            "tokens_in": row.tokens_in,
            "tokens_out": row.tokens_out,
            "cost_usd": (
                str(row.cost_usd) if row.cost_usd is not None else None
            ),
            "input": row.input,
            "output": row.output,
            "error": row.error,
        },
    )


def _task_to_entity(row: Task) -> StatusEntity:
    payload = row.payload or {}
    tool = payload.get("tool") or payload.get("tool_name")
    target = payload.get("target")
    color = _task_color(row.status)
    return StatusEntity(
        id=row.id,
        kind="task",
        title=row.title,
        subtitle=(f"{tool} → {target}" if tool and target else tool or target),
        color=color,
        raw_status=row.status.value,
        started_at=row.dispatched_at,
        completed_at=row.completed_at,
        retryable=color == "failed",
        log={
            "kind": row.kind.value,
            "owner_eligibility": row.owner_eligibility.value,
            "finding_id": str(row.finding_id) if row.finding_id else None,
            "run_id": str(row.run_id) if row.run_id else None,
            "dispatched_at": (
                row.dispatched_at.isoformat() if row.dispatched_at else None
            ),
            "payload": payload,
        },
    )


def _approval_to_entity(row: Approval) -> StatusEntity:
    color = _approval_color(row.status)
    return StatusEntity(
        id=row.id,
        kind="approval",
        title=f"{row.tool_name} approval",
        subtitle=row.risk.value,
        color=color,
        raw_status=row.status.value,
        started_at=row.created_at,
        completed_at=row.decided_at,
        retryable=False,
        log={
            "thread_id": row.thread_id,
            "node": row.node,
            "tool_name": row.tool_name,
            "tool_args": row.tool_args,
            "risk": row.risk.value,
            "scope_check": row.scope_check,
            "decision_args": row.decision_args,
            "authorization_id": (
                str(row.authorization_id) if row.authorization_id else None
            ),
        },
    )


# ── read endpoint ────────────────────────────────────────────────────────


@router.get(
    "/engagements/{slug}/status",
    response_model=EngagementStatusResponse,
)
def get_engagement_status(
    slug: str,
    session: DbSession,
    _user: CurrentUser,
) -> EngagementStatusResponse:
    """Aggregate live + historical execution state for one engagement.

    Each native status enum maps to a display colour the Status tab
    renders as a box border + pill. Newest first within each list.
    """
    eng = _engagement_by_slug(session, slug)

    agents = list(
        session.execute(
            select(AgentExecution)
            .where(AgentExecution.engagement_id == eng.id)
            .order_by(AgentExecution.started_at.desc())
            .limit(200)
        ).scalars()
    )
    tasks = list(
        session.execute(
            select(Task)
            .where(Task.engagement_id == eng.id)
            .order_by(Task.created_at.desc())
            .limit(200)
        ).scalars()
    )
    approvals = list(
        session.execute(
            select(Approval)
            .where(Approval.engagement_id == eng.id)
            .order_by(Approval.created_at.desc())
            .limit(200)
        ).scalars()
    )

    return EngagementStatusResponse(
        agents=[_agent_to_entity(a) for a in agents],
        tasks=[_task_to_entity(t) for t in tasks],
        approvals=[_approval_to_entity(a) for a in approvals],
    )


# ── retry endpoints ──────────────────────────────────────────────────────


@router.post(
    "/tasks/{task_id}/retry",
    response_model=StatusEntity,
)
def retry_task(
    task_id: uuid.UUID,
    session: DbSession,
    user: CurrentNonGuestUser,
) -> StatusEntity:
    """Reset a failed task back to ``pending`` so Tactical re-dispatches it.

    The status flip alone doesn't re-run the worker — Tactical's queue
    consumer picks pending tasks; the simplest possible retry just bumps
    status and lets the existing pipeline run again.
    """
    row = session.get(Task, task_id)
    if row is None:
        raise HTTPException(status_code=404, detail="task not found")
    if row.status not in (TaskStatus.failed, TaskStatus.cancelled):
        raise HTTPException(
            status_code=400,
            detail="only failed or cancelled tasks can be retried",
        )

    row.status = TaskStatus.pending
    row.dispatched_at = None
    row.completed_at = None
    row.run_id = None
    session.commit()
    session.refresh(row)
    return _task_to_entity(row)


# Discord webhook notifier hooks this to learn whether an entity has
# reached a terminal state (worth pinging the channel about).
def is_terminal_color(color: StatusColor) -> bool:
    return color in ("completed", "failed")


__all__ = ["router", "is_terminal_color"]
