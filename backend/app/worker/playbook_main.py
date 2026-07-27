"""Dedicated, supervised playbook execution pool."""

from __future__ import annotations

import signal
import threading
import uuid

import redis as redis_lib
import structlog

from app.core.config import settings
from app.core.logging import configure_logging
from app.db.session import SessionLocal
from app.services.worker_observability import WorkerRuntime
from app.worker.playbook_worker import PlaybookWorkerThread
from app.worker.supervisor import heartbeat_loop

log = structlog.get_logger(__name__)


def main() -> None:
    configure_logging(settings.env)
    if not settings.worker_mcp_api_key:
        raise RuntimeError("WORKER_MCP_API_KEY is required by the playbook worker")

    concurrency = settings.playbook_worker_concurrency
    runtime = WorkerRuntime(
        session_factory=SessionLocal,
        role="playbook",
        concurrency=concurrency,
    )
    runtime.start()
    stop_event = threading.Event()
    critical_failure = threading.Event()

    def shutdown(signum: int, _frame: object) -> None:
        log.info("playbook_pool.shutdown", signal=signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    heartbeat_thread = threading.Thread(
        target=heartbeat_loop,
        args=(runtime, stop_event, critical_failure),
        name="worker-process-heartbeat",
        daemon=True,
    )
    heartbeat_thread.start()

    redis_client = redis_lib.Redis.from_url(settings.redis_url, decode_responses=True)
    lane_threads: list[threading.Thread] = []

    def run_lane(slot: int) -> None:
        owner_token = f"{runtime.id}:{slot}:{uuid.uuid4()}"
        lane = PlaybookWorkerThread(
            session_factory=SessionLocal,
            redis_client=redis_client,
            worker_id=owner_token,
            runtime=runtime,
            slot=slot,
        )
        try:
            lane.run_forever(stop_event)
            if not stop_event.is_set():
                raise RuntimeError("playbook lane exited unexpectedly")
        except BaseException as exc:
            runtime.component_failed("playbook-lane", exc, slot=slot)
            critical_failure.set()
            stop_event.set()

    for slot in range(concurrency):
        thread = threading.Thread(
            target=run_lane,
            args=(slot,),
            name=f"playbook-lane-{slot + 1}",
            # A bounded graceful join happens below. Daemon mode ensures a
            # hung external call cannot keep an unhealthy container alive
            # forever after the supervisor has decided to exit.
            daemon=True,
        )
        lane_threads.append(thread)
        thread.start()

    log.info(
        "playbook_pool.start",
        worker_id=str(runtime.id),
        concurrency=concurrency,
        global_limit=settings.playbook_global_concurrency,
        per_engagement_limit=settings.playbook_per_engagement_concurrency,
    )

    try:
        while not stop_event.wait(1.0):
            if any(not thread.is_alive() for thread in lane_threads):
                critical_failure.set()
                stop_event.set()
                break
    finally:
        stop_event.set()
        for thread in lane_threads:
            thread.join(timeout=5.0)
        heartbeat_thread.join(timeout=5.0)
        runtime.stop(
            "critical component failure" if critical_failure.is_set() else "graceful shutdown"
        )

    raise SystemExit(1 if critical_failure.is_set() else 0)


if __name__ == "__main__":
    main()
