"""Playbook runner — Track A step A3a + A3c.

Three public entry points now that A3c has cut the async seam:

* ``enqueue_run(engagement, playbook, scope_subset)`` — create a
  ``PlaybookRun`` in ``pending`` status. The HTTP endpoint returns 202 with
  this row; a background worker picks it up.
* ``execute_pending_run(session, run_id, executor)`` — grab a pending row
  under a ``SELECT ... FOR UPDATE SKIP LOCKED`` transition to ``running``,
  drive every step, transition to a terminal status. This is what the
  worker calls. Also honors mid-run ``cancel_run`` requests.
* ``start_run(...)`` — sync convenience (enqueue → execute in one call).
  Kept for tests + local dev + code paths that don't want the queue.

The worker-facing shape (``execute_pending_run``) uses row-level locking so
multiple worker replicas cooperate: whichever holds the pending row's lock
executes it, the others try the next one.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

import structlog
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.engagement import milestones as ms
from app.models import (
    ActorType,
    AuditLog,
    CoverageNodeTier,
    CoverageRecordStatus,
    Engagement,
    EngagementArchitecture,
    EngagementStatus,
    EngagementWorkState,
    Playbook,
    PlaybookExecutorKind,
    PlaybookRun,
    PlaybookRunStatus,
    PlaybookStep,
    PlaybookStepExecution,
    PlaybookStepExecutionStatus,
)
from app.runs.streams import outbound_stream
from app.services import coverage as cov
from app.services import methodology as meth
from app.services.command_outbox import enqueue_event
from app.services.playbook.executor import (
    InternalExecutor,
    MCPExecutor,
    PlaybookExecutor,
    RoutedExecutor,
    StepResult,
    executor_for_tool_slug,
    resolve_step_args,
)
from app.services.playbook.policy import MAX_PLAYBOOK_CALLS

logger = structlog.get_logger(__name__)


class _RunCancelledBeforeStep(Exception):
    """Cancellation won the race immediately before executor dispatch."""


TERMINAL_STATUSES = frozenset(
    {
        PlaybookRunStatus.completed,
        PlaybookRunStatus.partial,
        PlaybookRunStatus.failed,
        PlaybookRunStatus.cancelled,
    }
)


def _now(now: datetime | None) -> datetime:
    return now if now is not None else datetime.now(tz=UTC)


def _final_status(succeeded: int, failed: int) -> PlaybookRunStatus:
    """Aggregate step outcomes into a run-level status.

    * All-good → ``completed``.
    * Mixed → ``partial`` (baseline coverage still gets the successes).
    * All bad → ``failed``.
    * Zero-step playbook → ``completed`` (a well-formed playbook with no
      steps is a no-op, not a failure).
    """
    if succeeded == 0 and failed > 0:
        return PlaybookRunStatus.failed
    if failed > 0:
        return PlaybookRunStatus.partial
    return PlaybookRunStatus.completed


# ---------------------------------------------------------------------------
# Enqueue — the request-side entry point (A3c)
# ---------------------------------------------------------------------------


def enqueue_run(
    session: Session,
    *,
    engagement: Engagement,
    playbook: Playbook,
    scope_subset: Sequence[str],
    executor_kind: PlaybookExecutorKind = PlaybookExecutorKind.internal,
    requested_by: uuid.UUID | None = None,
    plan_snapshot: dict[str, Any] | None = None,
    plan_sha256: str | None = None,
    now: datetime | None = None,
) -> PlaybookRun:
    """Create a playbook run. Worker picks it up (or waits for approval).

    ``steps_total`` is pre-populated so a client polling the row can see
    "N of M steps done" progress the moment the worker starts. Caller
    commits. ``executor_kind`` decides which executor the worker will
    build when it claims this run (A4).

    **A5 gate**: if ``playbook.active`` is ``True`` the run starts in
    ``awaiting_approval`` status instead of ``pending`` — the worker
    claims only ``pending`` rows, so gated runs sit until an analyst
    releases them via ``approve_run``. Inactive playbooks bypass the
    gate and go straight to ``pending``.
    """
    from app.services.playbook.policy import recipe_requires_approval

    ts = _now(now)
    try:
        policy_requires_approval = recipe_requires_approval(
            step.tool_slug for step in playbook.steps
        )
    except ValueError:
        policy_requires_approval = False
    initial_status = (
        PlaybookRunStatus.awaiting_approval
        if playbook.active or policy_requires_approval
        else PlaybookRunStatus.pending
    )
    run = PlaybookRun(
        engagement_id=engagement.id,
        playbook_id=playbook.id,
        status=initial_status,
        scope_subset=list(scope_subset),
        steps_total=len(playbook.steps) * len(scope_subset),
        executor_kind=executor_kind,
        requested_by=requested_by,
        plan_snapshot=plan_snapshot,
        plan_sha256=plan_sha256,
        planned_at=ts if plan_snapshot is not None else None,
        created_at=ts,
    )
    session.add(run)
    session.flush()
    return run


# ---------------------------------------------------------------------------
# Cancel
# ---------------------------------------------------------------------------


class RunNotCancellableError(Exception):
    """Raised when the requested run is already in a terminal state."""

    def __init__(self, run_id: uuid.UUID, status: PlaybookRunStatus) -> None:
        self.run_id = run_id
        self.status = status
        super().__init__(f"run {run_id} is {status.value}; cannot cancel a terminal run")


class RunNotAwaitingApprovalError(Exception):
    """Raised when approve/reject is called on a run that's not in the
    ``awaiting_approval`` state (already approved, or a non-gated run)."""

    def __init__(self, run_id: uuid.UUID, status: PlaybookRunStatus) -> None:
        self.run_id = run_id
        self.status = status
        super().__init__(
            f"run {run_id} is {status.value}; only awaiting_approval runs "
            "can be approved or rejected"
        )


def _locked_run(session: Session, run_id: uuid.UUID) -> PlaybookRun | None:
    """Serialize analyst lifecycle transitions for one playbook run."""
    return session.execute(
        select(PlaybookRun).where(PlaybookRun.id == run_id).with_for_update()
    ).scalar_one_or_none()


def approve_run(
    session: Session,
    *,
    run_id: uuid.UUID,
    approver_id: uuid.UUID,
    reason: str | None = None,
    now: datetime | None = None,
) -> PlaybookRun:
    """Release an ``awaiting_approval`` run into ``pending``.

    Stamps ``approved_by`` + ``approved_at`` + optional ``approval_reason``
    so the audit trail carries who signed off and why. The worker's
    ``claim_next_pending`` picks the row up on its next poll.

    Idempotent for the ``awaiting_approval → pending`` transition:
    a second call finds the row already pending and no-ops. Terminal /
    running / already-cancelled → ``RunNotAwaitingApprovalError`` (409).
    """
    run = _locked_run(session, run_id)
    if run is None:
        raise KeyError(str(run_id))
    if run.status is PlaybookRunStatus.pending and run.approved_by is not None:
        # Already approved — second call is a no-op.
        run._approval_transitioned = False
        return run
    if run.status is not PlaybookRunStatus.awaiting_approval:
        raise RunNotAwaitingApprovalError(run_id, run.status)
    ts = _now(now)
    run.status = PlaybookRunStatus.pending
    run.approved_by = approver_id
    run.approved_at = ts
    if reason:
        run.approval_reason = reason
    run._approval_transitioned = True
    session.flush()
    return run


def reject_run(
    session: Session,
    *,
    run_id: uuid.UUID,
    approver_id: uuid.UUID,
    reason: str,
    now: datetime | None = None,
) -> PlaybookRun:
    """Reject an ``awaiting_approval`` run; flip to ``cancelled``.

    ``reason`` is required — an analyst rejection without a why is a
    dead-end for the requestor. Stamps ``rejected_by`` + ``rejected_at``
    + ``rejection_reason``; ``last_error`` mirrors the rejection reason
    so consumers reading via the existing status/last_error surface see
    a coherent story.

    Idempotent (cancelled → cancelled is a no-op) via ``cancel_run``'s
    existing idempotency contract; terminal-but-not-cancelled →
    ``RunNotAwaitingApprovalError``.
    """
    run = _locked_run(session, run_id)
    if run is None:
        raise KeyError(str(run_id))
    if run.status is PlaybookRunStatus.cancelled and run.rejected_by is not None:
        run._rejection_transitioned = False
        return run
    if run.status is not PlaybookRunStatus.awaiting_approval:
        raise RunNotAwaitingApprovalError(run_id, run.status)
    ts = _now(now)
    run.status = PlaybookRunStatus.cancelled
    run.rejected_by = approver_id
    run.rejected_at = ts
    run.rejection_reason = reason
    if not run.last_error:
        run.last_error = f"rejected: {reason}"
    if run.completed_at is None:
        run.completed_at = ts
    run._rejection_transitioned = True
    session.flush()
    return run


def cancel_run(
    session: Session,
    *,
    run_id: uuid.UUID,
    now: datetime | None = None,
    reason: str | None = None,
) -> PlaybookRun:
    """Flip a pending or running run to ``cancelled``.

    * Pending → cancelled immediately (worker will skip the grab).
    * Running → cancelled; the worker checks status between steps and
      bails cleanly.
    * Terminal → raises ``RunNotCancellableError`` (409).

    Idempotent for the (pending/running) → cancelled transition: a second
    call on an already-cancelled run is a no-op.
    """
    run = _locked_run(session, run_id)
    if run is None:
        raise KeyError(str(run_id))
    if run.status is PlaybookRunStatus.cancelled:
        return run
    if run.status not in {
        PlaybookRunStatus.pending,
        PlaybookRunStatus.running,
    }:
        # awaiting_approval must go through reject_run so the analyst identity
        # and required rejection reason are preserved in the audit trail.
        raise RunNotCancellableError(run_id, run.status)
    ts = _now(now)
    run.status = PlaybookRunStatus.cancelled
    if run.completed_at is None:
        run.completed_at = ts
    if reason and not run.last_error:
        run.last_error = reason
    session.flush()
    return run


# ---------------------------------------------------------------------------
# Worker-facing execute path (A3c)
# ---------------------------------------------------------------------------


def claim_next_pending(
    session: Session,
    *,
    worker_id: str | None = None,
    global_concurrency: int | None = None,
    per_engagement_concurrency: int | None = None,
) -> PlaybookRun | None:
    """Try to claim the oldest pending run for this worker.

    Uses ``SELECT ... FOR UPDATE SKIP LOCKED`` so N worker replicas polling
    concurrently pick disjoint rows. The claim atomically transitions
    ``pending`` → ``running`` inside the same transaction the caller commits.
    Returns ``None`` when there's nothing to do (the worker sleeps + retries).

    Caller must commit before invoking ``execute_pending_run`` — the intent
    is "hold the lock only long enough to flip status," not for the full
    execution duration (playbooks can take minutes).

    Uses raw SQL because SQLAlchemy 2's ``with_for_update(skip_locked=True)``
    doesn't reliably chain with ``limit(1)`` in the Postgres dialect — the
    generated statement lands as plain ``FOR UPDATE`` under some ORM path
    combos and two claimers deadlock instead of one seeing None.
    """
    from sqlalchemy import text

    if global_concurrency is not None or per_engagement_concurrency is not None:
        # Serialize only the tiny capacity-check + claim transaction. Without
        # this lock two replicas can both observe one remaining slot before
        # either pending→running transition commits.
        session.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": 5_246_544_987_331_001_219},
        )

    if global_concurrency is not None:
        running = session.execute(
            text("SELECT count(*) FROM playbook_runs WHERE status = 'running'")
        ).scalar_one()
        if running >= global_concurrency:
            return None

    if per_engagement_concurrency is None:
        eligibility = ""
        parameters: dict[str, int] = {}
    else:
        eligibility = """
            AND (
                SELECT count(*) FROM playbook_runs active
                WHERE active.engagement_id = queued.engagement_id
                  AND active.status = 'running'
            ) < :per_engagement
        """
        parameters = {"per_engagement": per_engagement_concurrency}

    row_id_row = session.execute(
        text(
            f"""
            SELECT queued.id FROM playbook_runs queued
            WHERE queued.status = 'pending'
            {eligibility}
            ORDER BY queued.created_at, queued.id
            LIMIT 1
            FOR UPDATE SKIP LOCKED
            """
        ),
        parameters,
    ).scalar_one_or_none()
    if row_id_row is None:
        return None
    run = session.get(PlaybookRun, row_id_row)
    if run is None:
        return None
    run.status = PlaybookRunStatus.running
    run.started_at = datetime.now(tz=UTC)
    run.worker_id = worker_id
    run.worker_heartbeat_at = run.started_at if worker_id else None
    session.flush()
    return run


def recover_abandoned_runs(
    session: Session,
    *,
    stale_before: datetime,
    now: datetime | None = None,
) -> list[PlaybookRun]:
    """Fail expired worker leases without replaying uncertain tool calls.

    A process can die after committing a ``running`` receipt but before an
    executor returns. Replaying that invocation is unsafe because its external
    side effects are unknown. Recovery therefore records an interrupted
    failure and leaves an explicit receipt for analyst review.
    """
    recovered_at = _now(now)
    runs = list(
        session.execute(
            select(PlaybookRun)
            .where(
                PlaybookRun.status == PlaybookRunStatus.running,
                or_(
                    PlaybookRun.worker_heartbeat_at < stale_before,
                    (
                        PlaybookRun.worker_heartbeat_at.is_(None)
                        & (PlaybookRun.started_at < stale_before)
                    ),
                ),
            )
            .with_for_update(skip_locked=True)
        ).scalars()
    )
    for run in runs:
        message = "worker heartbeat expired; in-flight step outcome is unknown and was not retried"
        run.status = PlaybookRunStatus.failed
        run.completed_at = recovered_at
        run.last_error = message
        run.worker_id = None
        run.worker_heartbeat_at = None
        receipts = session.execute(
            select(PlaybookStepExecution).where(
                PlaybookStepExecution.playbook_run_id == run.id,
                PlaybookStepExecution.status == PlaybookStepExecutionStatus.running,
            )
        ).scalars()
        for receipt in receipts:
            receipt.status = PlaybookStepExecutionStatus.failed
            receipt.completed_at = recovered_at
            receipt.duration_ms = max(
                0,
                round((recovered_at - receipt.started_at).total_seconds() * 1000),
            )
            receipt.error = message
        from app.services.mcp_lease import find_active_for_thread, release

        lease = find_active_for_thread(session, run.id)
        if lease is not None:
            release(
                session,
                lease_id=lease.id,
                reason="playbook worker heartbeat expired",
            )
    session.flush()
    return runs


def _effective_actor(
    run: PlaybookRun,
    *,
    fallback_type: ActorType,
    fallback_id: str | None,
) -> tuple[ActorType, str | None]:
    """Resolve the analyst identity that authorized this collection run.

    Active playbooks use the approving analyst; inactive playbooks use the
    requesting analyst. Legacy/system-created rows safely retain the caller's
    fallback and their milestones omit ``acting_user_id`` rather than borrowing
    another user's model key.
    """
    acting_user_id = run.approved_by or run.requested_by
    if acting_user_id is not None:
        return ActorType.user, str(acting_user_id)
    return fallback_type, fallback_id


def execute_pending_run(
    session: Session,
    *,
    run_id: uuid.UUID,
    executor: PlaybookExecutor,
    now: datetime | None = None,
    actor_type: ActorType = ActorType.system,
    actor_id: str | None = None,
    progress_commit: Callable[[str], bool] | None = None,
) -> PlaybookRun:
    """Drive a claimed run to terminal status.

    Reloads the run + its playbook + engagement into the caller session.
    Iterates steps × scope items, honoring cancellation between iterations
    (a mid-run ``cancel_run`` flips ``status=cancelled`` and this loop
    checks + bails). Coverage records + FindingsSummary accumulate exactly
    like the sync path — A3a's ``start_run`` is now a thin wrapper.
    """
    run = session.get(PlaybookRun, run_id)
    if run is None:
        raise KeyError(str(run_id))
    if run.status is PlaybookRunStatus.cancelled:
        # Pending → cancelled while we were between claim + execute. Nothing
        # to do; caller commits.
        return run
    if run.status not in {PlaybookRunStatus.running, PlaybookRunStatus.pending}:
        # Already terminal or in an unexpected state — bail without doing
        # more damage.
        return run

    playbook = session.get(Playbook, run.playbook_id)
    engagement = session.get(Engagement, run.engagement_id)
    if playbook is None or engagement is None:
        run.status = PlaybookRunStatus.failed
        run.completed_at = _now(now)
        run.last_error = "playbook or engagement missing"
        run.worker_id = None
        run.worker_heartbeat_at = None
        session.flush()
        return run
    if (
        engagement.intelligence_architecture != EngagementArchitecture.v3
        or engagement.status != EngagementStatus.active
        or engagement.work_state == EngagementWorkState.completed
    ):
        run.status = PlaybookRunStatus.failed
        run.completed_at = _now(now)
        run.last_error = "engagement is no longer an active writable v3 engagement"
        run.worker_id = None
        run.worker_heartbeat_at = None
        session.flush()
        return run

    if run.plan_snapshot is not None:
        from app.services.playbook.planning import build_execution_plan

        current_plan = build_execution_plan(
            playbook=playbook,
            scope_subset=list(run.scope_subset or []),
            required_executor=run.executor_kind.value,
        )
        snapshot_hash = str(run.plan_snapshot.get("plan_sha256") or "")
        if current_plan["plan_sha256"] != run.plan_sha256 or snapshot_hash != run.plan_sha256:
            run.status = PlaybookRunStatus.failed
            run.completed_at = _now(now)
            run.last_error = "playbook recipe no longer matches the reviewed execution plan"
            run.worker_id = None
            run.worker_heartbeat_at = None
            session.flush()
            return run

    started = run.started_at or _now(now)
    effective_actor_type, effective_actor_id = _effective_actor(
        run,
        fallback_type=actor_type,
        fallback_id=actor_id,
    )

    if not run.scope_subset:
        run.status = PlaybookRunStatus.failed
        run.completed_at = _now(now)
        run.last_error = "empty scope"
        run.worker_id = None
        run.worker_heartbeat_at = None
        session.flush()
        _emit_completion(session, engagement=engagement, playbook=playbook, run=run)
        return run

    from sqlalchemy import text as _text

    cancelled_mid = False
    halted_on_error = False
    invocations_attempted = 0
    discovered_domains: set[str] = set()
    for step in playbook.steps:
        target_source = (step.args_template or {}).get("__target_source")
        step_targets = [str(item) for item in run.scope_subset]
        if target_source == "discovered_domains":
            step_targets = sorted({*step_targets, *discovered_domains})
            run.steps_total += max(0, len(step_targets) - len(run.scope_subset))
        for scope_item in step_targets:
            # Fresh read of status — a cancel_run committed from another
            # session flips the row and this loop must see it promptly.
            # Uses raw text so we bypass ORM caching + enum-coercion paths
            # and get the current row status as a plain string.
            row_status = session.execute(
                _text("SELECT status FROM playbook_runs WHERE id = :id"),
                {"id": str(run.id)},
            ).scalar_one()
            if row_status == PlaybookRunStatus.cancelled.value:
                run.status = PlaybookRunStatus.cancelled
                cancelled_mid = True
                break
            if invocations_attempted >= MAX_PLAYBOOK_CALLS:
                halted_on_error = True
                run.steps_failed += 1
                run.last_error = (
                    f"playbook call budget exhausted at {MAX_PLAYBOOK_CALLS} invocations"
                )
                session.add(
                    AuditLog(
                        engagement_id=engagement.id,
                        actor_type=effective_actor_type,
                        actor_id=effective_actor_id,
                        event_type="playbook_run.call_budget_exhausted",
                        payload={
                            "playbook_run_id": str(run.id),
                            "playbook_id": str(playbook.id),
                            "maximum_calls": MAX_PLAYBOOK_CALLS,
                            "invocation_count": invocations_attempted,
                        },
                    )
                )
                break
            invocations_attempted += 1
            try:
                result = _run_one(
                    session,
                    engagement=engagement,
                    playbook=playbook,
                    run=run,
                    step=step,
                    scope_item=str(scope_item),
                    executor=executor,
                    now=started,
                    actor_type=effective_actor_type,
                    actor_id=effective_actor_id,
                    progress_commit=progress_commit,
                )
            except _RunCancelledBeforeStep:
                run.status = PlaybookRunStatus.cancelled
                cancelled_mid = True
                break
            post_step_status = session.execute(
                _text("SELECT status FROM playbook_runs WHERE id = :id"),
                {"id": str(run.id)},
            ).scalar_one()
            if post_step_status == PlaybookRunStatus.cancelled.value:
                run.status = PlaybookRunStatus.cancelled
                cancelled_mid = True
                break
            if result.ok:
                discovered_domains.update(
                    _authorized_discovered_domains(
                        session,
                        engagement=engagement,
                        result=result,
                    )
                )
            elif (step.args_template or {}).get("__on_error") == "stop":
                halted_on_error = True
                break
        if cancelled_mid or halted_on_error:
            break

    if cancelled_mid:
        # cancel_run set status + completed_at; still emit the completion
        # milestone so B3 doesn't miss the event (its receiver decides
        # whether cancelled runs get analyzed — likely no, but the signal
        # travels).
        run.worker_id = None
        run.worker_heartbeat_at = None
        _emit_completion(session, engagement=engagement, playbook=playbook, run=run)
        return run

    run.status = _final_status(run.steps_succeeded, run.steps_failed)
    run.completed_at = _now(now)
    run.worker_id = None
    run.worker_heartbeat_at = None
    session.flush()
    _emit_completion(session, engagement=engagement, playbook=playbook, run=run)
    return run


# ---------------------------------------------------------------------------
# Sync convenience (A3a) — enqueue + execute in one call
# ---------------------------------------------------------------------------


def start_run(
    session: Session,
    *,
    engagement: Engagement,
    playbook: Playbook,
    scope_subset: Sequence[str],
    executor: PlaybookExecutor,
    now: datetime | None = None,
    actor_type: ActorType = ActorType.system,
    actor_id: str | None = None,
    executor_kind: PlaybookExecutorKind = PlaybookExecutorKind.internal,
    requested_by: uuid.UUID | None = None,
) -> PlaybookRun:
    """Enqueue + execute a run synchronously. Tests + code paths that don't
    want the async queue call this.

    ``scope_subset=[]`` is still handled here — enqueue creates the row with
    the empty subset, then execute writes ``last_error='empty scope'`` +
    ``status=failed`` and emits the completion milestone. Preserves the A3a
    contract callers already depend on.
    """
    run = enqueue_run(
        session,
        engagement=engagement,
        playbook=playbook,
        scope_subset=scope_subset,
        executor_kind=executor_kind,
        requested_by=requested_by,
        now=now,
    )
    run.status = PlaybookRunStatus.running
    run.started_at = _now(now)
    session.flush()
    return execute_pending_run(
        session,
        run_id=run.id,
        executor=executor,
        now=now,
        actor_type=actor_type,
        actor_id=actor_id,
    )


def _current_scope_authorization(
    session: Session,
    *,
    engagement: Engagement,
    playbook: Playbook,
    target: str,
) -> tuple[bool, str]:
    """Re-evaluate one concrete target immediately before dispatch."""
    from app.models import ScopeItem
    from app.services.playbook.policy import execution_target_kind
    from app.services.scope_matcher import (
        evaluate_scope_candidates,
        infer_scope_kind,
        normalize_scope_value,
    )

    rows = list(
        session.execute(select(ScopeItem).where(ScopeItem.engagement_id == engagement.id)).scalars()
    )
    kind = infer_scope_kind(target)
    target_kind = execution_target_kind(
        list(playbook.applicable_entity_types or []) or [playbook.applies_to_asset_class]
    )
    if target_kind != "scope" and kind.value != target_kind:
        return False, f"target kind {kind.value!r} is incompatible with {target_kind!r}"
    if target_kind == "scope":
        normalized = normalize_scope_value(target, kind)
        exact_include = any(
            not row.is_exclusion
            and row.kind == kind
            and normalize_scope_value(row.value, row.kind) == normalized
            for row in rows
        )
        if not exact_include:
            return False, "scope review requires a current exact include row"
    decision = evaluate_scope_candidates([(target, kind)], rows)
    return decision.allowed, decision.reason


def _run_one(
    session: Session,
    *,
    engagement: Engagement,
    playbook: Playbook,
    run: PlaybookRun,
    step: PlaybookStep,
    scope_item: str,
    executor: PlaybookExecutor,
    now: datetime,
    actor_type: ActorType,
    actor_id: str | None,
    progress_commit: Callable[[str], bool] | None = None,
) -> StepResult:
    """Invoke one step against one scope item and write coverage records.

    Executor exceptions become ``StepResult(ok=False, error=...)`` — a
    thrown tool is a failed step, not a broken run. The tests exercise this.
    """
    step_tool_slug = step.tool_slug
    step_args_template = step.args_template or {}
    step_satisfies_node_ids = list(step.satisfies_node_ids or [])
    execution_args = {
        key: value for key, value in step_args_template.items() if not str(key).startswith("__")
    }

    from app.services.playbook.evidence import redact_json, redact_text, safe_arguments

    receipt_started_at = datetime.now(tz=UTC)
    receipt_timer = perf_counter()
    try:
        transport = executor_for_tool_slug(step_tool_slug)
    except ValueError:
        # Extension/test executors may supply non-catalogued tools. The run's
        # persisted executor is still the safest truthful fallback label.
        transport = run.executor_kind.value
    prior_attempt = session.execute(
        select(func.max(PlaybookStepExecution.attempt)).where(
            PlaybookStepExecution.playbook_run_id == run.id,
            PlaybookStepExecution.playbook_step_id == step.id,
            PlaybookStepExecution.target == scope_item,
        )
    ).scalar_one()
    receipt = PlaybookStepExecution(
        playbook_run_id=run.id,
        playbook_step_id=step.id,
        sort_order=step.sort_order,
        tool_slug=step_tool_slug,
        target=scope_item,
        transport=transport,
        attempt=(prior_attempt or 0) + 1,
        status=PlaybookStepExecutionStatus.running,
        arguments=safe_arguments(resolve_step_args(step_tool_slug, execution_args, scope_item)),
        started_at=receipt_started_at,
    )
    session.add(receipt)
    session.flush()
    # The worker supplies a commit callback so the running receipt survives a
    # process failure during the external invocation. Sync tests/services keep
    # their caller-owned transaction by leaving it unset.
    if progress_commit is not None and not progress_commit("before"):
        raise _RunCancelledBeforeStep

    if run.plan_snapshot is None:
        # Legacy/internal callers predate authoritative kickoff plans. HTTP and
        # worker-created runs always carry a plan and take the fresh gate below.
        authorized, authorization_reason = True, "legacy internal run"
    else:
        authorized, authorization_reason = _current_scope_authorization(
            session,
            engagement=engagement,
            playbook=playbook,
            target=scope_item,
        )
    if not authorized:
        result = StepResult(
            ok=False,
            error=redact_text(
                f"target is no longer authorized; invocation skipped ({authorization_reason})"
            ),
        )
    else:
        try:
            result = executor.run_step(
                tool_slug=step_tool_slug,
                args_template=execution_args,
                scope_context=scope_item,
            )
        except Exception as exc:  # noqa: BLE001 - executor is untrusted; convert to step failure
            result = StepResult(
                ok=False,
                error=redact_text(f"{type(exc).__name__}: {exc}"),
            )
            logger.warning(
                "playbook.step.executor_raised",
                playbook=playbook.slug,
                tool=step_tool_slug,
                scope_item=scope_item,
                error=result.error,
            )

    safe_error = redact_text(result.error)
    safe_data = redact_json(result.data)
    if safe_error != result.error or safe_data != result.data:
        result = replace(
            result,
            error=safe_error,
            data=safe_data if isinstance(safe_data, dict) else {"value": safe_data},
        )
    collected_result = result

    # Every supported real playbook tool persists through the same canonical
    # bridge, regardless of transport. MCP lease calls return raw data to this
    # worker; they must not create a parallel server-owned finding path.
    bridge = None
    if result.ok and not getattr(result, "stub", False):
        from app.services.playbook.finding_bridge import (
            FindingBridgePersistenceError,
            bridge_step_to_finding,
        )

        try:
            actor_uuid = uuid.UUID(actor_id) if actor_id else None
        except (ValueError, TypeError):
            actor_uuid = None
        try:
            bridge = bridge_step_to_finding(
                session,
                engagement_id=engagement.id,
                playbook_tool=step_tool_slug,
                scope_item=scope_item,
                args_template=execution_args,
                data=result.data,
                thread_id=run.id,
                acting_user_id=actor_uuid,
                operation_id=run.id,
            )
        except FindingBridgePersistenceError as exc:
            result = StepResult(ok=False, error=str(exc))
    if bridge is not None:
        run.findings_new += bridge.items_added
        # Multiple tools can enrich the same canonical group in one run.
        # Count the group's latest total once, then only its growth.
        seen_totals: dict[uuid.UUID, int] = getattr(run, "_bridge_finding_totals", {})
        previous = seen_totals.get(bridge.finding_id, 0)
        run.findings_total += max(bridge.items_total - previous, 0)
        seen_totals[bridge.finding_id] = max(previous, bridge.items_total)
        run._bridge_finding_totals = seen_totals  # type: ignore[attr-defined]
    elif not isinstance(executor, (InternalExecutor, MCPExecutor, RoutedExecutor)):
        # Test/extension executors may own persistence and declare counts.
        run.findings_new += result.findings_new
        run.findings_total += result.findings_total
        run.findings_unvalidated += result.findings_unvalidated
        run.findings_high_severity += result.findings_high_severity

    if result.ok:
        run.steps_succeeded += 1
    else:
        run.steps_failed += 1
        if result.error and not run.last_error:
            run.last_error = result.error

    if getattr(result, "stub", False):
        status = CoverageRecordStatus.stub
    elif result.ok:
        status = CoverageRecordStatus.satisfied
    else:
        status = CoverageRecordStatus.failed
    receipt.status = (
        PlaybookStepExecutionStatus.stub
        if getattr(result, "stub", False)
        else (
            PlaybookStepExecutionStatus.succeeded
            if result.ok
            else PlaybookStepExecutionStatus.failed
        )
    )
    receipt.completed_at = datetime.now(tz=UTC)
    receipt.duration_ms = max(0, round((perf_counter() - receipt_timer) * 1000))
    from app.services.playbook.evidence import persist_step_evidence

    receipt.error = result.error
    persist_step_evidence(
        session,
        engagement_id=engagement.id,
        run_id=run.id,
        execution=receipt,
        tool_slug=step_tool_slug,
        target=scope_item,
        result=collected_result,
        finding_id=bridge.finding_id if bridge is not None else None,
        captured_at=receipt.completed_at,
    )

    for node_id in step_satisfies_node_ids:
        cov.record_coverage_attempt(
            session,
            engagement_id=engagement.id,
            node_id=node_id,
            node_tier=CoverageNodeTier.baseline,
            asset_class=playbook.applies_to_asset_class,
            scope_subset=[scope_item],
            status=status,
            methodology_id=engagement.methodology_id,
            playbook_run_id=run.id,
            notes=(
                str(result.data.get("note"))
                if result.stub and result.data.get("note")
                else (None if result.ok else result.error)
            ),
            actor_type=actor_type,
            actor_id=actor_id,
            now=now,
        )
    session.flush()
    if progress_commit is not None:
        progress_commit("after")
    return result


def _authorized_discovered_domains(
    session: Session,
    *,
    engagement: Engagement,
    result: StepResult,
) -> set[str]:
    """Extract prior-step domains and revalidate every one against scope."""
    from app.models import ScopeItem, ScopeKind
    from app.services.scope_matcher import evaluate_scope, normalize_domain

    candidates: set[str] = set()
    values = result.data.get("subdomains")
    if isinstance(values, list):
        candidates.update(
            normalize_domain(value) for value in values if isinstance(value, str) and value.strip()
        )
    for item in result.data.get("lease_findings") or []:
        if not isinstance(item, dict):
            continue
        target = item.get("target")
        if isinstance(target, str) and target.strip():
            candidates.add(normalize_domain(target))

    if not candidates:
        return set()
    scope_rows = list(
        session.execute(select(ScopeItem).where(ScopeItem.engagement_id == engagement.id)).scalars()
    )
    return {
        candidate
        for candidate in sorted(candidates)[:500]
        if candidate and evaluate_scope(candidate, ScopeKind.domain, scope_rows).allowed
    }


def _scope_items_by_asset_class(session: Session, engagement: Engagement) -> dict[str, list[str]]:
    """Group the engagement's active scope items by their kind.

    The methodology snapshot keys baseline nodes by ``asset_class`` (e.g.
    ``domain``). Scope items carry a ``kind`` that maps 1:1 to that axis
    (``domain`` / ``ip`` / ``url`` / ``cidr``). This groups them so
    ``derive_expected_triples`` can cross baseline nodes with the matching
    scope items.
    """
    from app.models import ScopeItem

    rows = (
        session.execute(
            select(ScopeItem).where(
                ScopeItem.engagement_id == engagement.id,
                ScopeItem.is_exclusion.is_(False),
            )
        )
        .scalars()
        .all()
    )
    grouped: dict[str, list[str]] = {}
    for item in rows:
        grouped.setdefault(item.kind, []).append(item.value)
    return grouped


def _check_and_emit_coverage_state(
    session: Session,
    *,
    engagement: Engagement,
    run: PlaybookRun,
) -> None:
    """After a collection run, detect baseline completion and coverage gaps.

    This is the wiring that was missing: ``check_baseline_complete`` and
    ``open_coverage_gap`` existed and were tested but never called in
    production. Now, after every terminal run with a methodology selected,
    we derive the expected baseline triples from the frozen snapshot + the
    engagement's scope, check satisfaction, and:

    * if all expected triples are satisfied → ``mark_baseline_completed``
      (idempotent — flips phase to exploration + emits ``baseline.completed``).
    * for each still-unsatisfied node → ``open_coverage_gap`` (idempotent per
      node so repeated runs don't duplicate the signal).

    ``acting_user_id`` comes from the run's effective actor (approver or
    requester) so downstream milestone intelligence resolves the correct BYO
    key. Legacy/system runs omit it; the consumer refuses to borrow.
    """
    if engagement.methodology_id is None or engagement.baseline_completed_at is not None:
        return  # no methodology, or already past baseline

    scope_map = _scope_items_by_asset_class(session, engagement)
    if not scope_map:
        return  # no scope → nothing to check

    expected = meth.derive_expected_triples(engagement, scope_item_ids_by_asset_class=scope_map)
    if not expected:
        return  # methodology has no baseline nodes for the in-scope asset classes

    is_complete, unsatisfied = cov.check_baseline_complete(
        session, engagement_id=engagement.id, expected=expected
    )

    acting_user_id = run.approved_by or run.requested_by

    # Sweep stale coverage (TTL-based demotion) so re-collection candidates
    # surface even between runs. This wires the previously-orphaned
    # sweep_stale + derive_node_ttls functions into the production path.
    ttls = meth.derive_node_ttls(engagement)
    if ttls:
        stale_rows = cov.sweep_stale(
            session,
            engagement_id=engagement.id,
            node_ttls=ttls,
            now=run.completed_at or datetime.now(tz=UTC),
        )
        for stale_row in stale_rows:
            cov.open_coverage_gap(
                session,
                engagement_id=engagement.id,
                node_id=stale_row.node_id,
                node_tier=stale_row.node_tier,
                asset_class=stale_row.asset_class,
                reason="coverage TTL lapsed",
                acting_user_id=acting_user_id,
                dedupe_key=f"coverage.gap.opened:{engagement.id}:{stale_row.node_id}",
            )

    if is_complete:
        cov.mark_baseline_completed(
            session,
            engagement_id=engagement.id,
            methodology_id=engagement.methodology_id,
            actor_type=ActorType.user if acting_user_id else ActorType.system,
            actor_id=str(acting_user_id) if acting_user_id else None,
        )
        return

    # Emit one idempotent gap per unsatisfied node so the strategy
    # intelligence can propose work to close them.
    unsatisfied_nodes = sorted({node_id for node_id, _, _ in unsatisfied})
    for node_id in unsatisfied_nodes:
        cov.open_coverage_gap(
            session,
            engagement_id=engagement.id,
            node_id=node_id,
            node_tier=CoverageNodeTier.baseline,
            asset_class=playbook_asset_class_for_node(engagement, node_id, scope_map),
            reason="baseline node not yet satisfied",
            acting_user_id=acting_user_id,
            dedupe_key=f"coverage.gap.opened:{engagement.id}:{node_id}",
        )


def playbook_asset_class_for_node(
    engagement: Engagement, node_id: str, scope_map: dict[str, list[str]]
) -> str:
    """Resolve the asset_class for a methodology node from the frozen snapshot."""
    snapshot = engagement.methodology_snapshot or {}
    for node in snapshot.get("nodes", []):
        if node.get("node_id") == node_id:
            return node.get("asset_class", "domain")
    return "domain"


def _emit_completion(
    session: Session,
    *,
    engagement: Engagement,
    playbook: Playbook,
    run: PlaybookRun,
) -> None:
    """Enqueue ``collection.job.completed`` for this run.

    Emitted for every terminal run (``completed`` / ``partial`` / ``failed``
    / ``cancelled``) — B3's milestone runner decides whether a failed or
    cancelled run is worth analyzing (typically no); the emission is the
    *event*, not the recommendation. ``methodology_id`` is the engagement's
    current selection (per A1); when the engagement has no methodology, we
    skip the emission — B3 has nothing to hang analysis off.
    """
    if engagement.methodology_id is None:
        logger.info(
            "playbook.completion.skipped_no_methodology",
            playbook=playbook.slug,
            run_id=str(run.id),
        )
        return
    node_ids = sorted({n for s in playbook.steps for n in (s.satisfies_node_ids or [])})
    payload = ms.collection_job_completed(
        engagement_id=str(engagement.id),
        playbook_run_id=str(run.id),
        methodology_id=str(engagement.methodology_id),
        node_ids=node_ids,
        asset_class=playbook.applies_to_asset_class,
        scope_subset=[str(s) for s in run.scope_subset or []],
        findings_summary={
            "new": run.findings_new,
            "unvalidated": run.findings_unvalidated,
            "high_severity": run.findings_high_severity,
            "total": run.findings_total,
        },
        acting_user_id=(
            str(run.approved_by or run.requested_by)
            if (run.approved_by or run.requested_by) is not None
            else None
        ),
    )
    enqueue_event(
        session,
        idempotency_key=f"collection.job.completed:{run.id}",
        engagement_id=engagement.id,
        stream_name=outbound_stream(engagement.id),
        payload=payload,
    )
    # Detect baseline completion and coverage gaps now that this run wrote
    # new coverage records. This wires the previously-orphaned A2 functions
    # (check_baseline_complete / mark_baseline_completed / open_coverage_gap)
    # into the production collection path.
    _check_and_emit_coverage_state(session, engagement=engagement, run=run)


def new_run_id() -> uuid.UUID:
    """Return a fresh id — exposed so callers can pre-mint an id if they
    need to log/reference the run before it's persisted."""
    from app.db.base import uuid7

    return uuid7()
