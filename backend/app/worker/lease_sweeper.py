"""Periodic sweeper for expired MCP leases.

Walks ``mcp_leases`` for rows with ``status='active'`` and
``expires_at < now()``, flipping them to ``status='expired'``. The
per-request ``mcp_lease.validate_token`` already rejects expired leases
at the MCP server, so this sweeper exists for accounting cleanliness
(the Costs and lease-state views otherwise carry stale "active" rows
past their TTL) rather than for security correctness.

Lives in the worker process as a daemon thread, same shape as
``StrategicConsumer``. Cadence is configurable via
``settings.lease_sweep_interval`` (default 300s).
"""
from __future__ import annotations

import threading
from collections.abc import Callable

import structlog
from sqlalchemy.orm import Session

from app.services import mcp_lease

logger = structlog.get_logger(__name__)

SessionFactory = Callable[[], Session]


class LeaseSweeperThread:
    """Periodic ``mcp_lease.sweep_expired`` runner."""

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        interval_seconds: float,
    ) -> None:
        self._session_factory = session_factory
        self._interval = interval_seconds

    def run_once(self) -> int:
        """One sweep pass. Returns the number of leases flipped.

        Catches and logs DB errors so a transient Postgres blip doesn't
        kill the sweeper thread. Returns 0 on error. The outer try covers
        session-factory failures too (pool exhausted, network blip
        before we ever get a session).
        """
        try:
            session = self._session_factory()
        except Exception:
            logger.exception("lease_sweeper.session_unavailable")
            return 0
        try:
            count = mcp_lease.sweep_expired(session)
            session.commit()
            return count
        except Exception:
            session.rollback()
            logger.exception("lease_sweeper.run_failed")
            return 0
        finally:
            session.close()

    def run_forever(self, stop_event: threading.Event) -> None:
        """Sweep loop. ``stop_event.wait(interval)`` blocks while idle so
        SIGTERM/SIGINT breaks out promptly instead of waiting the full
        interval."""
        logger.info("lease_sweeper.start", interval_seconds=self._interval)
        while not stop_event.is_set():
            self.run_once()
            stop_event.wait(self._interval)
        logger.info("lease_sweeper.stop")
