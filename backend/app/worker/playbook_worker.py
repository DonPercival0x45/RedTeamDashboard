"""Playbook run worker — Track A step A3c.

Polls ``playbook_runs`` for rows in ``status='pending'``, claims one via
``SELECT ... FOR UPDATE SKIP LOCKED`` (multiple worker replicas cooperate
this way), and drives it to a terminal status through
``execute_pending_run`` + the default ``InternalExecutor``.

Lives in the worker process as a daemon thread, same shape as
``LeaseSweeperThread``. Two transactions per run:

1. **Claim** — tiny; grabs the pending row, flips ``pending → running``,
   commits immediately. Row lock only held for milliseconds.
2. **Execute** — long; the actual step loop. If a second worker claimed a
   row we didn't get, they hold theirs; we sleep and try again.

Cancellation is handled inside ``execute_pending_run`` — a mid-run
``cancel_run`` flips ``status='cancelled'`` and the runner bails between
steps. No signal-handling here beyond the standard ``stop_event`` from
``worker/main.py``.
"""
from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from time import monotonic
from typing import Any

import structlog
from sqlalchemy import text, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import (
    Engagement,
    PlaybookExecutorKind,
    PlaybookRun,
    PlaybookRunStatus,
    PlaybookStepExecution,
    PlaybookStepExecutionStatus,
)
from app.services.playbook import (
    InternalExecutor,
    RoutedExecutor,
    claim_next_pending,
    execute_pending_run,
    recover_abandoned_runs,
)
from app.services.playbook.executor import (
    MCPExecutor,
    PlaybookExecutor,
    executor_for_tool_slug,
    executor_kinds_for_tools,
)

logger = structlog.get_logger(__name__)

SessionFactory = Callable[[], Session]


