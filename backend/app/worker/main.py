"""Redis Streams consumer entrypoint.

Boots the compiled OSINT graph (ChatAnthropic-backed by default, swappable
via ``LLM_PROVIDER``), wires it into a ``RunRunner`` with a Postgres-backed
checkpointer so in-flight runs survive restarts, and spins the
``StreamConsumer`` poll loop until SIGTERM/SIGINT.
"""
from __future__ import annotations

import signal
import sys
import threading

import redis as redis_lib
import structlog

from app.core.config import settings
from app.core.logging import configure_logging
from app.db.session import SessionLocal
from app.orchestrator import build_graph
from app.orchestrator.llm import default_llm
from app.worker.authz import make_db_authorizer
from app.worker.checkpoint import build_postgres_checkpointer
from app.worker.consumer import StreamConsumer
from app.worker.runner import RunRunner

log = structlog.get_logger()


def main() -> None:
    configure_logging(settings.env)
    log.info("worker.start", env=settings.env, redis=settings.redis_url)

    redis_client = redis_lib.Redis.from_url(settings.redis_url, decode_responses=True)
    checkpointer = build_postgres_checkpointer()
    graph = build_graph(
        llm=default_llm(),
        checkpointer=checkpointer,
        authorizer=make_db_authorizer(SessionLocal),
    )
    runner = RunRunner(
        graph=graph,
        redis_client=redis_client,
        session_factory=SessionLocal,
    )
    consumer = StreamConsumer(
        runner=runner,
        redis_client=redis_client,
        session_factory=SessionLocal,
    )

    stop_event = threading.Event()

    def _shutdown(signum: int, _frame: object) -> None:
        log.info("worker.shutdown", signal=signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    consumer.run_forever(stop_event)
    sys.exit(0)


if __name__ == "__main__":
    main()
