"""Correlate agent — proposes clusters of related findings for merge (v1.4.0).

The analyst clicks "Correlate findings" on the Findings tab; this agent
receives the engagement's open, non-excluded findings and returns a list
of proposed groups — findings that likely describe the same root cause,
attack path, affected entity, or CVE. **Nothing merges here.** The
analyst reviews each proposed group in a modal and approves the ones
they agree with; each approval triggers a separate
``POST /findings/{parent_id}/merge`` call.

Mirrors :class:`PlanningAgent` and :class:`StrategicAgent`:

- BYO provider key resolved per-user (never engagement-creator fallback)
- Structured output via ``with_structured_output`` — no freeform parsing
- Every call recorded as an :class:`AgentExecution` (Costs tab visible)
- Refuses to touch anything active — pure planning
"""
from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
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
    Engagement,
    Finding,
)
from app.orchestrator.llm import default_provider_model

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# LLM I/O shapes
# ---------------------------------------------------------------------------


class _ProposedGroup(BaseModel):
    """One cluster proposed by the LLM."""

    rationale: str = Field(
        ...,
        description=(
            "One short sentence naming why these findings belong together — "
            "same host, same CVE, same attack chain, same credential. The "
            "analyst reads this to decide whether to accept the merge."
        ),
    )
    finding_short_ids: list[str] = Field(
        ...,
        min_length=2,
        description=(
            "6-char short IDs (as shown in the Findings table) of the "
            "findings in this group. First is the proposed parent — the "
            "row that survives the merge. Minimum 2; a group of 1 is "
            "meaningless."
        ),
    )


class _CorrelateProposal(BaseModel):
    """Structured envelope the LLM returns."""

    groups: list[_ProposedGroup] = Field(
        default_factory=list,
        description=(
            "Proposed clusters. Empty list = every finding stands alone. "
            "Do not force groups when the findings are genuinely unrelated."
        ),
    )


class CorrelatedGroup(BaseModel):
    """One group as returned by :meth:`CorrelateAgent.propose` — resolved
    back into finding UUIDs (not short IDs) so the API layer can pass
    them straight to the frontend."""

    rationale: str
    finding_ids: list[uuid.UUID]


CORRELATE_SYSTEM_PROMPT = """You are the Correlation advisor for the Red \
Team Dashboard. An analyst has surfaced a set of findings during an \
authorized engagement and wants your read on which of them describe the \
same underlying issue.

Group findings when they share a plausible root cause:
- Same host or entity affected by the same class of issue.
- Same CVE / advisory across multiple targets in a way that suggests one \
  underlying misconfiguration.
- One finding is a symptom of another (e.g. exposed service → default \
  creds → data exposure on the same host).
- A finding's summary text closely matches or references another.

Do NOT group findings just because they share a phase, a severity, or a \
tool. Those are not root causes.

For each group:
- Pick the finding that BEST represents the group as the parent (the \
  first short ID in the list). This is the row that will survive the \
  merge; the others fold into it. Prefer the highest-severity finding, \
  or the one whose title most clearly names the underlying issue.
- The rationale is ONE short sentence naming what ties them together — \
  the analyst reads this before approving the merge.
- Only propose groups you are confident about. Empty output is the \
  correct answer when nothing groups cleanly. Do not force clusters.

You are not deciding. The analyst reviews each proposed group and \
approves the ones they agree with.
"""


def _render_findings_for_prompt(findings: Iterable[Finding]) -> str:
    """Compact one-row-per-finding rendering the LLM reads as context.

    Keeps token count down by only including fields that could inform a
    grouping decision — full details JSONB is dropped.
    """
    lines: list[str] = []
    for f in findings:
        short = str(f.id).replace("-", "")[:6].upper()
        summary = (f.summary or "").strip().replace("\n", " ")
        if len(summary) > 240:
            summary = summary[:237] + "…"
        target = f.target or "—"
        lines.append(
            f"[{short}] sev={f.severity.value} phase={f.phase.value} "
            f"target={target} · {f.title}"
            + (f"\n    summary: {summary}" if summary else "")
        )
    return "\n".join(lines)


