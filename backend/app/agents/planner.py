"""Planner agent — tenant-global "suggestion box".

The analyst types an idea into ``/settings/suggestions``; this agent reads
the idea against the project's CHARTER + HANDOFF (and the currently-approved
roadmap items) and emits a structured pros/cons response. The analyst (or an
admin) then approves or rejects the suggestion; approved items export to
``ROADMAP.md`` for Claude Code to pick up as PR work in a future session.

Pure observer — never executes anything. Mirrors :class:`StrategicAgent`'s
shape (BYO key resolved per-user, structured output via langchain, every
call logged to ``agent_executions`` so the Costs tab sees it).
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agents.strategic import _extract_usage, _make_chat_model
from app.models import (
    AgentExecution,
    AgentExecutionStatus,
    AgentName,
    AgentTrigger,
    RoadmapSuggestion,
    RoadmapSuggestionStatus,
)
from app.orchestrator.llm import default_provider_model

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Project docs the planner reads as context. Mirrored from the repo root
# into the backend image at build time (the Docker build context is
# ``backend/``, so root-level files aren't visible to the running container).
# Keep these in sync with the originals — see CLAUDE.md.
# ---------------------------------------------------------------------------

_CONTEXT_DIR = Path(__file__).parent / "planner_context"


def _read_doc(name: str) -> str:
    path = _CONTEXT_DIR / name
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning("planner.missing_context_doc", path=str(path))
        return f"({name} not bundled with this image)"


_CHARTER = _read_doc("CHARTER.md")
_HANDOFF = _read_doc("HANDOFF.md")


# ---------------------------------------------------------------------------
# LLM I/O shapes
# ---------------------------------------------------------------------------


class _Evaluation(BaseModel):
    """Structured-output envelope from the planner."""

    summary: str = Field(
        ...,
        description=(
            "One-sentence read on the suggestion in the context of the "
            "current project plan. Shown above the pros/cons in the UI."
        ),
    )
    pros: list[str] = Field(
        default_factory=list,
        description=(
            "Reasons to take this idea. Each item is a single sentence. "
            "If the idea is already approved/in-flight, say so as a pro."
        ),
    )
    cons: list[str] = Field(
        default_factory=list,
        description=(
            "Reasons not to take this idea, or risks. Each item is a "
            "single sentence. Empty list = no concerns."
        ),
    )


PLANNER_SYSTEM_PROMPT = """You are the Planning advisor for the Red Team \
Dashboard project. An analyst has submitted a product/feature suggestion. \
Your job: weigh it against the project's published vision (CHARTER) and \
current build status (HANDOFF), and produce a short, opinionated pros/cons \
read that the analyst — and a project admin — will use to decide whether \
to add it to the roadmap.

Rules:
- Be specific. Reference the relevant phase, document, or invariant when \
something in CHARTER/HANDOFF informs your call ("conflicts with the \
'agents scan, analysts exploit' invariant", "duplicates Phase 10's \
hybrid-execution work").
- Be honest about cost vs. value. If the suggestion is small but adds \
scope creep, that's a con. If it unblocks something already approved, \
that's a strong pro.
- Be concise. Each pro/con is one sentence. 2-5 pros and 2-5 cons is \
typical; fewer is fine when the idea is clearly good or clearly bad.
- If the idea is already approved/in-flight per HANDOFF, lead with that \
in summary and put it as the first pro.
- If the idea conflicts with a documented invariant (Charter "Decided" \
items, sanitization rules, agents-vs-analyst boundary), call that out \
explicitly as a con — those are usually deal-breakers.

You are not deciding. The admin makes the final Yes/No call from your \
read.
"""


def _build_user_prompt(suggestion_text: str, approved_roadmap: str) -> str:
    return f"""=== PROJECT CHARTER ===
{_CHARTER}

=== HANDOFF (current build status) ===
{_HANDOFF}

=== APPROVED ROADMAP (already on the queue) ===
{approved_roadmap or "(no items approved yet)"}

=== ANALYST'S SUGGESTION ===
{suggestion_text}

