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
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import CurrentNonGuestUser, CurrentUser, DbSession, RedisClient
from app.models import (
    AgentExecution,
    AgentExecutionStatus,
    AgentName,
    Approval,
    ApprovalStatus,
    Engagement,
    Finding,
    Task,
    TaskStatus,
)
from app.runs.events import decode_envelope
from app.runs.streams import outbound_stream
from app.schemas.status import (
    EngagementStatusResponse,
    StatusColor,
    StatusEntity,
    StatusTransition,
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


# ── status transition history (derived) ─────────────────────────────────


def _agent_history(row: AgentExecution) -> list[StatusTransition]:
    """AgentExecution rows are born running. If they have a completed_at,
    the entity reached its terminal colour at that time."""
    history: list[StatusTransition] = []
    if row.started_at:
        history.append(
            StatusTransition(
                status="active",
                raw_status=AgentExecutionStatus.running.value,
                at=row.started_at,
            )
        )
    if row.completed_at and row.status != AgentExecutionStatus.running:
        history.append(
            StatusTransition(
                status=_agent_color(row.status),
                raw_status=row.status.value,
                at=row.completed_at,
            )
        )
    return history


def _task_history(row: Task) -> list[StatusTransition]:
    """Tasks pass through pending → dispatched/running → completed/failed.
    Derive each leg from the explicit timestamps the model stores; for the
    Task it's the only entity that records a `dispatched_at` separate from
    `completed_at`."""
    history: list[StatusTransition] = [
        StatusTransition(
            status="pending",
            raw_status=TaskStatus.pending.value,
            at=row.created_at,
        )
    ]
    if row.dispatched_at:
        history.append(
            StatusTransition(
                status="active",
                raw_status=TaskStatus.dispatched.value,
                at=row.dispatched_at,
            )
        )
    if row.completed_at and row.status not in (
        TaskStatus.pending,
        TaskStatus.dispatched,
        TaskStatus.running,
    ):
        history.append(
            StatusTransition(
                status=_task_color(row.status),
                raw_status=row.status.value,
                at=row.completed_at,
            )
        )
    return history


def _approval_history(row: Approval) -> list[StatusTransition]:
    """Approvals are pending until the analyst decides. The terminal
    colour reflects the decision (approved/edited/auto → completed;
    denied → failed)."""
    history: list[StatusTransition] = [
        StatusTransition(
            status="pending",
            raw_status=ApprovalStatus.pending.value,
            at=row.created_at,
        )
    ]
    if row.decided_at and row.status != ApprovalStatus.pending:
        history.append(
            StatusTransition(
                status=_approval_color(row.status),
                raw_status=row.status.value,
                at=row.decided_at,
            )
        )
    return history


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
        # Triage retry is wired (POST /agent-executions/{id}/retry). Strategic
        # and Tactical retry need richer per-kind dispatch — coming in a
        # follow-up commit. Planner has its own re-evaluate button on
        # /settings/feedback.
        retryable=(
            row.status == AgentExecutionStatus.failed
            and row.agent == AgentName.triage
        ),
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
        history=_agent_history(row),
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
        history=_task_history(row),
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
        history=_approval_history(row),
    )


# ── read endpoint ────────────────────────────────────────────────────────


def _reconcile_running_runs(
    session: Session,
    redis_client: Any,
    eng_id: Any,
) -> None:
    """v0.8.1: lazy terminal update for run-tied AgentExecution rows.

    The run-start endpoint stamps an AgentExecution row with
    ``input.thread_id`` set so the Status tab paints an "active" box
    immediately. The worker doesn't update the row when it finishes
    (it can't — the worker only emits events to Redis), so we scan
    the engagement's outbound stream here for ``run.completed`` /
    ``run.errored`` events and flip the matching rows.

    Cost is bounded: only checks rows that are still ``running`` AND
    carry ``input.thread_id``. The XRANGE scan looks at the last 500
    events on the stream — enough to catch any terminal event from
    the last few hours of run activity.
    """
    pending = list(
        session.execute(
            select(AgentExecution).where(
                AgentExecution.engagement_id == eng_id,
                AgentExecution.status == AgentExecutionStatus.running,
                AgentExecution.input["thread_id"].as_string().isnot(None),
            )
        ).scalars()
    )
    if not pending:
        return

    try:
        raw = redis_client.xrange(outbound_stream(eng_id), count=500)
    except Exception:  # noqa: BLE001 — Redis hiccup must not break the status read
        return

    terminal: dict[str, tuple[str, str | None]] = {}
    for _msg_id, fields in raw or []:
        try:
            payload = decode_envelope(fields)
        except (ValueError, KeyError):
            continue
        event_type = payload.get("type")
        if event_type not in ("run.completed", "run.errored"):
            continue
        thread_id = str(payload.get("thread_id") or "")
        if not thread_id:
            continue
        # Last terminal event per thread wins.
        terminal[thread_id] = (
            event_type,
            str(payload.get("error") or "") if event_type == "run.errored" else None,
        )

    if not terminal:
        return

    now = datetime.now(tz=UTC)
    dirty = False
    for row in pending:
        thread_id = str((row.input or {}).get("thread_id") or "")
        if thread_id and thread_id in terminal:
            event_type, error = terminal[thread_id]
            if event_type == "run.completed":
                row.status = AgentExecutionStatus.completed
            else:
                row.status = AgentExecutionStatus.failed
                row.error = (error or "run errored")[:2000]
            row.completed_at = now
            dirty = True
    if dirty:
        session.commit()


