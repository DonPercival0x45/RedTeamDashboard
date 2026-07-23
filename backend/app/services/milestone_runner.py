"""Milestone runner (v3 B3) — the gather-then-analyze trigger.

Replaces the per-finding Strategic consumer (one cold-start LLM call per
finding) with milestone-batched invocation: on a milestone event, gather the
significant findings (``is_new OR not_validated OR high_severity``) and invoke
the intelligence agent once in the mode the milestone maps to.

Milestone → prompt-mode:
  ``collection.job.completed`` → analysis (analyze what the run produced)
  ``run.completed``            → analysis (stand-in until Track A emits
                                 ``collection.job.completed`` at A6)
  ``coverage.gap.opened``      → strategy (propose work to close the gap)
  ``baseline.completed``       → ideation (exploration begins)

Gather-then-analyze: the analysis modes only fire when there ARE significant
findings to analyze — a nothing-changed milestone burns no tokens. The
strategy/ideation modes always fire (they propose, regardless of new findings).

This module owns both B3 trigger logic and B5 milestone maintenance. The live
strategic consumer calls ``run_milestone_cycle`` so primary intelligence,
deterministic compaction, and optional coverage review share one engagement
lock and receipt transaction.
"""
from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.agents.intelligence import (
    build_intelligence_context,
    fingerprint_intelligence_context,
    record_intelligence_failure,
    run_intelligence_analysis,
)
from app.models import (
    AgentExecution,
    AgentExecutionStatus,
    AgentName,
    AgentTrigger,
)
from app.models.agent_mode_model_preference import AgentPromptMode
from app.services.engagement_rollup import significant_finding_batch
from app.services.memory import compact as compact_memory
from app.services.memory import hot_token_total

logger = structlog.get_logger(__name__)

MILESTONE_MODES: dict[str, AgentPromptMode] = {
    "collection.job.completed": AgentPromptMode.analysis,
    "run.completed": AgentPromptMode.analysis,
    "coverage.gap.opened": AgentPromptMode.strategy,
    "baseline.completed": AgentPromptMode.ideation,
}

# Modes that analyze findings — only fire when significant findings exist.
_ANALYSIS_MODES = {AgentPromptMode.analysis}


def milestone_mode(milestone_type: str) -> AgentPromptMode | None:
    """The prompt-mode a milestone maps to, or ``None`` if we don't react to it."""
    return MILESTONE_MODES.get(milestone_type)


def _completed_analysis_batch_exists(
    session: Session,
    *,
    engagement_id: uuid.UUID,
    fingerprint: str,
) -> bool:
    """Whether automatic analysis already completed for this finding state."""
    return session.scalar(
        select(AgentExecution.id)
        .where(
            AgentExecution.engagement_id == engagement_id,
            AgentExecution.agent == AgentName.engagement_strategist,
            AgentExecution.trigger == AgentTrigger.tick,
            AgentExecution.status == AgentExecutionStatus.completed,
            AgentExecution.input["mode"].astext == AgentPromptMode.analysis.value,
            AgentExecution.input["significant_batch_fingerprint"].astext
            == fingerprint,
        )
        .limit(1)
    ) is not None


def _completed_context_exists(
    session: Session,
    *,
    engagement_id: uuid.UUID,
    mode: AgentPromptMode,
    fingerprint: str,
) -> bool:
    return session.scalar(
        select(AgentExecution.id)
        .where(
            AgentExecution.engagement_id == engagement_id,
            AgentExecution.agent == AgentName.engagement_strategist,
            AgentExecution.trigger == AgentTrigger.tick,
            AgentExecution.status == AgentExecutionStatus.completed,
            AgentExecution.input["mode"].astext == mode.value,
            AgentExecution.input["context_fingerprint"].astext == fingerprint,
        )
        .limit(1)
    ) is not None


def handle_milestone(
    session: Session,
    *,
    engagement_id: uuid.UUID,
    milestone_type: str,
    acting_user_id: uuid.UUID,
    llm: Any = None,
    llm_factory: Callable[[], tuple[Any, str, str]] | None = None,
    since: Any = None,
    thread_id: uuid.UUID | None = None,
    milestone_payload: dict[str, Any] | None = None,
) -> tuple[Any, Any] | None:
    """React to a milestone: gather significant findings, invoke the agent once.

    Returns ``(output, execution)`` from ``run_intelligence_analysis``, or
    ``None`` when the milestone isn't reacted to, or when an analysis-mode
    milestone has no significant findings to analyze (gather-then-analyze —
    don't burn tokens on a nothing-changed milestone). ``llm_factory`` is
    intentionally lazy so a skipped milestone does not require a live BYO key.
    Callers may continue to inject an already-built ``llm`` directly.
    """
    mode = MILESTONE_MODES.get(milestone_type)
    if mode is None:
        return None

    # Enforce serialization here as well as in run_milestone_cycle so any
    # future direct caller cannot race the read-then-invoke fingerprint check.
    # The transaction-scoped lock is re-entrant for the cycle's existing lock
    # and remains held through the caller's commit/rollback.
    acquire_engagement_memory_lock(session, engagement_id)

    batch: dict[str, Any] | None = None
    if mode in _ANALYSIS_MODES:
        batch = significant_finding_batch(
            session,
            engagement_id=engagement_id,
            since=since,
            thread_id=thread_id,
        )
        if batch["total"] == 0:
            return None  # nothing significant -> no invocation
        if _completed_analysis_batch_exists(
            session,
            engagement_id=engagement_id,
            fingerprint=batch["fingerprint"],
        ):
            logger.info(
                "intelligence.unchanged_batch_skipped",
                engagement_id=str(engagement_id),
                milestone_type=milestone_type,
                significant_count=batch["total"],
                fingerprint=batch["fingerprint"],
            )
            return None

    context = build_intelligence_context(
        session,
        engagement_id=engagement_id,
        since=since,
        thread_id=thread_id,
        significant_batch=batch,
        milestone={
            "type": milestone_type,
            "payload": milestone_payload or {},
        },
    )

    model_provider: str | None = None
    model_name: str | None = None
    if llm is None:
        if llm_factory is None:
            raise ValueError("handle_milestone requires llm or llm_factory")
        llm, model_provider, model_name = llm_factory()

    result = run_intelligence_analysis(
        session,
        engagement_id=engagement_id,
        mode=mode,
        acting_user_id=acting_user_id,
        llm=llm,
        since=since,
        thread_id=thread_id,
        model_provider=model_provider,
        model_name=model_name,
        trigger=AgentTrigger.tick,
        significant_batch=batch,
        context=context,
    )
    if result[1].status is AgentExecutionStatus.failed:
        raise RuntimeError(
            f"milestone intelligence failed: {result[1].error or 'unknown error'}"
        )
    return result


