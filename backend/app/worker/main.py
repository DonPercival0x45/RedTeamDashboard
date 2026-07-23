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
from collections.abc import Mapping
from typing import Any

import redis as redis_lib
import structlog

from app.agents import StrategicAgent
from app.core.config import settings
from app.core.logging import configure_logging
from app.db.session import SessionLocal
from app.orchestrator import build_graph
from app.orchestrator.llm import default_provider_model, make_llm
from app.worker.authz import make_db_authorizer
from app.worker.checkpoint import build_postgres_checkpointer
from app.worker.consumer import StreamConsumer
from app.worker.discord_bot import DiscordBotThread
from app.worker.lease_sweeper import LeaseSweeperThread
from app.worker.outbox_relay import CommandOutboxRelay
from app.worker.playbook_worker import PlaybookWorkerThread
from app.worker.runner import RunRunner
from app.worker.strategic_consumer import StrategicConsumer

log = structlog.get_logger()


def main() -> None:
    configure_logging(settings.env)
    log.info("worker.start", env=settings.env, redis=settings.redis_url)

    # Stage 3+1: the Stage 1.5 local-execution fallback is gone. Every
    # worker run executes through the MCP server, which means the worker
    # needs an API key to talk to it. Fail fast at boot rather than per-
    # run so the operator sees the misconfiguration immediately.
    if not settings.worker_mcp_api_key:
        raise RuntimeError(
            "WORKER_MCP_API_KEY is required — the Stage 1.5 local-execution "
            "fallback was ripped. Mint a cli-scoped API key, stash it in "
            "Key Vault as worker-mcp-api-key, and surface it as the env var."
        )

    redis_client = redis_lib.Redis.from_url(settings.redis_url, decode_responses=True)
    checkpointer = build_postgres_checkpointer()
    authorizer = make_db_authorizer(SessionLocal)

    def graph_factory(
        model: Mapping[str, Any] | None,
        allowed_tools: list[str] | None = None,
        mcp_url: str | None = None,
        lease_token: str | None = None,
    ) -> object:
        """Build a fresh graph per run with the requested LLM and tool surface.

        Cheap — StateGraph compile is sub-millisecond. The LLM constructor
        is what costs (network handshake on first invoke), and we'd pay
        that anyway. Per-run rebuild lets each run pick its own provider.

        BYO-keys: ``api_key`` and ``endpoint`` arrive in ``model`` via the
        runner's per-envelope lookup against the kicker's ephemeral
        Redis-cached provider key.

        MCP leases: ``allowed_tools`` arrives from the runner's lease
        lookup. We filter the global tool registry down to the lease's
        curated surface AND bind only those schemas onto the LLM (so the
        agent never proposes a tool outside the lease).

        Stage 3+1: every envelope MUST carry ``mcp_url`` + ``lease_token``.
        Tactical-dispatched runs and direct ``POST /runs`` runs both mint
        leases before the envelope hits Redis. Any envelope missing
        either is a programming error; the run errors out instead of
        silently falling back to local execution.
        """
        registry = None
        if allowed_tools is not None:
            from app.orchestrator.tools import all_tools

            registry = {
                spec.name: spec
                for spec in all_tools()
                if spec.name in allowed_tools
            }

        if model and model.get("provider") and model.get("name"):
            llm = make_llm(
                str(model["provider"]),
                str(model["name"]),
                api_key=model.get("api_key"),
                endpoint=model.get("endpoint"),
                registry=registry,
            )
        else:
            provider, model_name = default_provider_model()
            llm = make_llm(provider, model_name, registry=registry)

        if not (mcp_url and lease_token):
            # The runner builds envelopes from Redis; we should never
            # see a stripped one in production. Raising here surfaces
            # the bug rather than running the agent with no executor.
            raise RuntimeError(
                "graph_factory called without mcp_url+lease_token — every "
                "run.start envelope must carry an MCP lease (Stage 3+1). "
                "Check the producer (Tactical.dispatch or POST /runs)."
            )

        from app.worker.mcp_executor import make_mcp_executor

        mcp_executor = make_mcp_executor(
            mcp_url,
            lease_token,
            api_key=settings.worker_mcp_api_key,
        )

        return build_graph(
            llm=llm,
            checkpointer=checkpointer,
            authorizer=authorizer,
            registry=registry,
            mcp_executor=mcp_executor,
        )

    runner = RunRunner(
        graph_factory=graph_factory,
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

    # Relay commands whose domain transaction committed while Redis was
    # unavailable. Multiple worker replicas coordinate through row locks.
    outbox_relay = CommandOutboxRelay(
        redis_client=redis_client,
        session_factory=SessionLocal,
    )
    outbox_thread = threading.Thread(
        target=outbox_relay.run_forever,
        args=(stop_event,),
        name="command-outbox-relay",
        daemon=True,
    )
    outbox_thread.start()

    # Phase 9: Strategic watcher subscribes to the outbound event stream and
    # runs on every finding.created. Lives in a sibling thread so the
    # existing run-command consumer in the main thread is untouched.
    strategic = StrategicConsumer(
        # Strategic needs the redis client so it can resolve the kicking
        # analyst's ephemeral BYO key per call (no engagement-creator
        # fallback anymore — locked 2026-06-29).
        agent=StrategicAgent(redis_client=redis_client),
        redis_client=redis_client,
        session_factory=SessionLocal,
    )
    strategic_thread = threading.Thread(
        target=strategic.run_forever,
        args=(stop_event,),
        name="strategic-watcher",
        daemon=True,
    )
    strategic_thread.start()

    # Stage 3+1.5: periodic sweep of expired MCP leases. validate_token
    # rejects expired leases at request time so this is for clean
    # accounting (Costs UI + lease-state queries), not security.
    sweeper = LeaseSweeperThread(
        session_factory=SessionLocal,
        interval_seconds=settings.lease_sweep_interval,
    )
    sweeper_thread = threading.Thread(
        target=sweeper.run_forever,
        args=(stop_event,),
        name="lease-sweeper",
        daemon=True,
    )
    sweeper_thread.start()

    # Discord bot — daemon thread; gracefully no-ops if discord.py is
    # absent OR no enabled Discord integration row exists in the DB.
    # Restart the worker after editing the integration config in the UI
    # to pick up the new token/channel.
    discord_bot = DiscordBotThread(
        session_factory=SessionLocal,
        redis_client=redis_client,
    )
    discord_thread = threading.Thread(
        target=discord_bot.run,
        args=(stop_event,),
        name="discord-bot",
        daemon=True,
    )
    discord_thread.start()

    # v3 A3c: async playbook runs. Polls playbook_runs for pending rows via
    # SELECT ... FOR UPDATE SKIP LOCKED so multiple worker replicas cooperate
    # without stepping on each other. Same daemon-thread shape as the other
    # background loops; the analyst-facing HTTP endpoint returns 202 and this
    # thread does the actual work.
    playbook_worker = PlaybookWorkerThread(session_factory=SessionLocal)
    playbook_thread = threading.Thread(
        target=playbook_worker.run_forever,
        args=(stop_event,),
        name="playbook-worker",
        daemon=True,
    )
    playbook_thread.start()

    consumer.run_forever(stop_event)
    outbox_thread.join(timeout=5.0)
    strategic_thread.join(timeout=5.0)
    sweeper_thread.join(timeout=5.0)
    discord_thread.join(timeout=5.0)
    playbook_thread.join(timeout=5.0)
    sys.exit(0)


if __name__ == "__main__":
    main()
