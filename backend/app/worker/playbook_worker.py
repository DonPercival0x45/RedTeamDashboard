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
from collections.abc import Callable

import structlog
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import PlaybookExecutorKind
from app.services.playbook import (
    InternalExecutor,
    claim_next_pending,
    execute_pending_run,
)
from app.services.playbook.executor import MCPExecutor, PlaybookExecutor

logger = structlog.get_logger(__name__)

SessionFactory = Callable[[], Session]


class PlaybookWorkerThread:
    """Polls for pending playbook runs and drives them to completion."""

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        poll_interval_seconds: float = 2.0,
    ) -> None:
        self._session_factory = session_factory
        self._poll = poll_interval_seconds

    def _build_executor(self, kind: PlaybookExecutorKind) -> PlaybookExecutor:
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
            run = claim_next_pending(session)
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

    def _execute(self, run_id_str: str, kind: PlaybookExecutorKind) -> None:
        """Drive the claimed run in a separate transaction.

        Any exception here transitions the run to ``failed`` with the
        error message so a botched executor doesn't leave a run stuck at
        ``running`` forever. The runner's per-step ``try/except`` already
        catches executor exceptions; this outer catch is for anything
        outside the step loop (DB glitch, model reload failure).
        """
        import uuid as _uuid

        try:
            session = self._session_factory()
        except Exception:
            logger.exception("playbook_worker.execute_session_unavailable")
            return
        try:
            executor = self._build_executor(kind)
            execute_pending_run(
                session,
                run_id=_uuid.UUID(run_id_str),
                executor=executor,
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
                from datetime import UTC, datetime

                from app.models import PlaybookRun, PlaybookRunStatus

                row = s2.get(PlaybookRun, _uuid.UUID(run_id_str))
                if row is not None and row.status is PlaybookRunStatus.running:
                    row.status = PlaybookRunStatus.failed
                    row.completed_at = datetime.now(tz=UTC)
                    if not row.last_error:
                        row.last_error = "worker exception during execute"
                s2.commit()
            except Exception:
                s2.rollback()
                logger.exception("playbook_worker.finalize_failed", run_id=run_id_str)
            finally:
                s2.close()
        finally:
            session.close()

    def run_once(self) -> bool:
        """One claim+execute cycle. Returns True if work was done, False if
        the queue was empty."""
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