@dataclass(frozen=True)
class MilestoneCycleResult:
    """One atomic B5 milestone cycle and its maintenance outcomes."""

    primary: tuple[Any, Any] | None
    compaction: dict[str, Any]
    coverage_review: tuple[Any, Any] | None


def acquire_engagement_memory_lock(
    session: Session, engagement_id: uuid.UUID
) -> None:
    """Serialize all Memory writes for this engagement until outer commit.

    The strategic consumer's processing receipt owns the transaction. A
    transaction-scoped advisory lock therefore releases exactly when receipt
    completion commits, or when failure handling rolls the transaction back.
    """
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"engagement-memory:{engagement_id}"},
    )


def run_milestone_cycle(
    session: Session,
    *,
    engagement_id: uuid.UUID,
    milestone_type: str,
    acting_user_id: uuid.UUID,
    llm_factory: Callable[[], tuple[Any, str, str]],
    coverage_review_llm_factory: Callable[[], tuple[Any, str, str]],
    since: Any = None,
    thread_id: uuid.UUID | None = None,
    milestone_payload: dict[str, Any] | None = None,
) -> MilestoneCycleResult:
    """Run primary intelligence, deterministic compaction, then B5 review.

    All Memory mutations are serialized under one transaction-scoped
    engagement lock. Coverage review is lazy and fires only when deterministic
    compaction leaves the hot set above budget. Model/setup failures in this
    secondary maintenance pass are recorded but do not replay an already-run
    primary milestone; database failures still propagate to receipt retry.
    """
    acquire_engagement_memory_lock(session, engagement_id)
    primary = handle_milestone(
        session,
        engagement_id=engagement_id,
        milestone_type=milestone_type,
        acting_user_id=acting_user_id,
        llm_factory=llm_factory,
        since=since,
        thread_id=thread_id,
        milestone_payload=milestone_payload,
    )
    compaction = compact_memory(session, engagement_id=engagement_id)
    coverage_review: tuple[Any, Any] | None = None

    if compaction["still_over_budget"]:
        review_context = build_intelligence_context(
            session,
            engagement_id=engagement_id,
            since=since,
            thread_id=thread_id,
            milestone={
                "type": "maintenance.coverage_review",
                "payload": {},
            },
        )
        review_fingerprint = fingerprint_intelligence_context(review_context)
        review_already_completed = _completed_context_exists(
            session,
            engagement_id=engagement_id,
            mode=AgentPromptMode.coverage_review,
            fingerprint=review_fingerprint,
        )
        if review_already_completed:
            logger.info(
                "intelligence.unchanged_coverage_review_skipped",
                engagement_id=str(engagement_id),
                fingerprint=review_fingerprint,
            )
        else:
            try:
                llm, provider, model_name = coverage_review_llm_factory()
            except SQLAlchemyError:
                raise
            except Exception as exc:  # noqa: BLE001 - persist setup failure, do not replay primary
                execution = record_intelligence_failure(
                    session,
                    engagement_id=engagement_id,
                    mode=AgentPromptMode.coverage_review,
                    acting_user_id=acting_user_id,
                    error=exc,
                )
                coverage_review = (None, execution)
                logger.warning(
                    "intelligence.coverage_review_setup_failed",
                    engagement_id=str(engagement_id),
                    error=str(exc),
                )
            else:
                coverage_review = run_intelligence_analysis(
                    session,
                    engagement_id=engagement_id,
                    mode=AgentPromptMode.coverage_review,
                    acting_user_id=acting_user_id,
                    llm=llm,
                    model_provider=provider,
                    model_name=model_name,
                    trigger=AgentTrigger.tick,
                    context=review_context,
                )

        compaction = dict(compaction)
        compaction["token_after_review"] = hot_token_total(session, engagement_id)
        compaction["coverage_review_skipped_unchanged"] = review_already_completed
        if coverage_review is not None:
            execution = coverage_review[1]
            compaction["coverage_review_execution_id"] = str(execution.id)
            compaction["coverage_review_status"] = execution.status.value

    return MilestoneCycleResult(
        primary=primary,
        compaction=compaction,
        coverage_review=coverage_review,
    )