@router.get(
    "/engagements/{slug}/status",
    response_model=EngagementStatusResponse,
)
def get_engagement_status(
    slug: str,
    session: DbSession,
    redis_client: RedisClient,
    _user: CurrentUser,
) -> EngagementStatusResponse:
    """Aggregate live + historical execution state for one engagement.

    Each native status enum maps to a display colour the Status tab
    renders as a box border + pill. Newest first within each list.

    v0.8.1: before returning, reconciles any run-tied AgentExecution
    rows in 'running' state against the outbound stream — flipping
    them to completed/failed when the worker has emitted the matching
    terminal event. Lazy on-read so we don't need a background task.
    """
    eng = _engagement_by_slug(session, slug)

    _reconcile_running_runs(session, redis_client, eng.id)

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
    "/agent-executions/{execution_id}/retry",
    response_model=StatusEntity,
)
def retry_agent_execution(
    execution_id: uuid.UUID,
    session: DbSession,
    redis_client: RedisClient,
    user: CurrentNonGuestUser,
) -> StatusEntity:
    """Re-run a failed agent execution.

    v0.8 only wires Triage retry (the simplest dispatch — re-run on the
    same finding). Strategic / Tactical retry shipping in a follow-up:
    each agent kind needs its own dispatcher because the source entity
    (finding vs task) differs. Until then the Status tab's retryable
    flag is False for Strategic / Tactical failed rows so the UI
    doesn't promise a button that 501s.

    BYO key resolves against the *clicking* analyst's Redis cache
    (matches Strategic / Triage policy — preserves the v0.4 cross-user
    key-reuse lock).
    """
    row = session.get(AgentExecution, execution_id)
    if row is None:
        raise HTTPException(status_code=404, detail="agent execution not found")
    if row.status != AgentExecutionStatus.failed:
        raise HTTPException(
            status_code=400,
            detail="only failed agent executions can be retried",
        )
    if row.agent != AgentName.triage:
        raise HTTPException(
            status_code=501,
            detail=(
                f"retry for agent kind '{row.agent.value}' isn't wired yet; "
                "use the original action surface for now (Strategic: 'Agent' "
                "button on the finding slide-over)"
            ),
        )

    # Triage stashed the source finding id under input.finding_id. Look it up.
    input_payload = row.input or {}
    finding_id_raw = input_payload.get("finding_id") if isinstance(input_payload, dict) else None
    if not finding_id_raw:
        raise HTTPException(
            status_code=400,
            detail=(
                "this Triage execution has no finding_id in input — can't "
                "determine what to retry against"
            ),
        )
    try:
        finding_id = uuid.UUID(str(finding_id_raw))
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"corrupt finding_id on the execution row: {finding_id_raw!r}",
        ) from exc
    finding = session.get(Finding, finding_id)
    if finding is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"finding {finding_id} no longer exists — can't retry the "
                "triage against a deleted finding"
            ),
        )

    from app.services.ephemeral_provider_key import NoProviderKeyError
    from app.services.status_notifier import notify_status_event
    from app.services.triage import triage_finding_summary

    try:
        execution, _summary = triage_finding_summary(
            session,
            redis_client,
            finding=finding,
            acting_user_id=user.id,
        )
    except NoProviderKeyError as exc:
        session.rollback()
        raise HTTPException(
            status_code=400,
            detail=(
                f"No provider key configured for the LLM call ({exc}). "
                "Add one under /settings/keys."
            ),
        ) from exc
    except Exception as exc:
        session.commit()
        eng = session.get(Engagement, finding.engagement_id)
        notify_status_event(
            session,
            kind="agent",
            title=f"Triage retry failed: {finding.title}",
            status="failed",
            detail=str(exc)[:500],
            engagement_slug=eng.slug if eng else None,
        )
        raise HTTPException(
            status_code=502, detail=f"triage retry failed: {exc}"
        ) from exc

    session.commit()
    session.refresh(execution)
    return _agent_to_entity(execution)


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
