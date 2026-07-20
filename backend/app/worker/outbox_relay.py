"""Worker-side relay for pending transactional command-outbox rows."""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

import structlog
from sqlalchemy.orm import Session

from app.services.command_outbox import publish_pending_batch

logger = structlog.get_logger(__name__)
SessionFactory = Callable[[], Session]


class CommandOutboxRelay:
    def __init__(
        self,
        *,
        redis_client: Any,
        session_factory: SessionFactory,
        interval_seconds: float = 1.0,
        batch_size: int = 50,
    ) -> None:
        self._redis = redis_client
        self._session_factory = session_factory
        self._interval = interval_seconds
        self._batch_size = batch_size

    def run_once(self) -> int:
        session = self._session_factory()
        try:
            return publish_pending_batch(
                session, self._redis, limit=self._batch_size
            )
        except Exception:
            session.rollback()
            logger.exception("command_outbox.relay_failed")
            return 0
        finally:
            session.close()

    def run_forever(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            published = self.run_once()
            if published == 0:
                stop_event.wait(self._interval)
            else:
                # Drain backlog promptly while still yielding to other threads.
                time.sleep(0)
