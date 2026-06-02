"""structlog configuration.

- ``env=local``: human-readable colorized output (good for `docker compose logs`)
- otherwise   : single-line JSON per event (greppable, parseable by Log
                Analytics / Loki / etc. when this ships to AKS)

Call ``configure_logging(settings.env)`` once at process start
(``app/main.py`` for the API, ``app/worker/main.py`` for the consumer).
"""
from __future__ import annotations

import logging

import structlog


def configure_logging(env: str) -> None:
    timestamper = structlog.processors.TimeStamper(fmt="iso")

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.dev.set_exc_info,
        timestamper,
    ]

    renderer: structlog.types.Processor
    if env == "local":
        renderer = structlog.dev.ConsoleRenderer(colors=True)
    else:
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Bring stdlib logging in line so anything that bypasses structlog (e.g.
    # uvicorn access logs) is also captured at INFO and routed to the same
    # processor chain.
    logging.basicConfig(level=logging.INFO, format="%(message)s")
