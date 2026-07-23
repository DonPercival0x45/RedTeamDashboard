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

This module is the trigger logic; wiring it to the live event stream (the
strategic consumer / a new milestone consumer reading ``run.completed`` and,
later, Track A's ``collection.job.completed``) is a thin follow-up.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.agents.intelligence import run_intelligence_analysis
from app.models.agent_mode_model_preference import AgentPromptMode
from app.services.engagement_rollup import significant_finding_ids

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


def handle_milestone(
    session: Session,
    *,
    engagement_id: uuid.UUID,
    milestone_type: str,
    acting_user_id: uuid.UUID,
    llm: Any,
    since: Any = None,
) -> tuple[Any, Any] | None:
    """React to a milestone: gather significant findings, invoke the agent once.

    Returns ``(output, execution)`` from ``run_intelligence_analysis``, or
    ``None`` when the milestone isn't reacted to, or when an analysis-mode
    milestone has no significant findings to analyze (gather-then-analyze —
    don't burn tokens on a nothing-changed milestone).
    """
    mode = MILESTONE_MODES.get(milestone_type)
    if mode is None:
        return None

    if mode in _ANALYSIS_MODES:
        significant = significant_finding_ids(
            session, engagement_id=engagement_id, since=since
        )
        if not significant:
            return None  # nothing significant -> no invocation

    return run_intelligence_analysis(
        session,
        engagement_id=engagement_id,
        mode=mode,
        acting_user_id=acting_user_id,
        llm=llm,
        since=since,
    )
