"""Durable worker for analyst-triggered v3 intelligence jobs."""
from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.intelligence import run_intelligence_analysis
from app.models import (
    ActorType,
    AgentExecution,
    AgentExecutionStatus,
    AgentName,
    AgentPromptMode,
    AuditLog,
)
from app.services import memory
from app.services.agent_model_resolver import resolve_llm_for_mode
from app.services.milestone_runner import acquire_engagement_memory_lock

logger = structlog.get_logger(__name__)
SessionFactory = Callable[[], Session]


class IntelligenceWorkerThread:
    """Claim pending intelligence rows and execute them outside HTTP requests."""

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        redis_client: Any,
        poll_interval_seconds: float = 2.0,
    ) -> None:
        self._session_factory = session_factory
        self._redis = redis_client
        self._poll = poll_interval_seconds

    def _claim(self) -> uuid.UUID | None:
        session = self._session_factory()
        try:
            row = session.execute(
                select(AgentExecution)
                .where(
                    AgentExecution.agent == AgentName.engagement_strategist,
                    AgentExecution.status == AgentExecutionStatus.pending,
                    AgentExecution.input["durable_job"].as_boolean().is_(True),
                )
                .order_by(AgentExecution.started_at, AgentExecution.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            ).scalar_one_or_none()
            if row is None:
                session.commit()
                return None
            row.status = AgentExecutionStatus.running
            row.started_at = datetime.now(tz=UTC)
            session.commit()
            return row.id
        except Exception:
            session.rollback()
            logger.exception("intelligence_worker.claim_failed")
            return None
        finally:
            session.close()

    def _mark_failed(self, execution_id: uuid.UUID, error: Exception | str) -> None:
        session = self._session_factory()
        try:
            row = session.get(AgentExecution, execution_id)
            if row is not None and row.status in {
                AgentExecutionStatus.pending,
                AgentExecutionStatus.running,
            }:
                row.status = AgentExecutionStatus.failed
                row.error = str(error)[:2000]
                row.completed_at = datetime.now(tz=UTC)
                session.commit()
        except Exception:
            session.rollback()
            logger.exception(
                "intelligence_worker.finalize_failed", execution_id=str(execution_id)
            )
        finally:
            session.close()

    def _execute(self, execution_id: uuid.UUID) -> None:
        session = self._session_factory()
        try:
            execution = session.get(AgentExecution, execution_id)
            if execution is None or execution.status is not AgentExecutionStatus.running:
                return
            payload = execution.input or {}
            if execution.engagement_id is None:
                raise RuntimeError("durable intelligence job has no engagement")
            mode = AgentPromptMode(str(payload.get("mode")))
            acting_user_id = uuid.UUID(str(payload.get("acting_user_id")))

            acquire_engagement_memory_lock(session, execution.engagement_id)
            if mode is AgentPromptMode.coverage_review:
                memory.compact(session, engagement_id=execution.engagement_id)

            llm, provider, model_name = resolve_llm_for_mode(
                session,
                redis_client=self._redis,
                user_id=acting_user_id,
                engagement_id=execution.engagement_id,
                mode=mode,
            )
            _parsed, execution = run_intelligence_analysis(
                session,
                engagement_id=execution.engagement_id,
                mode=mode,
                acting_user_id=acting_user_id,
                llm=llm,
                model_provider=provider,
                model_name=model_name,
                trigger=execution.trigger,
                execution=execution,
            )
            session.add(
                AuditLog(
                    engagement_id=execution.engagement_id,
                    actor_type=ActorType.user,
                    actor_id=str(acting_user_id),
                    event_type="intelligence.completed",
                    payload={
                        "execution_id": str(execution.id),
                        "mode": mode.value,
                        "status": execution.status.value,
                        "manual": True,
                    },
                )
            )
            session.commit()
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            logger.exception(
                "intelligence_worker.execute_failed", execution_id=str(execution_id)
            )
            self._mark_failed(execution_id, exc)
        finally:
            session.close()

    def run_once(self) -> bool:
        execution_id = self._claim()
        if execution_id is None:
            return False
        logger.info("intelligence_worker.execute_start", execution_id=str(execution_id))
        self._execute(execution_id)
        logger.info("intelligence_worker.execute_done", execution_id=str(execution_id))
        return True

    def run_forever(self, stop_event: threading.Event) -> None:
        logger.info("intelligence_worker.start", interval_seconds=self._poll)
        while not stop_event.is_set():
            if not self.run_once():
                stop_event.wait(self._poll)
        logger.info("intelligence_worker.stop")
