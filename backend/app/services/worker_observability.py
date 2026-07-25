"""Durable, redacted worker process and component telemetry."""

from __future__ import annotations

import json
import os
import socket
import threading
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import WorkerComponent, WorkerInstance, WorkerOperationalEvent
from app.services.redaction import redact_sensitive_text

logger = structlog.get_logger(__name__)
SessionFactory = Callable[[], Session]
_MAX_ERROR_CHARS = 1000
_MAX_DETAILS_BYTES = 8192


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _safe_message(value: object) -> str:
    redacted = redact_sensitive_text(str(value), max_chars=_MAX_ERROR_CHARS)
    return redacted or "worker operation failed"


def _redact_details(value: Any, *, depth: int = 0) -> Any:
    if depth >= 12:
        return "[maximum depth reached]"
    if isinstance(value, Mapping):
        return {
            str(key): (
                "[REDACTED]"
                if any(
                    part in str(key).lower().replace("-", "_")
                    for part in (
                        "authorization",
                        "cookie",
                        "credential",
                        "password",
                        "secret",
                        "token",
                        "api_key",
                    )
                )
                else _redact_details(item, depth=depth + 1)
            )
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_redact_details(item, depth=depth + 1) for item in value]
    if isinstance(value, str):
        return redact_sensitive_text(value) or ""
    if value is None or isinstance(value, (int, float, bool)):
        return value
    return redact_sensitive_text(str(value)) or ""


def _safe_details(value: dict[str, Any] | None) -> dict[str, Any]:
    redacted = _redact_details(value or {})
    if not isinstance(redacted, dict):
        return {"value": str(redacted)[:_MAX_ERROR_CHARS]}
    encoded = json.dumps(redacted, default=str, separators=(",", ":"))
    if len(encoded.encode("utf-8")) <= _MAX_DETAILS_BYTES:
        return redacted
    return {"truncated": True, "preview": encoded[:4000]}


