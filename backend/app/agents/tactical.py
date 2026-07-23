"""Tactical manager — dispatches agent-eligible Tasks to the worker.

This agent dispatches enumeration and scanning tasks during **authorized security
engagements**. It enforces the charter invariant that **agents scan, analysts validate**.

**Charter:** Only agent-eligible tasks (scan/enum) are dispatched. Validation and
proof-of-concept work (``TaskKind.exploit``) is **analyst-only** — refused at the
service boundary.

Slice 1 (Phase 9): deterministic dispatcher. Pulls (tool, target) from
``task.payload`` (set by Strategic when the suggestion was accepted) and
publishes a ``run.start`` envelope on the engagement's inbound stream. The
worker's existing graph + approval gate handles everything from there.

HARD INVARIANT: ``TaskKind.exploit`` is refused at the service boundary. The
CHARTER decided agents scan, analysts exploit. ``TacticalRefusedExploit``
is raised so the API layer can map it to a 4xx and the caller knows the
refusal is by design, not by misconfiguration.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import String, cast, select, update
from sqlalchemy.orm import Session

from app.models import (
    AgentExecution,
    AgentExecutionStatus,
    AgentName,
    AgentTrigger,
    OwnerEligibility,
    Task,
    TaskKind,
    TaskStatus,
)
from app.orchestrator.llm import default_provider_model
from app.orchestrator.tools import get_tool
from app.runs.streams import inbound_stream, run_model_key, store_run_model
from app.services.agent_model_resolver import resolve_agent_model
from app.services.command_outbox import enqueue_command

logger = structlog.get_logger(__name__)


# Run-level dedup window: a completed Tactical run for the exact (tool,
# target) within this window short-circuits a re-dispatch. Passive OSINT
# results don't change minute-to-minute, so re-scanning the same target the
# same day just burns tokens + stacks duplicate findings.
_RESCAN_DEDUP_WINDOW = timedelta(hours=24)


class TacticalSkippedV3(Exception):
    """Raised by ``TacticalAgent.dispatch`` when the target engagement runs on
    v3 intelligence and ``enforce_v3_playbook_only`` is on.

    v3-converted engagements route OSINT through the playbook runner instead
    of Tactical's per-finding lease-policy LLM (v3 Convergence C6a). Callers
    should mark the task ``skipped`` (or leave it pending for analyst review)
    rather than treat this as an error. ``last_error`` on any surrounding
    ``AgentExecution`` receives the reason so the Costs tab surfaces the skip.
    """


class TacticalRefusedExploit(Exception):
    """Tactical was asked to dispatch a kind=exploit task. Agents scan,
    analysts exploit (CHARTER invariant). The HTTP layer maps this to 400
    so the analyst sees a deliberate refusal, not a generic error."""


class TacticalAlreadyScanned(Exception):
    """Tactical was asked to dispatch a (tool, target) a completed run already
    covered within the dedup window. Raised so the caller marks the task done
    (deduped) against the prior run instead of re-dispatching — the guardrail
    against "the same stuff over and over"."""

    def __init__(self, prior_execution_id: uuid.UUID, prior_thread_id: uuid.UUID) -> None:
        self.prior_execution_id = prior_execution_id
        self.prior_thread_id = prior_thread_id
        super().__init__(f"already scanned by worker thread {prior_thread_id}")


class TacticalAgent:
    """Dispatcher that turns an accepted Task into a worker run."""

    def __init__(self, redis_client: Any) -> None:
        self._redis = redis_client

    def dispatch(
        self,
        session: Session,
        *,
        task: Task,
        acting_user_id: uuid.UUID,
        trigger: AgentTrigger = AgentTrigger.manual,
    ) -> uuid.UUID:
        """Dispatch ``task`` as a worker run; return the new ``thread_id``.

        ``acting_user_id`` is the analyst who triggered this dispatch
        (accepted the suggestion, clicked Start). Stamped onto the worker
        envelope so the worker resolves the BYO key against the kicker's
        Redis cache — not the engagement creator's.

        Caller commits the session. The function mutates ``task`` (status,
        dispatched_at, run_id) and adds an ``AgentExecution`` row to record
        the dispatch decision.
        """
        if task.kind == TaskKind.exploit:
            raise TacticalRefusedExploit(
                "tactical refuses exploit tasks — agents scan, analysts exploit"
            )
        if task.owner_eligibility == OwnerEligibility.analyst:
            raise ValueError(
                f"task {task.id} is analyst-only; tactical cannot dispatch"
            )
        if task.status != TaskStatus.pending:
            raise ValueError(
                f"task {task.id} is already {task.status.value}; refusing to redispatch"
            )

        # v3 Convergence C6a — v3 engagements route OSINT through the playbook
        # runner. Skip Tactical dispatch outright so we don't burn tokens on a
        # lease-policy LLM call that would be for a legacy execution path. The
        # engagement's Playbooks tab handles the same work deterministically.
        from app.core.config import settings as _config_settings
        from app.models import Engagement as _Engagement
        from app.models import EngagementArchitecture as _EngagementArchitecture

        engagement = session.get(_Engagement, task.engagement_id)
        if (
            _config_settings.enforce_v3_playbook_only
            and engagement is not None
            and engagement.intelligence_architecture is _EngagementArchitecture.v3
        ):
            logger.info(
                "tactical.skipped_v3",
                task_id=str(task.id),
                engagement_id=str(engagement.id),
                engagement_slug=engagement.slug,
            )
            raise TacticalSkippedV3(
                f"engagement {engagement.slug} is on v3 intelligence; "
                "playbook runner handles OSINT dispatch"
            )

        tool_name = task.payload.get("tool")
        target = task.payload.get("target")
        if not (tool_name and target):
            raise ValueError(
                f"task {task.id} payload missing tool/target: {task.payload!r}"
            )

        spec = get_tool(tool_name)
        if spec is None:
            raise ValueError(f"task {task.id} references unknown tool {tool_name!r}")

        # Run-level dedup: a completed run already covered this exact
        # (tool, target) within the window. Re-scanning a passive probe
        # minutes/hours apart just burns tokens + stacks duplicate findings —
        # skip it and let the caller mark the task done against the prior run.
        prior = session.execute(
            select(AgentExecution.id, Task.run_id)
            .join(
                Task,
                cast(Task.run_id, String) == AgentExecution.output["thread_id"].astext,
            )
            .where(
                AgentExecution.engagement_id == task.engagement_id,
                AgentExecution.agent == AgentName.tactical,
                AgentExecution.status == AgentExecutionStatus.completed,
                AgentExecution.input["tool"].astext == tool_name,
                AgentExecution.input["target"].astext == target,
                Task.engagement_id == task.engagement_id,
                Task.status == TaskStatus.completed,
                Task.completed_at >= datetime.now(tz=UTC) - _RESCAN_DEDUP_WINDOW,
            )
            .order_by(Task.completed_at.desc())
            .limit(1)
        ).one_or_none()
        if prior is not None:
            prior_execution_id, prior_thread_id = prior
            raise TacticalAlreadyScanned(prior_execution_id, prior_thread_id)

        prompt = (
            f"Use the {tool_name} tool with {spec.target_arg}={target!r}. "
            "Report exactly what the tool returns; do not call any other tool."
        )

        # v1.24.0: honor the acting analyst's Settings > Configurations
        # pinning for this engagement + Tactical role. Falls back to the
        # user's default_model, then to the process-wide default.
        resolved = resolve_agent_model(
            session,
            user_id=acting_user_id,
            engagement_id=task.engagement_id,
            role=AgentName.tactical,
        )
        if resolved is not None:
            provider, model_name = resolved
            if provider is None:
                # Fall back on the process default provider if the analyst
                # typed a bare model string we couldn't map — keeps the
                # dispatch running instead of failing.
                default_provider, _ = default_provider_model()
                provider = default_provider
        else:
            provider, model_name = default_provider_model()
        thread_id = uuid.uuid4()

        # Stage 1 of per-task MCP composition: mint a lease for this dispatch
        # so the Execution Agent gets the curated tool/context/prompt surface
        # Strategic chose for this TaskKind. The lease id is the bearer token.
        from app.agents.strategic import StrategicAgent
        from app.core.config import settings

        lease = StrategicAgent(redis_client=self._redis).provision_lease(
            session, task=task, acting_user_id=acting_user_id
        )

        # Stage 2 routing: when Strategic marked the lease as needing an
        # isolated MCP host AND the deployment has provisioned a secondary
        # scale-to-zero MCP App, point the worker there. Otherwise use the
        # colocated MCP server in the backend container. The local-dev
        # default (``aca_mcp_app_enabled=False``) collapses both paths to
        # colocated so we don't fork the local stack for an Azure-only
        # feature.
        # The FastMCP server is mounted at /mcp; the SSE endpoint inside
        # it lives at /sse, so the worker's MCP client needs the full
        # /mcp/sse path. Hitting /mcp gets 404 (no handler at the mount
        # root) once auth passes. Same path on both colocated + secondary
        # MCP Apps — the standalone entrypoint mirrors the mount.
        if (
            lease.requires_container
            and settings.aca_mcp_app_enabled
            and settings.aca_mcp_url
        ):
            mcp_url = f"{settings.aca_mcp_url.rstrip('/')}/mcp/sse"
            mcp_host = "container"
        else:
            mcp_url = f"{settings.public_base_url.rstrip('/')}/mcp/sse"
            mcp_host = "colocated"

        try:
            store_run_model(
                self._redis,
                thread_id,
                provider=provider,
                model_name=model_name,
                acting_user_id=acting_user_id,
            )
        except Exception:  # noqa: BLE001 - durable lineage is in the command
            logger.warning(
                "tactical.run_model_cache_failed",
                task_id=str(task.id),
                thread_id=str(thread_id),
            )

        now = datetime.now(tz=UTC)
        execution = AgentExecution(
            engagement_id=task.engagement_id,
            agent=AgentName.tactical,
            trigger=trigger,
            input={
                "task_id": str(task.id),
                "tool": tool_name,
                "target": target,
            },
            output={"thread_id": str(thread_id), "prompt": prompt},
            model_provider=provider,
            model_name=model_name,
            status=AgentExecutionStatus.completed,
            started_at=now,
            completed_at=now,
        )
        session.add(execution)

        # Strategic policy selection may commit its AgentExecution while the
        # LLM call is visible in Status. A concurrent analyst cancellation can
        # therefore change the Task after our initial pending check. Use a
        # compare-and-set transition so stale dispatch work can never overwrite
        # that cancellation.
        transitioned = session.execute(
            update(Task)
            .where(
                Task.id == task.id,
                Task.status == TaskStatus.pending,
            )
            .values(
                status=TaskStatus.dispatched,
                dispatched_at=now,
                completed_at=None,
                run_id=thread_id,
            )
            .execution_options(synchronize_session=False)
        )
        if transitioned.rowcount != 1:
            session.rollback()
            try:
                self._redis.delete(run_model_key(thread_id))
            except Exception:  # noqa: BLE001 — TTL is the final cleanup fallback
                logger.warning(
                    "tactical.superseded_run_model_cleanup_failed",
                    task_id=str(task.id),
                    thread_id=str(thread_id),
                )
            raise ValueError(
                f"task {task.id} changed state during dispatch; refusing to enqueue"
            )

        # Commit lease + execution + task state and the command outbox row in
        # one transaction. Redis publication happens only after the lease is
        # visible, and a Redis outage leaves a retryable pending row rather
        # than a silently stranded dispatched task.
        run_payload = {
            "type": "run.start",
            "thread_id": str(thread_id),
            "prompt": prompt,
            "model": {"provider": provider, "name": model_name},
            "mcp_url": mcp_url,
            "lease_token": str(lease.id),
            # The worker resolves the BYO key off this id at run time.
            "acting_user_id": str(acting_user_id),
        }
        enqueue_command(
            session,
            idempotency_key=f"run.start:{thread_id}",
            engagement_id=task.engagement_id,
            stream_name=inbound_stream(task.engagement_id),
            payload=run_payload,
            task_id=task.id,
        )
        # Keep every mutation caller-owned. The API/service that accepted or
        # retried the Task commits suggestion + Task + policy execution + lease
        # + outbox together; the independent relay publishes after commit.
        session.refresh(task)

        logger.info(
            "tactical.dispatched",
            task_id=str(task.id),
            tool=tool_name,
            target=target,
            thread_id=str(thread_id),
            mcp_host=mcp_host,
            lease_requires_container=lease.requires_container,
        )

        return thread_id
