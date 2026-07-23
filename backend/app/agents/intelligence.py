"""The v3 intelligence agent — one agent, prompt-mode personas, Memory-backed.

This is the heart of architecture step 4 (B4): a single intelligence agent that
reads Engagement Memory + the deterministic rollup, and switches persona via
``AgentPromptMode`` (strategy / analysis / ideation / coverage_review). It
replaces the per-finding Strategic consumer (one cold-start LLM call per
finding) with milestone-batched, Memory-backed invocation.

This module is the deterministic foundation of B4:
  - the four prompt-mode system prompts (the personas),
  - ``build_intelligence_context`` — assembles Memory hot-set (B1 projection)
    + rollup (B2) + engagement basics into the structured input every
    invocation feeds the LLM,
  - ``build_intelligence_messages`` — selects the persona prompt + renders the
    context as the message list for an LLM ``invoke``.

The LLM-calling invocation (resolve_model_for_mode from B4a → structured
invoke → Memory writes + work items) and the per-finding retirement land in
later B4 sub-slices; this slice is the deterministic, fully-testable base.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Engagement, EngagementPhase, ScopeItem
from app.models.agent_mode_model_preference import AgentPromptMode
from app.services.engagement_rollup import (
    coverage_rollup,
    findings_summary,
    significant_finding_ids,
)
from app.services.strategy_projection import build_strategy_projection

# ---------------------------------------------------------------------------
# Prompt-mode personas — one agent, different postures
# ---------------------------------------------------------------------------

_STRATEGY_PROMPT = """You are the intelligence agent for an authorized security engagement, \
drafting strategy. You read the Engagement Memory and the finding/coverage \
rollup below. Separate known facts from hypotheses. Prioritize coverage gaps \
and high-severity/unvalidated findings. Propose concrete next steps as work \
items, each with a disposition (tool-backed / manual / build). Do not propose \
exploitation — analysts exploit, the agent enumerates and analyzes. Keep it \
tight; reference Memory element ids when building on a prior fact/hypothesis."""

_ANALYSIS_PROMPT = """You are the intelligence agent analyzing a batch of significant findings \
(new, unvalidated, or high-severity) from the Engagement Memory and rollup \
below. For each, state what it means, how confident you are, what it supports \
or refutes among open hypotheses, and what follow-up would raise confidence. \
Cite Memory element ids. Output structured assessments, not prose dumps. Flag \
anything that should become a new Memory fact/hypothesis or a work item."""

_IDEATION_PROMPT = """You are the intelligence agent in creative-exploration mode. The engagement \
is past baseline coverage — go off the beaten path. From the Engagement Memory \
and rollup below, surface avenues that aren't in the methodology backbone: \
unusual attack chains, lateral-movement hypotheses, things worth chasing that \
we have no tool for (propose those as manual/build work items so the gap is \
never lost). Be specific and grounded in the evidence — creative, not \
speculative. Scope still applies; flag out-of-scope ideas as out-of-scope, \
don't pursue them."""

_COVERAGE_REVIEW_PROMPT = """You are the intelligence agent reviewing coverage and Memory \
health. Read the coverage rollup and the Memory hot-set below. Identify baseline nodes that \
are stale or unsatisfied (re-collection candidates), hypotheses that are \
resolved and should fold into a decision, threads gone dormant, and facts that \
are low-confidence and unreferenced (compaction candidates). Your output drives \
compaction (fold_into_decision) and re-collection recommendations. Be \
conservative — only fold a hypothesis when the evidence actually settles it."""

PROMPT_MODE_PROMPTS: dict[AgentPromptMode, str] = {
    AgentPromptMode.strategy: _STRATEGY_PROMPT,
    AgentPromptMode.analysis: _ANALYSIS_PROMPT,
    AgentPromptMode.ideation: _IDEATION_PROMPT,
    AgentPromptMode.coverage_review: _COVERAGE_REVIEW_PROMPT,
}


# ---------------------------------------------------------------------------
# Context assembler — Memory hot-set + rollup + engagement basics
# ---------------------------------------------------------------------------


def build_intelligence_context(
    session: Session,
    *,
    engagement_id: uuid.UUID,
    since: Any = None,
) -> dict[str, Any]:
    """Assemble the deterministic structured input for any prompt-mode invocation.

    Combines (all already on main):
      - Memory hot-set grouped by kind (B1 ``build_strategy_projection``),
      - finding significance rollup (B2 ``findings_summary``) + the significant
        gather set (``significant_finding_ids``) for the analysis mode,
      - coverage rollup (B2 ``coverage_rollup``),
      - engagement basics: phase + scope-item count.

    ``since`` bounds "new" findings to those created at/after it (the delta
    window for batched analysis). The agent interprets this compact structured
    input; it never generates it (architecture-answers Q5).
    """
    memory = build_strategy_projection(session, engagement_id=engagement_id)
    summary = findings_summary(session, engagement_id=engagement_id, since=since)
    coverage = coverage_rollup(session, engagement_id=engagement_id)
    significant_ids = significant_finding_ids(
        session, engagement_id=engagement_id, since=since
    )

    engagement = session.get(Engagement, engagement_id)
    scope_count = session.scalar(
        select(func.count(ScopeItem.id)).where(ScopeItem.engagement_id == engagement_id)
    ) or 0
    phase = engagement.phase if engagement is not None else EngagementPhase.baseline

    return {
        "engagement": {
            "id": str(engagement_id),
            "phase": phase.value if hasattr(phase, "value") else str(phase),
            "scope_item_count": int(scope_count),
        },
        "memory": {
            "decisions": [_el_summary(e) for e in memory["decisions"]],
            "facts": [_el_summary(e) for e in memory["facts"]],
            "hypotheses": [_el_summary(e) for e in memory["hypotheses"]],
            "open_questions": [_el_summary(e) for e in memory["open_questions"]],
            "threads": [_el_summary(e) for e in memory["threads"]],
            "token_total": memory["token_total"],
            "token_budget": memory["token_budget"],
            "capped": memory["capped"],
        },
        "findings": summary,
        "significant_finding_ids": [str(fid) for fid in significant_ids],
        "coverage": coverage,
    }


def _el_summary(element: Any) -> dict[str, Any]:
    """Compact projection of a Memory element for the prompt — id + kind +
    summary + confidence/status, no heavy body. Keeps the context cheap."""
    return {
        "id": str(element.id),
        "kind": element.kind.value if hasattr(element.kind, "value") else str(element.kind),
        "summary": element.summary,
        "status": element.status.value if hasattr(element.status, "value") else str(element.status),
        "confidence": element.confidence,
    }


# ---------------------------------------------------------------------------
# Message builder — persona prompt + rendered context
# ---------------------------------------------------------------------------


def build_intelligence_messages(
    context: dict[str, Any], mode: AgentPromptMode
) -> list[tuple[str, str]]:
    """Return the ``[(system, prompt), (user, context_json)]`` message list for
    an LLM ``invoke`` in the given prompt-mode."""
    system_prompt = PROMPT_MODE_PROMPTS[mode]
    return [
        ("system", system_prompt),
        ("user", json.dumps(context, default=str)),
    ]


# ---------------------------------------------------------------------------
# LLM invocation (B4-2) — injected llm for testability; per-mode persistence
# ---------------------------------------------------------------------------

from datetime import UTC, datetime  # noqa: E402

from app.models import (  # noqa: E402
    ActorType,
    AgentExecution,
    AgentExecutionStatus,
    AgentName,
    AgentTrigger,
    MemoryElement,
    MemoryKind,
    WorkItem,
    WorkItemDisposition,
    WorkItemExecutor,
    WorkItemPriority,
    WorkItemStatus,
)
from app.schemas.intelligence import (  # noqa: E402
    AnalysisOutput,
    CoverageReviewOutput,
    IdeationOutput,
    StrategyOutput,
)
from app.services.agent_model_resolver import resolve_model_for_mode  # noqa: E402
from app.services.memory import create_element, fold_into_decision  # noqa: E402

MODE_OUTPUT_SCHEMAS: dict[AgentPromptMode, type] = {
    AgentPromptMode.strategy: StrategyOutput,
    AgentPromptMode.analysis: AnalysisOutput,
    AgentPromptMode.ideation: IdeationOutput,
    AgentPromptMode.coverage_review: CoverageReviewOutput,
}

_INTELLIGENCE_AUTHOR = "intelligence-agent"


def _disposition(value: str) -> WorkItemDisposition:
    try:
        return WorkItemDisposition(value)
    except ValueError:
        return WorkItemDisposition.manual_local


def _add_work_item(
    session: Session, *, engagement_id: uuid.UUID, proposed: Any
) -> None:
    session.add(
        WorkItem(
            engagement_id=engagement_id,
            title=proposed.title,
            status=WorkItemStatus.ready,
            priority=WorkItemPriority.medium,
            executor_type=WorkItemExecutor.unassigned,
            disposition=_disposition(proposed.disposition),
            rationale=proposed.rationale,
        )
    )


def _persist_analysis(session: Session, *, engagement_id: uuid.UUID, out: AnalysisOutput) -> None:
    for f in out.proposed_facts:
        create_element(
            session, engagement_id=engagement_id, kind=MemoryKind.fact,
            summary=f.summary, confidence=f.confidence,
            author_type=ActorType.agent, author_id=_INTELLIGENCE_AUTHOR,
        )
    for h in out.proposed_hypotheses:
        create_element(
            session, engagement_id=engagement_id, kind=MemoryKind.hypothesis,
            summary=h.summary, confidence=h.confidence,
            author_type=ActorType.agent, author_id=_INTELLIGENCE_AUTHOR,
        )


def _persist_ideation(session: Session, *, engagement_id: uuid.UUID, out: IdeationOutput) -> None:
    for h in out.proposed_hypotheses:
        create_element(
            session, engagement_id=engagement_id, kind=MemoryKind.hypothesis,
            summary=h.summary, confidence=h.confidence,
            author_type=ActorType.agent, author_id=_INTELLIGENCE_AUTHOR,
        )
    for w in out.proposed_work_items:
        _add_work_item(session, engagement_id=engagement_id, proposed=w)


def _persist_coverage_review(
    session: Session, *, engagement_id: uuid.UUID, out: CoverageReviewOutput
) -> int:
    folded = 0
    for fold in out.folds:
        hyps = list(
            session.execute(
                select(MemoryElement).where(
                    MemoryElement.engagement_id == engagement_id,
                    MemoryElement.id.in_([uuid.UUID(str(h)) for h in fold.hypothesis_ids]),
                )
            ).scalars()
        )
        if not hyps:
            continue
        fold_into_decision(
            session, engagement_id=engagement_id, hypotheses=hyps,
            decision_summary=fold.decision_summary, rationale=fold.rationale,
            actor_type=ActorType.agent, actor_id=_INTELLIGENCE_AUTHOR,
        )
        folded += len(hyps)
    return folded


def _persist_strategy(session: Session, *, engagement_id: uuid.UUID, out: StrategyOutput) -> None:
    for d in out.proposed_decisions:
        create_element(
            session, engagement_id=engagement_id, kind=MemoryKind.decision,
            summary=d.summary, body={"rationale": d.rationale},
            author_type=ActorType.agent, author_id=_INTELLIGENCE_AUTHOR,
        )
    for w in out.proposed_work_items:
        _add_work_item(session, engagement_id=engagement_id, proposed=w)


def run_intelligence_analysis(
    session: Session,
    *,
    engagement_id: uuid.UUID,
    mode: AgentPromptMode,
    acting_user_id: uuid.UUID,
    llm: Any,
    since: Any = None,
) -> tuple[Any, AgentExecution]:
    """Invoke the intelligence agent in ``mode`` and persist its output.

    ``llm`` is injected (the caller — B3 milestone runner, an API endpoint, or
    a test — constructs it, typically from ``resolve_model_for_mode`` + the
    acting analyst's BYO key). This function still calls ``resolve_model_for_mode``
    to record model attribution on the AgentExecution.

    Returns ``(parsed_output, execution)``. On LLM failure the execution is
    marked failed and ``(None, execution)`` is returned — no partial writes.
    """
    context = build_intelligence_context(
        session, engagement_id=engagement_id, since=since
    )
    messages = build_intelligence_messages(context, mode)
    schema = MODE_OUTPUT_SCHEMAS[mode]

    provider, model_name = resolve_model_for_mode(
        session, user_id=acting_user_id, engagement_id=engagement_id, mode=mode
    ) or (None, None)
    execution = AgentExecution(
        engagement_id=engagement_id,
        agent=AgentName.engagement_strategist,
        trigger=AgentTrigger.manual,
        input={
            "mode": mode.value,
            "engagement_id": str(engagement_id),
            "v3_intelligence": True,
        },
        status=AgentExecutionStatus.running,
        started_at=datetime.now(tz=UTC),
        model_provider=provider,
        model_name=model_name,
    )
    session.add(execution)
    session.flush()

    try:
        # Savepoint around the LLM invoke + persistence: on failure we roll back
        # ONLY this intelligence work (undoing any partial writes), leaving the
        # caller's session (e.g. the engagement row) and the execution row intact.
        with session.begin_nested():
            structured = llm.with_structured_output(schema)
            result = structured.invoke(messages)
            # Per-mode persistence.
            if mode is AgentPromptMode.analysis:
                _persist_analysis(session, engagement_id=engagement_id, out=result)
            elif mode is AgentPromptMode.ideation:
                _persist_ideation(session, engagement_id=engagement_id, out=result)
            elif mode is AgentPromptMode.coverage_review:
                _persist_coverage_review(session, engagement_id=engagement_id, out=result)
            elif mode is AgentPromptMode.strategy:
                _persist_strategy(session, engagement_id=engagement_id, out=result)
        execution.status = AgentExecutionStatus.completed
        execution.completed_at = datetime.now(tz=UTC)
        execution.output = {"mode": mode.value, "parsed": True}
    except Exception as exc:  # noqa: BLE001 — savepoint rolled back; execution survives
        execution.status = AgentExecutionStatus.failed
        execution.error = str(exc)[:2000]
        execution.completed_at = datetime.now(tz=UTC)
        session.flush()
        return None, execution
    return result, execution