Evaluate the suggestion above against the project context. Return JSON \
matching the required schema.
"""


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class PlanningAgent:
    """Evaluates one suggestion at a time. ``evaluate`` is the entry point.

    Like Strategic, BYO key resolution uses the ephemeral Redis cache
    keyed on the suggestion author's user id — never an engagement
    creator or env-var fallback.
    """

    def __init__(
        self,
        *,
        provider: str | None = None,
        model_name: str | None = None,
        llm: Any | None = None,
        redis_client: Any | None = None,
    ) -> None:
        self._llm = llm
        self._provider = provider
        self._model_name = model_name
        self._redis = redis_client

    def _resolve_llm(
        self,
        *,
        acting_user_id: uuid.UUID,
    ) -> tuple[Any, str, str]:
        if self._llm is not None:
            return (
                self._llm,
                self._provider or "test",
                self._model_name or "test",
            )
        provider = self._provider
        model_name = self._model_name
        if not (provider and model_name):
            provider, model_name = default_provider_model()
        if self._redis is None:
            raise RuntimeError(
                "PlanningAgent needs a redis_client to resolve the "
                "submitter's BYO key — construct with "
                "PlanningAgent(redis_client=...)"
            )
        from app.services.ephemeral_provider_key import resolve_for_user

        resolved = resolve_for_user(
            self._redis, user_id=acting_user_id, provider=provider
        )
        return (
            _make_chat_model(
                provider,
                model_name,
                api_key=resolved.api_key,
                endpoint=resolved.endpoint,
            ),
            provider,
            model_name,
        )

    def evaluate(
        self,
        session: Session,
        *,
        suggestion: RoadmapSuggestion,
        approved_roadmap: str,
    ) -> AgentExecution:
        """Run the planner over a freshly-created suggestion row.

        Mutates the suggestion in place — fills in ``agent_summary``,
        ``agent_pros``, ``agent_cons`` (or leaves them empty + flips the
        execution to ``failed`` if the LLM call dies). Returns the
        ``AgentExecution`` so the caller can persist + link it.

        Caller commits the session — we ``add`` but don't ``commit`` so
        this composes inside an API-request transaction.
        """
        execution = AgentExecution(
            engagement_id=None,
            agent=AgentName.planner,
            trigger=AgentTrigger.manual,
            input={
                "suggestion_id": str(suggestion.id),
                "author_user_id": (
                    str(suggestion.author_user_id)
                    if suggestion.author_user_id
                    else None
                ),
                "body_length": len(suggestion.body),
            },
            status=AgentExecutionStatus.running,
            started_at=datetime.now(tz=UTC),
        )
        session.add(execution)
        session.flush()

        try:
            if suggestion.author_user_id is None:
                raise RuntimeError(
                    "PlanningAgent requires a suggestion with an "
                    "author_user_id — anonymous suggestions can't resolve "
                    "a BYO key"
                )
            llm, provider, model_name = self._resolve_llm(
                acting_user_id=suggestion.author_user_id
            )
            execution.model_provider = provider
            execution.model_name = model_name

            structured = llm.with_structured_output(_Evaluation)
            messages = [
                ("system", PLANNER_SYSTEM_PROMPT),
                ("user", _build_user_prompt(suggestion.body, approved_roadmap)),
            ]
            raw_response: Any = structured.invoke(messages)
            evaluation: _Evaluation = (
                raw_response
                if isinstance(raw_response, _Evaluation)
                else _Evaluation.model_validate(raw_response)
            )
            tokens_in, tokens_out = _extract_usage(raw_response)
            execution.tokens_in = tokens_in
            execution.tokens_out = tokens_out
        except Exception as exc:  # noqa: BLE001 — any LLM failure → mark failed
            execution.status = AgentExecutionStatus.failed
            execution.error = str(exc)[:2000]
            execution.completed_at = datetime.now(tz=UTC)
            logger.warning(
                "planner.failed",
                suggestion_id=str(suggestion.id),
                error=str(exc),
            )
            return execution

        suggestion.agent_summary = evaluation.summary
        suggestion.agent_pros = list(evaluation.pros)
        suggestion.agent_cons = list(evaluation.cons)
        suggestion.agent_execution_id = execution.id

        execution.output = {
            "summary": evaluation.summary,
            "pros_count": len(evaluation.pros),
            "cons_count": len(evaluation.cons),
        }
        execution.status = AgentExecutionStatus.completed
        execution.completed_at = datetime.now(tz=UTC)

        return execution


def render_approved_roadmap(suggestions: list[RoadmapSuggestion]) -> str:
    """Render the approved suggestions as the markdown that an admin would
    download (and that the agent reads back on subsequent evaluations).

    v1.1.0: split into two sections.

    - **Open (Approved · Not Shipped)** — rows where ``implemented_at is
      None``, sorted by ``priority ASC NULLS LAST`` then ``created_at``.
      Priority appears in the heading as ``[P<n>]`` (or ``[unranked]``)
      so Claude Code (and human readers) can see the queue order without
      hitting the dashboard.
    - **Shipped** — rows where ``implemented_at`` is set, newest first.
      Compact one-line entries — the full pros/cons stay with the Open
      section they came from until they ship.
    """
    approved = [
        s for s in suggestions if s.status == RoadmapSuggestionStatus.approved
    ]
    if not approved:
        return ""

    open_rows = [s for s in approved if s.implemented_at is None]
    shipped_rows = [s for s in approved if s.implemented_at is not None]

    # Priority 1 first; unranked at the end. created_at as the stable
    # tiebreaker so the file diff is stable across pushes.
    open_rows.sort(
        key=lambda s: (
            s.priority if s.priority is not None else 999,
            s.created_at,
        )
    )
    shipped_rows.sort(key=lambda s: s.implemented_at, reverse=True)

    lines: list[str] = [
        "# Red Team Dashboard — Approved Roadmap",
        "",
        (
            "Approved suggestions from the in-portal Suggestion Box "
            "(`/settings/suggestions`). Generated for Claude Code to pick up "
            "as future PR work."
        ),
        "",
    ]

    if open_rows:
        lines.append("## Open (Approved · Not Shipped)")
        lines.append("")
        lines.append(
            "Ordered by priority (P1 = highest, then P2… then unranked). "
            "Unranked rows haven't been triaged yet — treat them as lower "
            "priority than any numbered row unless an admin note says "
            "otherwise."
        )
        lines.append("")
        for idx, s in enumerate(open_rows, start=1):
            tag = f"P{s.priority}" if s.priority is not None else "unranked"
            summary = s.agent_summary or "(no summary)"
            lines.append(f"### {idx}. [{tag}] {summary}")
            lines.append("")
            lines.append("**Original suggestion:**")
            lines.append("")
            lines.append(f"> {s.body.strip()}")
            lines.append("")
            if s.agent_pros:
                lines.append("**Pros:**")
                for p in s.agent_pros:
                    lines.append(f"- {p}")
                lines.append("")
            if s.agent_cons:
                lines.append("**Cons:**")
                for c in s.agent_cons:
                    lines.append(f"- {c}")
                lines.append("")
            if s.review_note:
                lines.append(f"**Admin note:** {s.review_note}")
                lines.append("")
            reviewed = (
                s.reviewed_at.isoformat() if s.reviewed_at else "(no timestamp)"
            )
            lines.append(
                f"_Approved {reviewed} — suggestion id `{s.id}`_"
            )
            lines.append("")

    if shipped_rows:
        lines.append("## Shipped")
        lines.append("")
        lines.append(
            "Approved items that have landed. Kept here (not deleted) so "
            "the roadmap doubles as a running changelog."
        )
        lines.append("")
        for s in shipped_rows:
            shipped_day = (
                s.implemented_at.date().isoformat()
                if s.implemented_at
                else "(no date)"
            )
            summary = s.agent_summary or s.body.strip().splitlines()[0]
            lines.append(
                f"- **{shipped_day}** — {summary} "
                f"(suggestion id `{s.id}`)"
            )
        lines.append("")

    return "\n".join(lines)
