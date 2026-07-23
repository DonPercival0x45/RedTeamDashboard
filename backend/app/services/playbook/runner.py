"""Playbook runner — Track A step A3a.

``start_run`` orchestrates one execution of a playbook against a scope
subset. For each step × each scope item, it:

1. Calls the injected executor's ``run_step``.
2. Writes a ``CoverageRecord`` per (satisfies_node, scope_item) with
   ``status=satisfied`` on success or ``status=failed`` on error — per-step
   granularity (A3a decision Q3). ``methodology_id`` on those records comes
   from the engagement's ``methodology_id`` when present so B1/B2 can pin
   coverage to the selected tree.
3. Accumulates ``FindingsSummary`` counters onto the ``PlaybookRun``.

At run end:

* Final ``status`` = ``completed`` when all steps succeeded, ``partial`` if
  at least one succeeded and one failed, ``failed`` if none succeeded.
* Emits ``collection.job.completed`` through the shared durable outbox with
  the ``FindingsSummary`` (counts-only per B3's contract).

Sync execution, single-transaction — no queue, no fan-out. A3b adds the
queue + async fan-out; A5 adds the pre-authorization gate. The runner shape
here (a single call that returns a completed ``PlaybookRun``) is what A3b
wraps, so A3b is additive.
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
    """Create + execute a playbook run in one call.

    Sync: returns once every step has been attempted. The caller commits.
    Idempotency: this creates a NEW ``PlaybookRun`` every call — reruns are a
    new row (the coverage log picks up the fresh attempts). Callers who want
    per-run idempotency dedupe upstream on their own key.

    ``executor`` is any object matching ``PlaybookExecutor``. Tests inject a
    ``MockExecutor``; A3b wires the real ``InternalExecutor`` to the tool
    registry.

    Runs against a scope with zero items get status ``failed`` +
    ``last_error='empty scope'`` — the analyst asked for a run against
    nothing, and we surface it as a fault instead of silently succeeding.
    """
    started = _now(now)
    run = PlaybookRun(
        engagement_id=engagement.id,
        playbook_id=playbook.id,
        status=PlaybookRunStatus.running,
        scope_subset=list(scope_subset),
        started_at=started,
        steps_total=len(playbook.steps) * len(scope_subset),
    )
    session.add(run)
    session.flush()

    if not scope_subset:
        run.status = PlaybookRunStatus.failed
        run.completed_at = started
        run.last_error = "empty scope"
        session.flush()
        _emit_completion(session, engagement=engagement, playbook=playbook, run=run)
        return run

    for step in playbook.steps:
        for scope_item in scope_subset:
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

    run.status = _final_status(run.steps_succeeded, run.steps_failed)
    run.completed_at = _now(now)
    session.flush()
    _emit_completion(session, engagement=engagement, playbook=playbook, run=run)
    return run


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
            # Runner has no direct methodology-node context; we tag baseline
            # (playbook steps satisfy baseline nodes by convention). Callers
            # who need to write exploration-tier coverage do so outside the
            # runner.
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

    Emitted for every terminal run (``completed`` / ``partial`` / ``failed``)
    — B3's milestone runner decides whether a failed run is worth analyzing
    (typically no); the emission is the *event*, not the recommendation.
    ``methodology_id`` is the engagement's current selection (per A1); when
    the engagement has no methodology, we skip the emission — B3 has nothing
    to hang analysis off.
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
