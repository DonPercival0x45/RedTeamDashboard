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
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy.orm import Session

from app.engagement import milestones as ms
from app.models import (
    ActorType,
    CoverageNodeTier,
    CoverageRecordStatus,
    Engagement,
    Playbook,
    PlaybookRun,
    PlaybookRunStatus,
)
from app.runs.streams import outbound_stream
from app.services import coverage as cov
from app.services.command_outbox import enqueue_event
from app.services.playbook.executor import PlaybookExecutor, StepResult

logger = structlog.get_logger(__name__)


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
    now: datetime | None = None,
) -> PlaybookRun:
    """Create a ``pending`` playbook run. Worker picks it up.

    ``steps_total`` is pre-populated so a client polling the row can see
    "N of M steps done" progress the moment the worker starts. Caller
    commits.
    """
    ts = _now(now)
    run = PlaybookRun(
        engagement_id=engagement.id,
        playbook_id=playbook.id,
        status=PlaybookRunStatus.pending,
        scope_subset=list(scope_subset),
        steps_total=len(playbook.steps) * len(scope_subset),
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
        super().__init__(
            f"run {run_id} is {status.value}; cannot cancel a terminal run"
        )


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
    run = session.get(PlaybookRun, run_id)
    if run is None:
        raise KeyError(str(run_id))
    if run.status is PlaybookRunStatus.cancelled:
        return run
    if run.status in TERMINAL_STATUSES:
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


def claim_next_pending(session: Session) -> PlaybookRun | None:
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

    row_id_row = session.execute(
        text(
            """
            SELECT id FROM playbook_runs
            WHERE status = 'pending'
            ORDER BY created_at, id
            LIMIT 1
            FOR UPDATE SKIP LOCKED
            """
        )
    ).scalar_one_or_none()
    if row_id_row is None:
        return None
    run = session.get(PlaybookRun, row_id_row)
    if run is None:
        return None
    run.status = PlaybookRunStatus.running
    run.started_at = datetime.now(tz=UTC)
    session.flush()
    return run


def execute_pending_run(
    session: Session,
    *,
    run_id: uuid.UUID,
    executor: PlaybookExecutor,
    now: datetime | None = None,
    actor_type: ActorType = ActorType.system,
    actor_id: str | None = None,
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
        session.flush()
        return run

    started = run.started_at or _now(now)

    if not run.scope_subset:
        run.status = PlaybookRunStatus.failed
        run.completed_at = _now(now)
        run.last_error = "empty scope"
        session.flush()
        _emit_completion(session, engagement=engagement, playbook=playbook, run=run)
        return run

    from sqlalchemy import text as _text

    cancelled_mid = False
    for step in playbook.steps:
        for scope_item in run.scope_subset:
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
            _run_one(
                session,
                engagement=engagement,
                playbook=playbook,
                run=run,
                step_tool_slug=step.tool_slug,
                step_args_template=step.args_template,
                step_satisfies_node_ids=list(step.satisfies_node_ids or []),
                scope_item=str(scope_item),
                executor=executor,
                now=started,
                actor_type=actor_type,
                actor_id=actor_id,
            )
        if cancelled_mid:
            break

    if cancelled_mid:
        # cancel_run set status + completed_at; still emit the completion
        # milestone so B3 doesn't miss the event (its receiver decides
        # whether cancelled runs get analyzed — likely no, but the signal
        # travels).
        _emit_completion(session, engagement=engagement, playbook=playbook, run=run)
        return run

    run.status = _final_status(run.steps_succeeded, run.steps_failed)
    run.completed_at = _now(now)
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


def _run_one(
    session: Session,
    *,
    engagement: Engagement,
    playbook: Playbook,
    run: PlaybookRun,
    step_tool_slug: str,
    step_args_template: dict[str, Any],
    step_satisfies_node_ids: list[str],
    scope_item: str,
    executor: PlaybookExecutor,
    now: datetime,
    actor_type: ActorType,
    actor_id: str | None,
) -> None:
    """Invoke one step against one scope item and write coverage records.

    Executor exceptions become ``StepResult(ok=False, error=...)`` — a
    thrown tool is a failed step, not a broken run. The tests exercise this.
    """
    try:
        result = executor.run_step(
            tool_slug=step_tool_slug,
            args_template=step_args_template,
            scope_context=scope_item,
        )
    except Exception as exc:  # noqa: BLE001 - executor is untrusted; convert to step failure
        result = StepResult(ok=False, error=f"{type(exc).__name__}: {exc}")
        logger.exception(
            "playbook.step.executor_raised",
            playbook=playbook.slug,
            tool=step_tool_slug,
            scope_item=scope_item,
        )

    if result.ok:
        run.steps_succeeded += 1
    else:
        run.steps_failed += 1
        if result.error and not run.last_error:
            run.last_error = result.error

    run.findings_new += result.findings_new
    run.findings_unvalidated += result.findings_unvalidated
    run.findings_high_severity += result.findings_high_severity
    run.findings_total += result.findings_total

    status = (
        CoverageRecordStatus.satisfied if result.ok else CoverageRecordStatus.failed
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
            notes=None if result.ok else result.error,
            actor_type=actor_type,
            actor_id=actor_id,
            now=now,
        )
    session.flush()


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
    node_ids = sorted(
        {
            n
            for s in playbook.steps
            for n in (s.satisfies_node_ids or [])
        }
    )
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
    )
    enqueue_event(
        session,
        idempotency_key=f"collection.job.completed:{run.id}",
        engagement_id=engagement.id,
        stream_name=outbound_stream(engagement.id),
        payload=payload,
    )


def new_run_id() -> uuid.UUID:
    """Return a fresh id — exposed so callers can pre-mint an id if they
    need to log/reference the run before it's persisted."""
    from app.db.base import uuid7

    return uuid7()