def _build_user_prompt(eng: Engagement, findings_block: str) -> str:
    return f"""=== ENGAGEMENT ===
name: {eng.name}
slug: {eng.slug}
description: {eng.description or "(none)"}

=== OPEN FINDINGS ({len(findings_block.splitlines())} rows — short-id in brackets) ===
{findings_block}

Propose groups of related findings per the rules. Return JSON matching \
the required schema. If nothing groups cleanly, return an empty list.
"""


class CorrelateAgent:
    """Cluster proposer — one shot per Correlate button press.

    Construction mirrors :class:`PlanningAgent`: pass ``redis_client`` for
    production, or pass a pre-built ``llm`` for tests to skip the BYO-key
    resolver.
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
                "CorrelateAgent needs a redis_client to resolve the "
                "analyst's BYO key — construct with "
                "CorrelateAgent(redis_client=...)"
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

    def propose(
        self,
        session: Session,
        *,
        engagement: Engagement,
        findings: list[Finding],
        acting_user_id: uuid.UUID,
    ) -> tuple[AgentExecution, list[CorrelatedGroup]]:
        """Run the LLM over the finding list and return proposed clusters.

        Returns ``(execution, groups)``. Caller commits the session — we
        create but don't commit the ``AgentExecution`` so the API request
        can wrap it in one transaction alongside its audit_log row.

        Groups whose short IDs don't resolve back to real findings (LLM
        hallucination) are dropped silently, logged as a warning. Groups
        of size < 2 are also dropped — a "group" of one is meaningless.
        """
        execution = AgentExecution(
            engagement_id=engagement.id,
            agent=AgentName.correlate,
            trigger=AgentTrigger.manual,
            input={
                "finding_count": len(findings),
                "acting_user_id": str(acting_user_id),
            },
            status=AgentExecutionStatus.running,
            started_at=datetime.now(tz=UTC),
        )

        try:
            llm, provider, model_name = self._resolve_llm(
                acting_user_id=acting_user_id
            )
            execution.model_provider = provider
            execution.model_name = model_name

            findings_block = _render_findings_for_prompt(findings)
            structured = llm.with_structured_output(_CorrelateProposal)
            messages = [
                ("system", CORRELATE_SYSTEM_PROMPT),
                ("user", _build_user_prompt(engagement, findings_block)),
            ]
            raw_response: Any = structured.invoke(messages)
            proposal: _CorrelateProposal = (
                raw_response
                if isinstance(raw_response, _CorrelateProposal)
                else _CorrelateProposal.model_validate(raw_response)
            )
            tokens_in, tokens_out = _extract_usage(raw_response)
            execution.tokens_in = tokens_in
            execution.tokens_out = tokens_out
        except Exception as exc:  # noqa: BLE001 — any failure → mark failed
            execution.status = AgentExecutionStatus.failed
            execution.error = str(exc)[:2000]
            execution.completed_at = datetime.now(tz=UTC)
            logger.warning(
                "correlate.failed",
                engagement_id=str(engagement.id),
                error=str(exc),
            )
            return execution, []

        # Resolve short IDs back to full UUIDs. Anything the LLM hallucinated
        # gets dropped — no way to merge a finding that doesn't exist.
        by_short = {str(f.id).replace("-", "")[:6].upper(): f.id for f in findings}
        resolved_groups: list[CorrelatedGroup] = []
        dropped_hallucinations = 0
        for g in proposal.groups:
            resolved_ids: list[uuid.UUID] = []
            for short in g.finding_short_ids:
                fid = by_short.get(short.strip().upper())
                if fid is not None and fid not in resolved_ids:
                    resolved_ids.append(fid)
                else:
                    dropped_hallucinations += 1
            if len(resolved_ids) >= 2:
                resolved_groups.append(
                    CorrelatedGroup(rationale=g.rationale, finding_ids=resolved_ids)
                )

        if dropped_hallucinations:
            logger.info(
                "correlate.dropped_hallucinations",
                engagement_id=str(engagement.id),
                count=dropped_hallucinations,
            )

        execution.output = {
            "groups_count": len(resolved_groups),
            "considered": len(findings),
            "dropped_hallucinations": dropped_hallucinations,
        }
        execution.status = AgentExecutionStatus.completed
        execution.completed_at = datetime.now(tz=UTC)

        return execution, resolved_groups
