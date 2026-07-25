"""Small process-level supervision helpers shared by worker entrypoints."""

from __future__ import annotations

import threading
from collections.abc import Callable

from app.core.config import settings
from app.services.worker_observability import WorkerRuntime


def heartbeat_loop(
    runtime: WorkerRuntime,
    stop_event: threading.Event,
    critical_failure: threading.Event,
) -> None:
    """Maintain process freshness and fail closed after a sustained DB outage."""
    failures = 0
    runtime.component_started("process-supervisor")
    try:
        while not stop_event.is_set():
            ok = runtime.heartbeat()
            if ok:
                failures = 0
                runtime.component_heartbeat("process-supervisor", state="running")
            else:
                failures += 1
                if failures >= 6:
                    runtime.record_event(
                        event_type="heartbeat.exhausted",
                        severity="critical",
                        component="process-supervisor",
                        message="worker heartbeat failed six consecutive times",
                    )
                    critical_failure.set()
                    stop_event.set()
                    return
            stop_event.wait(settings.worker_heartbeat_interval)
    finally:
        runtime.component_stopped("process-supervisor")


def supervised_target(
    *,
    runtime: WorkerRuntime,
    component: str,
    target: Callable[[], None],
    stop_event: threading.Event,
    critical_failure: threading.Event,
    slot: int = 0,
    critical: bool = True,
) -> None:
    """Expose an unexpected component return/crash and stop for Docker restart."""
    runtime.component_started(component, slot=slot)
    try:
        target()
        if not stop_event.is_set():
            raise RuntimeError("worker component exited unexpectedly")
    except BaseException as exc:
        runtime.component_failed(component, exc, slot=slot)
        if critical:
            critical_failure.set()
            stop_event.set()
    finally:
        runtime.component_stopped(component, slot=slot)