class PlaybookWorkerThread:
    """Polls for pending playbook runs and drives them to completion."""

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        poll_interval_seconds: float = 2.0,
        redis_client: Any | None = None,
        heartbeat_interval_seconds: float = 15.0,
        stale_after_seconds: float = 300.0,
        recovery_interval_seconds: float = 60.0,
    ) -> None:
        self._session_factory = session_factory
        self._poll = poll_interval_seconds
        self._redis = redis_client
        self._worker_id = str(uuid.uuid4())
        self._heartbeat_interval = heartbeat_interval_seconds
        self._stale_after = stale_after_seconds
        self._recovery_interval = recovery_interval_seconds
        self._last_recovery = 0.0

    def _build_executor(
        self,
        kind: PlaybookExecutorKind,
        *,
        lease_token: str | None = None,
        engagement_slug: str | None = None,
        tool_secrets: dict[str, str] | None = None,
    ) -> PlaybookExecutor:
        """Instantiate the right executor for this run.

        MCPExecutor lazily opens its client on first ``run_step`` — building
        one here is cheap. We build fresh per run so a newly-registered MCP
        tool becomes visible on the next dispatch instead of stuck behind a
        cached catalog.
        """
        if kind is PlaybookExecutorKind.mcp:
            base_url = f"{settings.playbook_mcp_url.rstrip('/')}/sse"
            return MCPExecutor(
                base_url=base_url,
                api_key=settings.worker_mcp_api_key,
                lease_token=lease_token,
                engagement_slug=engagement_slug,
                tool_secrets=tool_secrets,
            )
        return InternalExecutor()

    def _claim(self) -> tuple[str, PlaybookExecutorKind] | None:
        """Grab the next pending run + flip to running. Returns
        ``(run_id_str, executor_kind)`` or ``None`` when nothing's pending.

        Executor kind travels back as an enum so the execute step builds the
        right executor without a second row read.
        """
        try:
            session = self._session_factory()
        except Exception:
            logger.exception("playbook_worker.claim_session_unavailable")
            return None
        try:
            run = claim_next_pending(session, worker_id=self._worker_id)
            if run is None:
                session.commit()
                return None
            claimed_id = str(run.id)
            kind = run.executor_kind
            session.commit()
            return claimed_id, kind
        except Exception:
            session.rollback()
            logger.exception("playbook_worker.claim_failed")
            return None
        finally:
            session.close()

    def _heartbeat_once(self, run_id: uuid.UUID) -> None:
        """Refresh worker ownership in a short independent transaction."""
        try:
            session = self._session_factory()
        except Exception:
            # Keep the heartbeat loop alive so a transient pool/database outage
            # can recover on the next interval rather than silently expiring a
            # still-running worker lease.
            logger.exception(
                "playbook_worker.heartbeat_session_unavailable",
                run_id=str(run_id),
            )
            return
        try:
            session.execute(
                update(PlaybookRun)
                .where(
                    PlaybookRun.id == run_id,
                    PlaybookRun.status == PlaybookRunStatus.running,
                    PlaybookRun.worker_id == self._worker_id,
                )
                .values(worker_heartbeat_at=datetime.now(tz=UTC))
            )
            session.commit()
        except Exception:
            session.rollback()
            logger.exception("playbook_worker.heartbeat_failed", run_id=str(run_id))
        finally:
            session.close()

    def _heartbeat_loop(self, run_id: uuid.UUID, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            self._heartbeat_once(run_id)
            stop_event.wait(self._heartbeat_interval)

    def _recover_abandoned(self) -> None:
        """Periodically fail expired leases; never replay uncertain steps."""
        current = monotonic()
        if current - self._last_recovery < self._recovery_interval:
            return
        self._last_recovery = current
        try:
            session = self._session_factory()
        except Exception:
            logger.exception("playbook_worker.recovery_session_unavailable")
            return
        try:
            recovered = recover_abandoned_runs(
                session,
                stale_before=datetime.now(tz=UTC)
                - timedelta(seconds=self._stale_after),
            )
            session.commit()
            for run in recovered:
                logger.warning(
                    "playbook_worker.abandoned_run_recovered",
                    run_id=str(run.id),
                )
        except Exception:
            session.rollback()
            logger.exception("playbook_worker.recovery_failed")
        finally:
            session.close()

    def _execute(self, run_id_str: str, kind: PlaybookExecutorKind) -> None:
        """Drive the claimed run in a separate transaction.

        Any exception here transitions the run to ``failed`` with the
        error message so a botched executor doesn't leave a run stuck at
        ``running`` forever. The runner's per-step ``try/except`` already
        catches executor exceptions; this outer catch is for anything
        outside the step loop (DB glitch, model reload failure).
        """
        heartbeat_stop: threading.Event | None = None
        heartbeat_thread: threading.Thread | None = None
        lease_id: uuid.UUID | None = None

        try:
            session = self._session_factory()
        except Exception:
            logger.exception("playbook_worker.execute_session_unavailable")
            return
        try:
            run_id = uuid.UUID(run_id_str)
            executor: PlaybookExecutor
            run = session.get(PlaybookRun, run_id)
            if run is None:
                raise RuntimeError(f"playbook run {run_id} not found")
            engagement = session.get(Engagement, run.engagement_id)
            if engagement is None:
                raise RuntimeError("playbook engagement not found")
            heartbeat_stop = threading.Event()
            heartbeat_thread = threading.Thread(
                target=self._heartbeat_loop,
                args=(run_id, heartbeat_stop),
                name=f"playbook-heartbeat-{run_id}",
                daemon=True,
            )
            heartbeat_thread.start()
            required_kinds = executor_kinds_for_tools(
                step.tool_slug for step in run.playbook.steps
            )
            delegates: dict[str, PlaybookExecutor] = {}
            if "internal" in required_kinds:
                from app.services.playbook.tools.breach_lookup import run_from_store
                from app.services.playbook.tools.scope_hygiene import (
                    run_scope_hygiene,
                )

                internal = InternalExecutor()
                internal.register(
                    "breach-lookup",
                    lambda scope, args: run_from_store(
                        session,
                        engagement_id=engagement.id,
                        scope_context=scope,
                        args=args,
                    ),
                )
                internal.register(
                    "scope-hygiene",
                    lambda scope, _args: run_scope_hygiene(
                        session,
                        engagement_id=engagement.id,
                        scope_context=scope,
                    ),
                )
                delegates["internal"] = internal

            if "mcp" in required_kinds:
                from app.services.mcp_lease import mint_for_engagement, release
                from app.worker.runner import _resolve_tool_secrets

                tool_slugs = []
                for step in run.playbook.steps:
                    if executor_for_tool_slug(step.tool_slug) != "mcp":
                        continue
                    tool_name = step.tool_slug.removeprefix("mcp_")
                    if tool_name == "port_scan":
                        tool_name = "portscan"
                    tool_slugs.append(tool_name)
                lease = mint_for_engagement(
                    session,
                    engagement_id=engagement.id,
                    thread_id=run.id,
                    allowed_tools=tool_slugs,
                    context={
                        "engagement": {"slug": engagement.slug},
                        "acting_user_id": (
                            str(run.requested_by) if run.requested_by else None
                        ),
                        "playbook_run_id": str(run.id),
                        "playbook_approved_by": (
                            str(run.approved_by) if run.approved_by else None
                        ),
                        "playbook_approved_at": (
                            run.approved_at.isoformat() if run.approved_at else None
                        ),
                    },
                    prompt_keys=[],
                )
                lease_id = lease.id
                # The MCP server uses its own database session and must see the
                # lease before the first tool invocation.
                session.commit()
                secrets = (
                    _resolve_tool_secrets(self._redis, str(run.requested_by))
                    if self._redis is not None and run.requested_by is not None
                    else {}
                )
                delegates["mcp"] = self._build_executor(
                    PlaybookExecutorKind.mcp,
                    lease_token=str(lease.id),
                    engagement_slug=engagement.slug,
                    tool_secrets=secrets,
                )

            executor = (
                next(iter(delegates.values()))
                if len(delegates) == 1
                else RoutedExecutor(delegates)
            )

            def commit_progress(phase: str) -> bool:
                # Atomic ownership fencing: recovery and progress both update
                # the same row under a conditional write. Whichever wins the
                # row lock decides whether dispatch may proceed.
                owned = session.execute(
                    update(PlaybookRun)
                    .where(
                        PlaybookRun.id == run.id,
                        PlaybookRun.status == PlaybookRunStatus.running,
                        PlaybookRun.worker_id == self._worker_id,
                    )
                    .values(worker_heartbeat_at=datetime.now(tz=UTC))
                    .returning(PlaybookRun.id)
                ).scalar_one_or_none()
                if owned is not None:
                    session.commit()
                    return True

                state = session.execute(
                    text(
                        "SELECT status, worker_id FROM playbook_runs "
                        "WHERE id = :run_id"
                    ),
                    {"run_id": str(run.id)},
                ).one()
                if state.status == PlaybookRunStatus.cancelled.value:
                    if phase == "before":
                        # Discard the uncommitted running receipt: the external
                        # call never started, so no attempt should be claimed.
                        session.rollback()
                        return False
                    # The tool returned before cooperative cancellation could
                    # stop it. Preserve its truthful receipt/evidence, then let
                    # the runner observe cancellation and emit normal cleanup.
                    session.commit()
                    return True

                session.rollback()
                raise RuntimeError(
                    "playbook worker lease lost; execution stopped without retry"
                )

            execute_pending_run(
                session,
                run_id=run_id,
                executor=executor,
                progress_commit=commit_progress,
            )
            if lease_id is not None:
                release(
                    session,
                    lease_id=lease_id,
                    reason="playbook run completed",
                )
            session.commit()
        except Exception:
            session.rollback()
            logger.exception("playbook_worker.execute_failed", run_id=run_id_str)
            # Best-effort mark the run as failed so it doesn't dangle in
            # ``running``. Uses a fresh session so we don't inherit the
            # aborted transaction.
            try:
                s2 = self._session_factory()
            except Exception:
                logger.exception("playbook_worker.finalize_session_unavailable")
                return
            try:
                row = s2.get(PlaybookRun, uuid.UUID(run_id_str))
                failed_at = datetime.now(tz=UTC)
                if row is not None and row.status is PlaybookRunStatus.running:
                    row.status = PlaybookRunStatus.failed
                    row.completed_at = failed_at
                    if not row.last_error:
                        row.last_error = "worker exception during execute"
                    row.worker_id = None
                    row.worker_heartbeat_at = None
                interrupted = list(
                    s2.query(PlaybookStepExecution).filter(
                        PlaybookStepExecution.playbook_run_id
                        == uuid.UUID(run_id_str),
                        PlaybookStepExecution.status
                        == PlaybookStepExecutionStatus.running,
                    )
                )
                for receipt in interrupted:
                    receipt.status = PlaybookStepExecutionStatus.failed
                    receipt.completed_at = failed_at
                    receipt.duration_ms = max(
                        0,
                        round((failed_at - receipt.started_at).total_seconds() * 1000),
                    )
                    receipt.error = "worker interrupted during step execution"
                if lease_id is not None:
                    from app.services.mcp_lease import release

                    release(
                        s2,
                        lease_id=lease_id,
                        reason="playbook worker stopped before normal cleanup",
                    )
                s2.commit()
            except Exception:
                s2.rollback()
                logger.exception("playbook_worker.finalize_failed", run_id=run_id_str)
            finally:
                s2.close()
        finally:
            if heartbeat_stop is not None:
                heartbeat_stop.set()
            if heartbeat_thread is not None:
                heartbeat_thread.join(
                    timeout=max(1.0, self._heartbeat_interval + 1.0)
                )
            session.close()

    def run_once(self) -> bool:
        """One claim+execute cycle. Returns True if work was done, False if
        the queue was empty."""
        self._recover_abandoned()
        claim = self._claim()
        if claim is None:
            return False
        run_id, kind = claim
        logger.info("playbook_worker.execute_start", run_id=run_id, executor=kind.value)
        self._execute(run_id, kind)
        logger.info("playbook_worker.execute_done", run_id=run_id)
        return True

    def run_forever(self, stop_event: threading.Event) -> None:
        """Poll loop. Idle → ``stop_event.wait(poll_interval)`` so SIGTERM
        breaks out promptly; busy → immediate next iteration so a backlog
        drains without idle delay."""
        logger.info("playbook_worker.start", interval_seconds=self._poll)
        while not stop_event.is_set():
            did_work = self.run_once()
            if not did_work:
                stop_event.wait(self._poll)
        logger.info("playbook_worker.stop")
