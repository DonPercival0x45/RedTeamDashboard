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
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents import TacticalAgent, TacticalAlreadyScanned
from app.api.deps import CurrentNonGuestUser, CurrentUser, DbSession, RedisClient
from app.models import (
    ActorType,
    AgentExecution,
    AgentExecutionStatus,
    AgentName,
    AgentTrigger,
    Approval,
    ApprovalStatus,
    AuditLog,
    Engagement,
    EngagementStatus,
    EngagementWorkState,
    MCPLease,
    MCPLeaseStatus,
    OwnerEligibility,
    Task,
    TaskKind,
    TaskStatus,
)
from app.runs.events import decode_envelope
from app.runs.streams import inbound_stream, outbound_stream
from app.schemas.status import (
    EngagementStatusResponse,
    StatusColor,
    StatusEntity,
    StatusOutcome,
    StatusTransition,
    StepEntry,
    StepLogResponse,
)

router = APIRouter()


def _engagement_by_slug(session: Session, slug: str) -> Engagement:
    eng = session.execute(select(Engagement).where(Engagement.slug == slug)).scalar_one_or_none()
    if eng is None:
        raise HTTPException(status_code=404, detail=f"engagement '{slug}' not found")
    return eng


def _ensure_mutable_engagement(session: Session, engagement_id: uuid.UUID) -> None:
    engagement = session.execute(
        select(Engagement)
        .where(Engagement.id == engagement_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if engagement is None or engagement.status == EngagementStatus.flushed:
        raise HTTPException(status_code=404, detail="engagement not found")
    if engagement.status == EngagementStatus.archived:
        raise HTTPException(status_code=409, detail="archived engagement is read-only")
    if engagement.work_state == EngagementWorkState.completed:
        raise HTTPException(status_code=409, detail="completed engagement is read-only")


def _lock_mutable_task(session: Session, task_id: uuid.UUID) -> Task:
    """Lock a mutable Task using the global Engagement → child order."""
    engagement_id = session.execute(
        select(Task.engagement_id).where(Task.id == task_id)
    ).scalar_one_or_none()
    if engagement_id is None:
        raise HTTPException(status_code=404, detail="task not found")

    _ensure_mutable_engagement(session, engagement_id)
    task = session.execute(
        select(Task)
        .where(Task.id == task_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return task


# ── v1.2.0: run_slug + outcome + synopsis derivation ────────────────────
#
# All three are derived from existing columns at read time — no schema
# migration. run_slug is a display-only handle (the URL still uses the
# full UUID). Outcome + synopsis fold together the terminal-state
# signals so the analyst sees "success/empty/partial/errored + one
# line of what happened" without needing to open the JSON payload.


def _run_slug(source: str | uuid.UUID) -> str:
    """rt-<4 hex> — first four hex chars of the identifier.

    64k display slugs. Collisions are visually harmless (the UI shows
    it, the URL uses the full ID). Keep this in sync with the frontend
    ``lib/runSlug.ts`` helper so kickoff toasts show the same slug the
    Status card will show once the row is visible.

    For agent/task entities the caller should pass the run's
    ``thread_id`` (string) when known — that way the kickoff toast (which
    fires off the ``run.started`` SSE payload) shows the same rt-XXXX
    the Status card will show when the reconciler flips it to
    completed/failed. Falls back to the entity's own UUID otherwise.
    """
    if isinstance(source, uuid.UUID):
        return f"rt-{source.hex[:4]}"
    # String path — strip dashes, take first 4 hex chars. Handles both
    # UUID strings and plain hex strings.
    hex_only = source.replace("-", "")
    return f"rt-{hex_only[:4].lower()}"


def _agent_outcome(row: AgentExecution) -> StatusOutcome | None:
    if row.status == AgentExecutionStatus.running:
        return None
    if row.status in (AgentExecutionStatus.failed, AgentExecutionStatus.cancelled) or row.error:
        return "errored"
    # completed. Look at the shape of ``output`` per-agent-kind for the
    # empty/partial/success split. Errors mid-run that still produced
    # some structured output land in ``partial``.
    output = row.output or {}
    partial_flag = bool(output.get("partial") or output.get("tool_errors"))
    findings_count = int(output.get("findings_count") or 0)
    tasks_count = int(output.get("tasks_count") or 0)
    tools_count = int(output.get("tools_count") or 0)
    if partial_flag:
        return "partial"
    if row.agent == AgentName.strategic and tasks_count == 0:
        return "empty"
    if row.agent == AgentName.tactical and findings_count == 0 and tools_count == 0:
        return "empty"
    if row.agent == AgentName.triage:
        # Triage always produces a summary; call it success unless the
        # output is literally empty.
        return "success" if output else "empty"
    return "success"


def _task_outcome(row: Task) -> StatusOutcome | None:
    if row.status in (
        TaskStatus.pending,
        TaskStatus.deferred,
        TaskStatus.dispatched,
        TaskStatus.running,
    ):
        return None
    if row.status == TaskStatus.cancelled:
        return None
    if row.status == TaskStatus.failed:
        return "errored"
    # completed — check if the payload's expected output landed.
    payload = row.payload or {}
    if payload.get("no_results"):
        return "empty"
    if payload.get("partial") or payload.get("tool_errors"):
        return "partial"
    return "success"


def _approval_outcome(row: Approval) -> StatusOutcome | None:
    if row.status == ApprovalStatus.pending:
        return None
    if row.status == ApprovalStatus.denied:
        return "errored"
    return "success"


def _agent_synopsis(row: AgentExecution, outcome: StatusOutcome | None) -> str:
    """One-line "here's what I tried / what happened / why I failed"."""
    if outcome == "errored":
        if row.status == AgentExecutionStatus.cancelled:
            return "Cancelled by user."
        err = (row.error or "unknown error")[:120]
        return f"Failed: {err}"
    output = row.output or {}
    agent = row.agent.value
    if outcome is None:
        return f"{agent.capitalize()} agent running…"
    if outcome == "empty":
        return f"{agent.capitalize()} agent completed — no output."
    findings = int(output.get("findings_count") or 0)
    tasks = int(output.get("tasks_count") or 0)
    tools = int(output.get("tools_count") or 0)
    if row.agent == AgentName.strategic:
        return f"Strategic proposed {tasks} task(s)."
    if row.agent == AgentName.tactical:
        parts = []
        if tools:
            parts.append(f"{tools} tool call(s)")
        if findings:
            parts.append(f"produced {findings} finding(s)")
        return "Tactical ran " + (", ".join(parts) if parts else "(no signal)") + "."
    if row.agent == AgentName.triage:
        return "Triage summarized finding."
    if row.agent == AgentName.planner:
        return output.get("summary") or "Planner evaluation complete."
    if row.agent == AgentName.tool_review:
        return "Tool review complete."
    return f"{agent.capitalize()} completed."


def _task_synopsis(row: Task, outcome: StatusOutcome | None) -> str:
    payload = row.payload or {}
    tool = payload.get("tool") or payload.get("tool_name") or "task"
    target = payload.get("target")
    tool_target = f"{tool} → {target}" if tool and target else tool
    if row.status == TaskStatus.deferred:
        retryable = row.kind in (TaskKind.scan, TaskKind.enum) and row.owner_eligibility in (
            OwnerEligibility.agent,
            OwnerEligibility.either,
        )
        action = "Retry when ready or cancel" if retryable else "Cancel"
        return f"Deferred: {tool_target}. {action} to resolve it."
    if row.status == TaskStatus.pending:
        return f"Awaiting dispatch: {tool_target}."
    if row.status == TaskStatus.cancelled:
        return f"Cancelled by analyst: {tool_target}."
    if outcome is None:
        return f"Running {tool_target}…"
    if outcome == "errored":
        return f"Failed: {tool_target}"
    if outcome == "empty":
        return f"{tool_target} completed — no results."
    if outcome == "partial":
        return f"{tool_target} completed with partial results."
    return f"{tool_target} completed."


def _approval_synopsis(row: Approval, outcome: StatusOutcome | None) -> str:
    tool = row.tool_name
    if outcome is None:
        return f"Awaiting approval for {tool} ({row.risk.value} risk)."
    if outcome == "errored":
        return f"Denied {tool}."
    return f"Approved {tool}."


# ── status colour mappers ────────────────────────────────────────────────


def _agent_color(s: AgentExecutionStatus) -> StatusColor:
    if s == AgentExecutionStatus.running:
        return "active"
    if s == AgentExecutionStatus.completed:
        return "completed"
    return "failed"  # failed or cancelled


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
    agent_label = row.agent.value.replace("_", " ").title()
    color = _agent_color(row.status)
    outcome = _agent_outcome(row)
    thread_id = (row.input or {}).get("thread_id") if isinstance(row.input, dict) else None
    slug_source: str | uuid.UUID = thread_id if isinstance(thread_id, str) and thread_id else row.id
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
        run_slug=_run_slug(slug_source),
        outcome=outcome,
        synopsis=_agent_synopsis(row, outcome),
        # Triage retry re-runs the source finding; Tactical retry re-dispatches
        # the run's source task (both via POST /agent-executions/{id}/retry).
        # Strategic / Planner aren't wired here (Planner has its own re-evaluate
        # button on /settings/feedback). Tactical is optimistic — the endpoint
        # 400s cleanly if the run has no retryable source task.
        retryable=(
            row.status == AgentExecutionStatus.failed
            and row.agent in (AgentName.triage, AgentName.tactical)
        ),
        log={
            "agent": row.agent.value,
            "trigger": row.trigger.value,
            "model_provider": row.model_provider,
            "model_name": row.model_name,
            "tokens_in": row.tokens_in,
            "tokens_out": row.tokens_out,
            "cost_usd": (str(row.cost_usd) if row.cost_usd is not None else None),
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
    outcome = _task_outcome(row)
    task_slug_source: str | uuid.UUID = str(row.run_id) if row.run_id else row.id
    return StatusEntity(
        id=row.id,
        kind="task",
        title=row.title,
        subtitle=(f"{tool} → {target}" if tool and target else tool or target),
        color=color,
        raw_status=row.status.value,
        started_at=row.dispatched_at,
        completed_at=row.completed_at,
        retryable=(
            row.status in (TaskStatus.failed, TaskStatus.deferred)
            and row.kind in (TaskKind.scan, TaskKind.enum)
            and row.owner_eligibility in (OwnerEligibility.agent, OwnerEligibility.either)
        ),
        finding_id=row.finding_id,
        work_item_id=row.work_item_id,
        task_id=row.id,
        run_slug=_run_slug(task_slug_source),
        outcome=outcome,
        synopsis=_task_synopsis(row, outcome),
        log={
            "kind": row.kind.value,
            "owner_eligibility": row.owner_eligibility.value,
            "finding_id": str(row.finding_id) if row.finding_id else None,
            "work_item_id": str(row.work_item_id) if row.work_item_id else None,
            "run_id": str(row.run_id) if row.run_id else None,
            "dispatched_at": (row.dispatched_at.isoformat() if row.dispatched_at else None),
            "payload": payload,
        },
        history=_task_history(row),
    )


def _approval_to_entity(row: Approval) -> StatusEntity:
    color = _approval_color(row.status)
    outcome = _approval_outcome(row)
    approval_slug_source: str | uuid.UUID = row.thread_id if row.thread_id else row.id
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
        run_slug=_run_slug(approval_slug_source),
        outcome=outcome,
        synopsis=_approval_synopsis(row, outcome),
        log={
            "thread_id": row.thread_id,
            "node": row.node,
            "tool_name": row.tool_name,
            "tool_args": row.tool_args,
            "risk": row.risk.value,
            "scope_check": row.scope_check,
            "decision_args": row.decision_args,
            "authorization_id": (str(row.authorization_id) if row.authorization_id else None),
        },
        history=_approval_history(row),
    )


# ── read endpoint ────────────────────────────────────────────────────────


# v0.8.3: hard timeout for dispatched/running Tasks the worker never
# completed. Real OSINT runs almost always finish in well under this.
# Any Task still in dispatched state past this window gets cancelled
# on the next Status read.
_STALE_TASK_TIMEOUT = timedelta(minutes=30)


def _reconcile_stale_tasks(
    session: Session,
    eng_id: Any,
) -> None:
    """v0.8.3: cancel Tasks that have been dispatched > 30 minutes.

    These are rows whose worker message was lost (consumer rename on
    worker restart, Redis hiccup) or where the worker processed them
    but failed to update the DB. Without this sweep they sit in
    ``dispatched`` state forever — the four 4-day-old "ACTIVE" boxes
    on the 5qprod tenant that this fix was born from.

    Conservatism: only sweeps when the task has gone past the timeout
    AND has no completed_at. Tasks in ``pending`` are left alone
    (analyst hasn't accepted them yet).
    """
    cutoff = datetime.now(tz=UTC) - _STALE_TASK_TIMEOUT
    stale = list(
        session.execute(
            select(Task).where(
                Task.engagement_id == eng_id,
                Task.status.in_((TaskStatus.dispatched, TaskStatus.running)),
                Task.completed_at.is_(None),
                Task.dispatched_at.isnot(None),
                Task.dispatched_at < cutoff,
            )
        ).scalars()
    )
    if not stale:
        return
    now = datetime.now(tz=UTC)
    for row in stale:
        row.status = TaskStatus.cancelled
        row.completed_at = now
    session.commit()


def _reconcile_running_tasks_from_stream(
    session: Session,
    redis_client: Any,
    eng_id: Any,
    *,
    engagement_slug: str | None = None,
) -> None:
    """v0.8.3: same shape as the AgentExecution reconcile — but for Tasks.

    Tasks carry a ``run_id``. Scan the engagement's outbound stream for
    ``run.completed`` / ``run.errored`` events; flip any matching Task
    rows that are still ``dispatched`` or ``running`` to
    completed / failed accordingly. Lazy (on Status read), bounded by
    the last 500 events on the stream.
    """
    pending = list(
        session.execute(
            select(Task).where(
                Task.engagement_id == eng_id,
                Task.status.in_((TaskStatus.dispatched, TaskStatus.running)),
                Task.run_id.isnot(None),
            )
        ).scalars()
    )
    if not pending:
        return

    try:
        raw = redis_client.xrange(outbound_stream(eng_id), count=500)
    except Exception:  # noqa: BLE001
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
        terminal[thread_id] = (
            event_type,
            str(payload.get("error") or "") if event_type == "run.errored" else None,
        )

    if not terminal:
        return

    now = datetime.now(tz=UTC)
    dirty = False
    # v0.9.1: collect transitions first so we can fire Discord pings AFTER
    # the row is committed (avoids "row says still running" race if the
    # webhook reads back via /integrations during the post-commit window).
    transitions: list[tuple[Task, str, str | None]] = []
    for row in pending:
        rid = str(row.run_id) if row.run_id else ""
        if rid and rid in terminal:
            event_type, err = terminal[rid]
            row.status = (
                TaskStatus.completed if event_type == "run.completed" else TaskStatus.failed
            )
            row.completed_at = now
            dirty = True
            transitions.append((row, event_type, err))
    if dirty:
        session.commit()
        from app.services.status_notifier import notify_status_event

        for row, event_type, err in transitions:
            is_failed = event_type == "run.errored"
            notify_status_event(
                session,
                kind="task",
                title=f"Task {'failed' if is_failed else 'completed'}: {row.title}",
                status="failed" if is_failed else "completed",
                detail=(err or "")[:500] if is_failed else None,
                engagement_slug=engagement_slug,
            )


def _reconcile_running_runs(
    session: Session,
    redis_client: Any,
    eng_id: Any,
    *,
    engagement_slug: str | None = None,
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
    # v0.9.1: collect transitions to fire Discord pings AFTER commit.
    transitions: list[tuple[AgentExecution, str, str | None]] = []
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
            transitions.append((row, event_type, error))
    if dirty:
        session.commit()
        # v0.9.1: fire Discord status-alert ping for every transition.
        # The status_notifier looks up enabled integrations with
        # purpose='status_alerts' and posts to each. Silent no-op if no
        # such integration is configured. This is the "run-level
        # completions" hook the v0.8.0 brief promised.
        from app.services.status_notifier import notify_status_event

        for row, event_type, err in transitions:
            is_failed = event_type == "run.errored"
            thread_short = str((row.input or {}).get("thread_id") or "")[:8]
            notify_status_event(
                session,
                kind="run",
                title=(f"Run {'failed' if is_failed else 'completed'} (thread {thread_short})"),
                status="failed" if is_failed else "completed",
                detail=(err or "")[:500] if is_failed else None,
                engagement_slug=engagement_slug,
            )


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

    # v0.8.3: three lazy reconciles before the read. Order matters
    # marginally: the stream-match passes run first because the
    # stale-task sweep is the catch-all for the worker-lost-message
    # case where there's no stream event to match against.
    # v0.9.1: the two stream-match passes also fire Discord status-alert
    # pings on each transition (purpose='status_alerts' integration row).
    _reconcile_running_runs(session, redis_client, eng.id, engagement_slug=eng.slug)
    _reconcile_running_tasks_from_stream(session, redis_client, eng.id, engagement_slug=eng.slug)
    _reconcile_stale_tasks(session, eng.id)

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


# ── v1.2.0: tenant-global runs (no engagement scope) ────────────────────
#
# Planner rank / combine / re-evaluate produce AgentExecution rows with
# ``engagement_id == NULL``. Same for admin roadmap ops. The new
# ``/settings/agent-runs`` page needs a way to list those — mirrors the
# engagement Status feed but without engagement scope.


@router.get(
    "/agent-runs",
    response_model=EngagementStatusResponse,
)
def list_global_agent_runs(
    session: DbSession,
    _user: CurrentUser,
) -> EngagementStatusResponse:
    """Tenant-global AgentExecution rows. No tasks / approvals — those
    are always engagement-scoped."""
    agents = list(
        session.execute(
            select(AgentExecution)
            .where(AgentExecution.engagement_id.is_(None))
            .order_by(AgentExecution.started_at.desc())
            .limit(200)
        ).scalars()
    )
    return EngagementStatusResponse(
        agents=[_agent_to_entity(a) for a in agents],
        tasks=[],
        approvals=[],
    )


# v1.2.0: step-log endpoint for tenant-global agents. Reuses
# ``_steps_for_entity`` with ``eng`` set to a stub so the audit query
# runs across all engagements — planner-ish rows won't have an
# engagement_id on their audit rows anyway.


@router.get(
    "/agent-runs/{execution_id}/steps",
    response_model=StepLogResponse,
)
def get_global_agent_execution_steps(
    execution_id: uuid.UUID,
    session: DbSession,
    _user: CurrentUser,
) -> StepLogResponse:
    """Step log for a tenant-global (no engagement scope) execution."""
    from app.models import AuditLog

    row = session.get(AgentExecution, execution_id)
    if row is None or row.engagement_id is not None:
        raise HTTPException(
            status_code=404,
            detail="tenant-global agent execution not found",
        )
    entity_id_str = str(execution_id)
    audit_rows = list(
        session.execute(
            select(AuditLog)
            .where(AuditLog.engagement_id.is_(None))
            .order_by(AuditLog.created_at.desc())
            .limit(1000)
        ).scalars()
    )
    steps = [
        _audit_step_entry(r)
        for r in audit_rows
        if (r.payload or {}).get("execution_id") == entity_id_str
        or (r.payload or {}).get("id") == entity_id_str
    ]
    steps.sort(key=lambda s: s.at)
    truncated = len(steps) > _MAX_STEPS_PER_ENTITY
    if truncated:
        steps = steps[-_MAX_STEPS_PER_ENTITY:]
    return StepLogResponse(steps=steps, truncated=truncated)


# ── v1.2.0: step-log endpoint ───────────────────────────────────────────
#
# Analyst clicks "Expand" on a Status card; the modal fetches this to
# render a step-by-step trace of what the entity did. Merges two
# sources:
#
#   1. ``audit_log`` rows for this engagement whose payload references
#      the entity (execution_id / task_id / approval_id / thread_id).
#      This is the durable trace — good for reconstructing runs long
#      after they finish.
#   2. The Redis outbound stream ``runs:{eng_id}:events`` filtered to
#      the entity's thread_id. This adds live SSE-only events (tool
#      calls, findings, approvals) that don't leave an audit row.
#
# Results are deduped by (kind, at) and ordered newest last so the
# frontend can render top-down.


_MAX_STEPS_PER_ENTITY = 200


def _relevant_audit_rows(
    session: Session,
    eng_id: uuid.UUID,
    *,
    thread_id: str | None,
    entity_kind: str,
    entity_id: uuid.UUID,
) -> list[Any]:
    """Read audit rows scoped to this engagement whose payload references
    the entity. We over-fetch (payload JSONB matches are cheap in
    Postgres via ``->>``) and filter in Python for shape robustness."""
    from app.models import AuditLog

    rows = list(
        session.execute(
            select(AuditLog)
            .where(AuditLog.engagement_id == eng_id)
            .order_by(AuditLog.created_at.desc())
            .limit(1000)
        ).scalars()
    )
    entity_id_str = str(entity_id)
    kept: list[Any] = []
    for r in rows:
        p = r.payload or {}
        # Match by any of the fields that could reference this entity.
        # Match against a broad set of key names so we don't miss step
        # events emitted by different code paths.
        hit = (
            p.get("execution_id") == entity_id_str
            or p.get("task_id") == entity_id_str
            or p.get("approval_id") == entity_id_str
            or p.get("id") == entity_id_str
            or (thread_id and p.get("thread_id") == thread_id)
        )
        if hit:
            kept.append(r)
    return kept


def _stream_step_events(
    redis_client: Any,
    eng_id: uuid.UUID,
    *,
    thread_id: str,
) -> list[StepEntry]:
    """Read the outbound stream tail and pick out step events for the
    given thread_id. Bounded to the last 500 events (~ enough for any
    single-run trace)."""
    try:
        raw = redis_client.xrange(outbound_stream(eng_id), count=500)
    except Exception:  # noqa: BLE001 — Redis hiccup must not break step read
        return []
    steps: list[StepEntry] = []
    for msg_id, fields in raw or []:
        try:
            payload = decode_envelope(fields)
        except (ValueError, KeyError):
            continue
        if payload.get("thread_id") != thread_id:
            continue
        etype = payload.get("type") or ""
        # Redis stream ids look like ``1234567890123-0``; parse epoch ms.
        try:
            epoch_ms = int(str(msg_id).split("-", 1)[0])
        except (ValueError, TypeError):
            continue
        at = datetime.fromtimestamp(epoch_ms / 1000, tz=UTC)
        label = _summarize_stream_event(payload)
        steps.append(
            StepEntry(
                at=at,
                kind=etype,
                label=label,
                detail={k: v for k, v in payload.items() if k not in ("type", "thread_id")},
            )
        )
    return steps


def _summarize_stream_event(payload: dict[str, Any]) -> str:
    """Plain-language one-liner for a single stream event."""
    t = payload.get("type") or ""
    if t == "run.started":
        return f"Run started: {(payload.get('prompt') or '')[:120]}"
    if t == "approval.pending":
        return f"Approval pending: {payload.get('tool')} ({payload.get('risk')})"
    if t == "tool.denied":
        return f"Tool denied: {payload.get('tool')} — {payload.get('reason')}"
    if t == "tool.auto_approved":
        return f"Auto-approved: {payload.get('tool')}"
    if t == "finding.created":
        title = payload.get("title") or payload.get("tool") or "finding"
        return f"Finding: {title}"
    if t == "run.completed":
        return "Run completed."
    if t == "run.errored":
        return f"Run errored: {(payload.get('error') or '')[:120]}"
    if t == "llm.responded":
        # v1.4.3: LLM invocation trace. If tool_call_count > 0, note it.
        # Else surface a preview of what the model said — critical for
        # diagnosing "run completed with 0 findings" cases where the
        # agent responded with text instead of calling a tool.
        tcc = payload.get("tool_call_count") or 0
        tokens = f"in={payload.get('tokens_in')} out={payload.get('tokens_out')}"
        if tcc:
            call_names = ", ".join(c.get("name", "?") for c in (payload.get("tool_calls") or []))
            return f"LLM → {tcc} tool call(s): {call_names} [{tokens}]"
        preview = (payload.get("content_preview") or "").replace("\n", " ")[:120]
        return f"LLM → no tool calls [{tokens}] — {preview!r}"
    if t == "tool.executed":
        ok = "ok" if payload.get("ok") else "failed"
        return (
            f"Tool ran: {payload.get('tool')}("
            f"{_short_args(payload.get('args'))}) — {ok}"
            f" · {payload.get('findings_emitted') or 0} finding(s)"
            f" · {payload.get('elapsed_ms') or 0}ms"
            + (f" · error: {(payload.get('error') or '')[:80]}" if not payload.get("ok") else "")
        )
    return t or "event"


def _short_args(args: Any) -> str:
    """One-line render of tool args for the step log summary."""
    if not isinstance(args, Mapping):
        return ""
    parts: list[str] = []
    for k, v in args.items():
        sv = str(v)
        if len(sv) > 60:
            sv = sv[:57] + "…"
        parts.append(f"{k}={sv}")
    return ", ".join(parts)


def _audit_step_entry(row: Any) -> StepEntry:
    p = row.payload or {}
    kind = row.event_type or "audit"
    # Prefer a "friendly" label field if the emitter set one; otherwise
    # synthesize from the event_type + a short payload preview.
    label = str(p.get("label") or p.get("message") or "")
    if not label:
        # Trim payload to essentials for a readable one-liner.
        parts = []
        for k in ("tool", "target", "outcome", "status", "decision"):
            v = p.get(k)
            if v is not None:
                parts.append(f"{k}={v}")
        label = f"{kind}" + (f" — {' · '.join(parts)}" if parts else "")
    return StepEntry(
        at=row.created_at,
        kind=kind,
        label=label,
        detail={k: v for k, v in p.items() if k != "label"},
    )


def _steps_for_entity(
    session: Session,
    redis_client: Any,
    eng: Engagement,
    *,
    kind: str,
    entity_id: uuid.UUID,
    thread_id: str | None,
) -> StepLogResponse:
    audit_steps = [
        _audit_step_entry(r)
        for r in _relevant_audit_rows(
            session,
            eng.id,
            thread_id=thread_id,
            entity_kind=kind,
            entity_id=entity_id,
        )
    ]
    stream_steps: list[StepEntry] = []
    if thread_id:
        stream_steps = _stream_step_events(redis_client, eng.id, thread_id=thread_id)
    # Merge + dedupe by (kind, iso timestamp) — audit rows land within
    # ~1s of the stream event they mirror, so dedupe on second precision.
    seen: set[tuple[str, str]] = set()
    merged: list[StepEntry] = []
    for s in [*audit_steps, *stream_steps]:
        key = (s.kind, s.at.replace(microsecond=0).isoformat())
        if key in seen:
            continue
        seen.add(key)
        merged.append(s)
    merged.sort(key=lambda s: s.at)
    truncated = len(merged) > _MAX_STEPS_PER_ENTITY
    if truncated:
        merged = merged[-_MAX_STEPS_PER_ENTITY:]
    return StepLogResponse(steps=merged, truncated=truncated)


@router.get(
    "/engagements/{slug}/status/agents/{execution_id}/steps",
    response_model=StepLogResponse,
)
def get_agent_execution_steps(
    slug: str,
    execution_id: uuid.UUID,
    session: DbSession,
    redis_client: RedisClient,
    _user: CurrentUser,
) -> StepLogResponse:
    """Step log for one AgentExecution row on this engagement."""
    eng = _engagement_by_slug(session, slug)
    row = session.get(AgentExecution, execution_id)
    if row is None or row.engagement_id != eng.id:
        raise HTTPException(status_code=404, detail="agent execution not found")
    thread_id = None
    if isinstance(row.input, dict):
        raw_tid = row.input.get("thread_id")
        if isinstance(raw_tid, str) and raw_tid:
            thread_id = raw_tid
    return _steps_for_entity(
        session,
        redis_client,
        eng,
        kind="agent",
        entity_id=execution_id,
        thread_id=thread_id,
    )


@router.get(
    "/engagements/{slug}/status/tasks/{task_id}/steps",
    response_model=StepLogResponse,
)
def get_task_steps(
    slug: str,
    task_id: uuid.UUID,
    session: DbSession,
    redis_client: RedisClient,
    _user: CurrentUser,
) -> StepLogResponse:
    """Step log for one Task on this engagement. Streams events by
    ``run_id`` (which is the same value as ``thread_id`` for tasks)."""
    eng = _engagement_by_slug(session, slug)
    row = session.get(Task, task_id)
    if row is None or row.engagement_id != eng.id:
        raise HTTPException(status_code=404, detail="task not found")
    thread_id = str(row.run_id) if row.run_id else None
    return _steps_for_entity(
        session,
        redis_client,
        eng,
        kind="task",
        entity_id=task_id,
        thread_id=thread_id,
    )


@router.get(
    "/engagements/{slug}/status/approvals/{approval_id}/steps",
    response_model=StepLogResponse,
)
def get_approval_steps(
    slug: str,
    approval_id: uuid.UUID,
    session: DbSession,
    redis_client: RedisClient,
    _user: CurrentUser,
) -> StepLogResponse:
    """Step log for one Approval on this engagement."""
    eng = _engagement_by_slug(session, slug)
    row = session.get(Approval, approval_id)
    if row is None or row.engagement_id != eng.id:
        raise HTTPException(status_code=404, detail="approval not found")
    return _steps_for_entity(
        session,
        redis_client,
        eng,
        kind="approval",
        entity_id=approval_id,
        thread_id=row.thread_id or None,
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

    Triage: re-run on the same source finding. Tactical: a run dispatched from
    a task is retried by re-dispatching its source task (which re-derives the
    prompt — the run's own prompt isn't durably stored). Other agents
    (Strategic, …) are not wired and return 501.

    BYO key resolves against the *clicking* analyst's Redis cache
    (matches Strategic / Triage policy — preserves the v0.4 cross-user
    key-reuse lock).
    """
    row = session.get(AgentExecution, execution_id)
    if row is None:
        raise HTTPException(status_code=404, detail="agent execution not found")
    if row.engagement_id is not None:
        _ensure_mutable_engagement(session, row.engagement_id)
    if row.status != AgentExecutionStatus.failed:
        raise HTTPException(
            status_code=400,
            detail="only failed agent executions can be retried",
        )
    if row.agent == AgentName.tactical:
        # A Tactical run dispatched from a task: re-dispatch the source task
        # (TacticalAgent.dispatch re-derives the prompt; the run's own prompt
        # isn't durably stored, so we can't rebuild the run directly).
        task = session.execute(select(Task).where(Task.run_id == execution_id)).scalar_one_or_none()
        if task is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    "this run wasn't dispatched from a task, so it can't be "
                    "retried from here — re-run it from the engagement's Runs panel"
                ),
            )
        task = _redispatch_task(session, redis_client, task, user=user)
        return _task_to_entity(task)

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
    from app.services.findings import lock_active_finding_or_404

    finding = lock_active_finding_or_404(session, finding_id)

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
        raise HTTPException(status_code=502, detail=f"triage retry failed: {exc}") from exc

    session.commit()
    session.refresh(execution)
    return _agent_to_entity(execution)


def _remove_queued_thread_start(
    redis_client: Any,
    engagement_id: uuid.UUID,
    thread_id: str,
) -> int:
    """Best-effort removal of a queued run.start command for a thread.

    If the worker already consumed the stream entry, XDEL returns 0; callers
    still mark the DB row cancelled so the UI stops presenting it as active.
    """
    stream = inbound_stream(engagement_id)
    removed = 0
    try:
        rows = redis_client.xrange(stream, min="-", max="+", count=500)
    except Exception:
        return 0
    for entry_id, fields in rows:
        if not isinstance(fields, dict):
            continue
        try:
            payload = decode_envelope(fields)
        except Exception:
            continue
        if payload.get("type") == "run.start" and str(payload.get("thread_id")) == thread_id:
            removed += int(redis_client.xdel(stream, entry_id) or 0)
    redis_client.delete(f"run:model:{thread_id}")
    return removed


def _remove_queued_run_start(redis_client: Any, task: Task) -> int:
    """Best-effort removal of a queued run.start command for this task."""
    if task.run_id is None:
        return 0
    return _remove_queued_thread_start(
        redis_client,
        task.engagement_id,
        str(task.run_id),
    )


@router.post(
    "/agent-executions/{execution_id}/cancel",
    response_model=StatusEntity,
)
def cancel_agent_execution(
    execution_id: uuid.UUID,
    session: DbSession,
    redis_client: RedisClient,
    user: CurrentNonGuestUser,
) -> StatusEntity:
    """Cancel a running AgentExecution row.

    For stream-backed executions with ``input.thread_id`` and an engagement,
    this also removes a queued run.start command if it has not been consumed
    yet. For synchronous LLM calls already in flight, cancellation is
    cooperative/best-effort: the DB row is marked cancelled immediately so the
    app no longer shows it as active.
    """
    row = session.get(AgentExecution, execution_id)
    if row is None:
        raise HTTPException(status_code=404, detail="agent execution not found")
    if row.status != AgentExecutionStatus.running:
        raise HTTPException(
            status_code=400,
            detail="only running agent executions can be cancelled",
        )

    input_payload = row.input or {}
    thread_id = input_payload.get("thread_id") if isinstance(input_payload, dict) else None
    removed = 0
    if row.engagement_id is not None and thread_id:
        removed = _remove_queued_thread_start(
            redis_client,
            row.engagement_id,
            str(thread_id),
        )

    row.status = AgentExecutionStatus.cancelled
    row.completed_at = datetime.now(tz=UTC)
    row.error = "Cancelled by user"
    session.add(
        AuditLog(
            engagement_id=row.engagement_id,
            actor_type=ActorType.user,
            actor_id=str(user.id),
            event_type="agent_execution.cancelled",
            payload={
                "execution_id": str(row.id),
                "agent": row.agent.value,
                "thread_id": str(thread_id) if thread_id else None,
                "queued_commands_removed": removed,
            },
        )
    )
    session.commit()
    session.refresh(row)
    return _agent_to_entity(row)


def _release_task_leases(session: Session, task: Task) -> int:
    leases = list(
        session.execute(
            select(MCPLease).where(
                MCPLease.task_id == task.id,
                MCPLease.status == MCPLeaseStatus.active.value,
            )
        ).scalars()
    )
    now = datetime.now(tz=UTC)
    for lease in leases:
        lease.status = MCPLeaseStatus.released.value
        lease.released_at = now
    return len(leases)


@router.post(
    "/tasks/{task_id}/cancel",
    response_model=StatusEntity,
)
def cancel_task(
    task_id: uuid.UUID,
    session: DbSession,
    redis_client: RedisClient,
    user: CurrentNonGuestUser,
) -> StatusEntity:
    """Cancel a pending/deferred/dispatched/running task and queued command.

    This is best-effort for work already consumed by the worker, but it always
    marks the task cancelled, releases active MCP leases, and removes any queued
    run.start envelope that has not yet been consumed.
    """
    row = _lock_mutable_task(session, task_id)
    if row.status not in (
        TaskStatus.pending,
        TaskStatus.deferred,
        TaskStatus.dispatched,
        TaskStatus.running,
    ):
        raise HTTPException(
            status_code=400,
            detail="only pending, deferred, dispatched, or running tasks can be cancelled",
        )

    previous_status = row.status
    removed = _remove_queued_run_start(redis_client, row)
    released = _release_task_leases(session, row)
    row.status = TaskStatus.cancelled
    row.completed_at = datetime.now(tz=UTC)
    session.add(
        AuditLog(
            engagement_id=row.engagement_id,
            actor_type=ActorType.user,
            actor_id=str(user.id),
            event_type="task.cancelled",
            payload={
                "task_id": str(row.id),
                "previous_status": previous_status.value,
                "run_id": str(row.run_id) if row.run_id else None,
                "queued_commands_removed": removed,
                "leases_released": released,
            },
        )
    )
    session.commit()
    session.refresh(row)
    return _task_to_entity(row)


def _redispatch_task(
    session: DbSession,
    redis_client: RedisClient,
    task: Task,
    *,
    user: CurrentNonGuestUser,
) -> Task:
    """Re-dispatch a failed/deferred agent-eligible task.

    Shared by ``POST /tasks/{id}/retry`` and Tactical run-retry
    (``POST /agent-executions/{id}/retry`` for a run that came from a task).
    The caller locks the task; this validates it is retryable, cleans up the
    prior queued run.start + leases, resets to ``pending``, and hands off to
    ``TacticalAgent.dispatch`` (which re-derives the prompt from the task —
    the run's own prompt isn't stored durably). Raises HTTPException on any
    failure, restoring the prior terminal state so retry can't leave phantom
    work.
    """
    if task.status not in (TaskStatus.failed, TaskStatus.deferred):
        raise HTTPException(
            status_code=400,
            detail="only failed or deferred tasks can be retried",
        )
    if task.kind not in (TaskKind.scan, TaskKind.enum) or task.owner_eligibility not in (
        OwnerEligibility.agent,
        OwnerEligibility.either,
    ):
        raise HTTPException(
            status_code=400,
            detail="only agent-eligible enumeration or scan tasks can be retried",
        )

    previous_status = task.status
    previous_run_id = task.run_id
    previous_dispatched_at = task.dispatched_at
    previous_completed_at = task.completed_at
    removed = _remove_queued_run_start(redis_client, task)
    released = _release_task_leases(session, task)
    # Leave prior run timestamps/ID intact until Tactical atomically swaps the
    # pending row to dispatched. If cancellation wins during policy selection,
    # it retains the old lineage needed for cleanup and audit.
    task.status = TaskStatus.pending
    try:
        TacticalAgent(redis_client).dispatch(
            session,
            task=task,
            acting_user_id=user.id,
            trigger=AgentTrigger.manual,
        )
    except TacticalAlreadyScanned as dedup:
        # A completed run already covered this (tool, target) within the dedup
        # window — the retry is moot. Mark the task done against the prior run
        # instead of re-dispatching (the duplicate-runs guardrail).
        task.status = TaskStatus.completed
        task.completed_at = datetime.now(tz=UTC)
        task.run_id = dedup.prior_execution_id
    except Exception as exc:
        # Tactical commits the new lease/task state before Redis XADD to avoid
        # a worker-vs-DB race. If enqueue then fails, restore the prior terminal
        # state and release the new lease so retry cannot leave phantom work.
        session.rollback()
        session.expire_all()
        failed_row = session.get(Task, task.id)
        if failed_row is None:
            raise HTTPException(status_code=404, detail="task not found") from exc
        failed_run_id = failed_row.run_id
        if failed_row.status == TaskStatus.cancelled:
            session.add(
                AuditLog(
                    engagement_id=failed_row.engagement_id,
                    actor_type=ActorType.user,
                    actor_id=str(user.id),
                    event_type="task.retry_superseded",
                    payload={
                        "task_id": str(failed_row.id),
                        "previous_status": previous_status.value,
                        "winning_status": TaskStatus.cancelled.value,
                    },
                )
            )
            session.commit()
            raise HTTPException(
                status_code=409,
                detail="task was cancelled while retry dispatch was preparing",
            ) from exc
        if failed_run_id is not None and failed_run_id != previous_run_id:
            _remove_queued_run_start(redis_client, failed_row)
            _release_task_leases(session, failed_row)
        failed_row.status = previous_status
        failed_row.run_id = previous_run_id
        failed_row.dispatched_at = previous_dispatched_at
        failed_row.completed_at = previous_completed_at
        session.add(
            AuditLog(
                engagement_id=failed_row.engagement_id,
                actor_type=ActorType.user,
                actor_id=str(user.id),
                event_type="task.retry_failed",
                payload={
                    "task_id": str(failed_row.id),
                    "previous_status": previous_status.value,
                    "failed_run_id": str(failed_run_id) if failed_run_id else None,
                    "error": str(exc)[:500],
                },
            )
        )
        session.commit()
        status_code = 400 if isinstance(exc, ValueError) else 502
        raise HTTPException(
            status_code=status_code,
            detail=f"task retry dispatch failed: {exc}",
        ) from exc

    session.add(
        AuditLog(
            engagement_id=task.engagement_id,
            actor_type=ActorType.user,
            actor_id=str(user.id),
            event_type="task.retried",
            payload={
                "task_id": str(task.id),
                "previous_status": previous_status.value,
                "run_id": str(task.run_id) if task.run_id else None,
                "old_queued_commands_removed": removed,
                "old_leases_released": released,
            },
        )
    )
    session.commit()
    session.refresh(task)
    return task


@router.post(
    "/tasks/{task_id}/retry",
    response_model=StatusEntity,
)
def retry_task(
    task_id: uuid.UUID,
    session: DbSession,
    redis_client: RedisClient,
    user: CurrentNonGuestUser,
) -> StatusEntity:
    """Re-dispatch a failed or deferred agent-eligible task.

    Retry crosses the same Tactical service boundary as first dispatch, so
    scope/approval gating and the analyst's provider/model selection still
    apply. Merely resetting to pending is insufficient: there is no background
    consumer that automatically discovers pending Task rows.
    """
    row = _lock_mutable_task(session, task_id)
    row = _redispatch_task(session, redis_client, row, user=user)
    return _task_to_entity(row)


# Discord webhook notifier hooks this to learn whether an entity has
# reached a terminal state (worth pinging the channel about).
def is_terminal_color(color: StatusColor) -> bool:
    return color in ("completed", "failed")


__all__ = ["router", "is_terminal_color"]