class WorkerRuntime:
    """Thread-safe facade that writes telemetry through fresh short sessions."""

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        role: str,
        concurrency: int,
        health_file: str | None = None,
    ) -> None:
        self._session_factory = session_factory
        self.id = uuid.uuid4()
        self.role = role
        self.concurrency = concurrency
        self._health_file = Path(
            health_file or os.getenv("RTD_WORKER_HEALTH_FILE", f"/tmp/rtd-{role}-worker-id")
        )
        self._components: dict[tuple[str, int], uuid.UUID] = {}
        self._lock = threading.Lock()

    def start(self) -> None:
        ts = _now()
        session = self._session_factory()
        try:
            session.add(
                WorkerInstance(
                    id=self.id,
                    role=self.role,
                    hostname=socket.gethostname(),
                    pid=os.getpid(),
                    deployment=os.getenv("CONTAINER_APP_REVISION") or os.getenv("HOSTNAME"),
                    version=os.getenv("RTD_BUILD_REVISION"),
                    concurrency=self.concurrency,
                    started_at=ts,
                    heartbeat_at=ts,
                    details={},
                )
            )
            session.commit()
        finally:
            session.close()
        self._health_file.write_text(str(self.id), encoding="utf-8")

    def heartbeat(self) -> bool:
        session = self._session_factory()
        try:
            row = session.get(WorkerInstance, self.id)
            if row is None or row.stopped_at is not None:
                return False
            row.heartbeat_at = _now()
            session.commit()
            return True
        except Exception:
            session.rollback()
            logger.exception("worker_runtime.heartbeat_failed", worker_id=str(self.id))
            return False
        finally:
            session.close()

    def component_started(
        self,
        name: str,
        *,
        slot: int = 0,
        owner_token: str | None = None,
    ) -> uuid.UUID | None:
        ts = _now()
        session = self._session_factory()
        try:
            row = WorkerComponent(
                worker_instance_id=self.id,
                name=name,
                slot=slot,
                owner_token=owner_token,
                state="idle" if name == "playbook-lane" else "running",
                started_at=ts,
                heartbeat_at=ts,
            )
            session.add(row)
            session.commit()
            with self._lock:
                self._components[(name, slot)] = row.id
            return row.id
        except Exception:
            session.rollback()
            logger.exception(
                "worker_runtime.component_start_failed",
                worker_id=str(self.id),
                component=name,
                slot=slot,
            )
            return None
        finally:
            session.close()

    def component_heartbeat(
        self,
        name: str,
        *,
        slot: int = 0,
        state: str | None = None,
        current_run_id: uuid.UUID | None = None,
        clear_current_run: bool = False,
    ) -> bool:
        component_id = self._component_id(name, slot)
        if component_id is None:
            return False
        session = self._session_factory()
        try:
            row = session.get(WorkerComponent, component_id)
            if row is None:
                return False
            row.heartbeat_at = _now()
            if state is not None:
                row.state = state
            if current_run_id is not None or clear_current_run:
                row.current_run_id = current_run_id
            session.commit()
            return True
        except Exception:
            session.rollback()
            logger.exception(
                "worker_runtime.component_heartbeat_failed",
                worker_id=str(self.id),
                component=name,
                slot=slot,
            )
            return False
        finally:
            session.close()

    def component_failed(
        self,
        name: str,
        error: object,
        *,
        slot: int = 0,
        current_run_id: uuid.UUID | None = None,
    ) -> None:
        message = _safe_message(error)
        component_id = self._component_id(name, slot)
        session = self._session_factory()
        try:
            if component_id is not None:
                row = session.get(WorkerComponent, component_id)
                if row is not None:
                    row.state = "failed"
                    row.last_error = message
                    row.last_error_at = _now()
                    row.heartbeat_at = row.last_error_at
            session.add(
                WorkerOperationalEvent(
                    worker_instance_id=self.id,
                    component=name,
                    slot=slot,
                    occurred_at=_now(),
                    severity="critical",
                    event_type="thread.crashed",
                    message=message,
                    playbook_run_id=current_run_id,
                    details={},
                )
            )
            session.commit()
        except Exception:
            session.rollback()
            logger.exception(
                "worker_runtime.failure_persist_failed",
                worker_id=str(self.id),
                component=name,
                slot=slot,
            )
        finally:
            session.close()

    def record_event(
        self,
        *,
        event_type: str,
        message: object,
        severity: str = "error",
        component: str | None = None,
        slot: int | None = None,
        engagement_id: uuid.UUID | None = None,
        playbook_run_id: uuid.UUID | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        session = self._session_factory()
        try:
            session.add(
                WorkerOperationalEvent(
                    worker_instance_id=self.id,
                    component=component,
                    slot=slot,
                    occurred_at=_now(),
                    severity=severity,
                    event_type=event_type[:80],
                    message=_safe_message(message),
                    engagement_id=engagement_id,
                    playbook_run_id=playbook_run_id,
                    details=_safe_details(details),
                )
            )
            session.commit()
        except Exception:
            session.rollback()
            logger.exception(
                "worker_runtime.event_persist_failed",
                worker_id=str(self.id),
                event_type=event_type,
            )
        finally:
            session.close()

    def component_stopped(self, name: str, *, slot: int = 0) -> None:
        component_id = self._component_id(name, slot)
        if component_id is None:
            return
        session = self._session_factory()
        try:
            row = session.get(WorkerComponent, component_id)
            if row is not None:
                row.state = "stopped"
                row.stopped_at = _now()
                row.heartbeat_at = row.stopped_at
                row.current_run_id = None
            session.commit()
        except Exception:
            session.rollback()
            logger.exception("worker_runtime.component_stop_failed")
        finally:
            session.close()

    def stop(self, reason: str = "graceful shutdown") -> None:
        session = self._session_factory()
        try:
            row = session.get(WorkerInstance, self.id)
            if row is not None:
                row.stopped_at = _now()
                row.heartbeat_at = row.stopped_at
                row.stop_reason = _safe_message(reason)
            session.commit()
        except Exception:
            session.rollback()
            logger.exception("worker_runtime.stop_failed", worker_id=str(self.id))
        finally:
            session.close()

    def _component_id(self, name: str, slot: int) -> uuid.UUID | None:
        with self._lock:
            component_id = self._components.get((name, slot))
        if component_id is not None:
            return component_id
        session = self._session_factory()
        try:
            component_id = session.execute(
                select(WorkerComponent.id).where(
                    WorkerComponent.worker_instance_id == self.id,
                    WorkerComponent.name == name,
                    WorkerComponent.slot == slot,
                )
            ).scalar_one_or_none()
            if component_id is not None:
                with self._lock:
                    self._components[(name, slot)] = component_id
            return component_id
        finally:
            session.close()
