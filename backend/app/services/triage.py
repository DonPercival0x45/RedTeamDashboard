"""LLM triage summary for a Finding.

v0.7.0. The Findings slide-over has an "AI Triage" button next to "Save
summary"; clicking it asks the analyst's BYO LLM to write a 2-4 sentence
report-ready narrative of the finding and drops the result into the
Summary textarea. The analyst then edits and saves manually — this
service does NOT mutate ``findings.summary``.

Cost-tracking: every call writes one ``AgentExecution`` row keyed to
``agent='triage'`` (migration 0026 extended the enum), so the Costs tab
counts triage spend the same way it counts Strategic / Tactical /
Planner spend.

BYO-key policy: the *clicking* analyst's ephemeral Redis-cached key
satisfies the call, not the engagement creator's. This preserves the
v0.4.0 cross-user-key-reuse lock.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.agents.strategic import _extract_usage, _make_chat_model
from app.core import pricing
from app.models import (
    AgentExecution,
    AgentExecutionStatus,
    AgentName,
    AgentTrigger,
    Finding,
)
from app.orchestrator.llm import default_provider_model
from app.services.ephemeral_provider_key import resolve_for_user

_SYSTEM_PROMPT = (
    "You write concise pentest-finding summaries for inclusion in a "
    "written report. Audience: a security professional reading the "
    "engagement deliverable. Name what the issue is, where it was "
    "observed, and the practical impact. 2-4 sentences. Plain text "
    "ONLY — no markdown, no headers, no bullets, no lead-in like "
    "'This finding describes...'."
)


def _build_user_prompt(finding: Finding) -> str:
    return (
        f"Finding title: {finding.title}\n"
        f"Severity: {finding.severity.value}\n"
        f"Affected target: {finding.target or '(no target recorded)'}\n"
        f"Source tool: {finding.source_tool or 'unknown'}\n"
        f"Existing analyst summary (may be empty or a draft to refine): "
        f"{finding.summary or '(none)'}\n"
        f"Tool-emitted detail payload: {finding.details!r}\n\n"
        "Write the summary now. Plain text, 2-4 sentences."
    )


def triage_finding_summary(
    session: Session,
    redis_client: Any,
    *,
    finding: Finding,
    acting_user_id: uuid.UUID,
) -> tuple[AgentExecution, str]:
    """Generate a triage summary; persist a cost-tracking row.

    Returns ``(execution, summary_text)``. Caller commits the session.
    Raises whatever ``resolve_for_user`` / the LLM SDK raise — the API
    layer maps ``NoProviderKeyError`` to a 400 with a pointer to
    /settings/keys; other failures bubble as a 502.
    """
    provider, model_name = default_provider_model()
    resolved = resolve_for_user(
        redis_client, user_id=acting_user_id, provider=provider
    )
    llm = _make_chat_model(
        provider,
        model_name,
        api_key=resolved.api_key,
        endpoint=resolved.endpoint,
    )

    execution = AgentExecution(
        engagement_id=finding.engagement_id,
        agent=AgentName.triage,
        trigger=AgentTrigger.manual,
        input={"finding_id": str(finding.id)},
        model_provider=provider,
        model_name=model_name,
        status=AgentExecutionStatus.running,
        started_at=datetime.now(tz=UTC),
    )
    session.add(execution)
    # v0.8.1 fix: commit immediately so the Status tab sees the row in its
    # `running` state RIGHT NOW. The previous pattern (flush only, caller
    # commits) made active rows invisible to other DB sessions for the
    # entire duration of the LLM call. With this commit, the Status tab
    # paints a green "active" box within the next 2s poll.
    session.commit()
    session.refresh(execution)

    try:
        response = llm.invoke(
            [
                ("system", _SYSTEM_PROMPT),
                ("user", _build_user_prompt(finding)),
            ]
        )
        # langchain message content is either a str or a list of content
        # blocks; coerce to str for the textarea.
        raw = response.content
        summary = (raw if isinstance(raw, str) else str(raw)).strip()
        tokens_in, tokens_out = _extract_usage(response)
        cost = pricing.cost_usd(model_name, tokens_in, tokens_out, provider=provider)
        execution.status = AgentExecutionStatus.completed
        execution.completed_at = datetime.now(tz=UTC)
        execution.tokens_in = tokens_in
        execution.tokens_out = tokens_out
        execution.cost_usd = cost
        execution.output = {"summary_chars": len(summary)}
        session.commit()
        session.refresh(execution)
        return execution, summary
    except Exception as exc:
        execution.status = AgentExecutionStatus.failed
        execution.completed_at = datetime.now(tz=UTC)
        execution.error = str(exc)[:1000]
        # Commit the failed-state row so the Status tab can show it as a red
        # box without waiting for the outer commit. Caller does NOT need to
        # re-commit; subsequent `session.commit()` in the API endpoint is a
        # no-op for this row.
        session.commit()
        raise


# v0.20.0 (roadmap #1): AI-assisted rewrite of a manually-entered
# finding description. Sibling of triage, but refines the analyst's OWN
# draft instead of generating from scratch — and is explicitly
# constrained NOT to introduce facts the analyst didn't write (the
# fabrication guardrail the roadmap flagged).
_REWRITE_SYSTEM_PROMPT = (
    "You improve a security analyst's draft finding description for "
    "clarity, concision, and report-readiness. HARD CONSTRAINT: use ONLY "
    "information present in the draft. Do NOT add CVE IDs, hostnames, "
    "tool names, ports, users, severities, or any technical detail not "
    "already in the text. Do NOT drop material facts. Fix grammar, "
    "tighten wording, fix ordering, and make the impact clear. If the "
    "draft is already clear, return it substantially unchanged. "
    "Plain text ONLY — no markdown, no headers, no bullets."
)


def _build_rewrite_user_prompt(draft: str) -> str:
    return f"Rewrite this finding description:\n\n{draft}"


def rewrite_finding_summary(
    session: Session,
    redis_client: Any,
    *,
    finding: Finding,
    draft: str,
    acting_user_id: uuid.UUID,
) -> tuple[AgentExecution, str]:
    """Refine an analyst's draft summary via the LLM (roadmap #1).

    Like :func:`triage_finding_summary` but the source is the analyst's
    own draft text, and the system prompt forbids introducing facts not
    in the draft. Returns ``(execution, rewritten_text)``; caller commits.
    Raises whatever the key resolver / LLM SDK raise.
    """
    provider, model_name = default_provider_model()
    resolved = resolve_for_user(
        redis_client, user_id=acting_user_id, provider=provider
    )
    llm = _make_chat_model(
        provider,
        model_name,
        api_key=resolved.api_key,
        endpoint=resolved.endpoint,
    )

    execution = AgentExecution(
        engagement_id=finding.engagement_id,
        agent=AgentName.triage,
        trigger=AgentTrigger.manual,
        input={"finding_id": str(finding.id), "rewrite": True},
        model_provider=provider,
        model_name=model_name,
        status=AgentExecutionStatus.running,
        started_at=datetime.now(tz=UTC),
    )
    session.add(execution)
    session.commit()
    session.refresh(execution)

    try:
        response = llm.invoke(
            [
                ("system", _REWRITE_SYSTEM_PROMPT),
                ("user", _build_rewrite_user_prompt(draft)),
            ]
        )
        raw = response.content
        summary = (raw if isinstance(raw, str) else str(raw)).strip()
        tokens_in, tokens_out = _extract_usage(response)
        cost = pricing.cost_usd(model_name, tokens_in, tokens_out, provider=provider)
        execution.status = AgentExecutionStatus.completed
        execution.completed_at = datetime.now(tz=UTC)
        execution.tokens_in = tokens_in
        execution.tokens_out = tokens_out
        execution.cost_usd = cost
        execution.output = {"rewrite_chars": len(summary)}
        session.commit()
        session.refresh(execution)
        return execution, summary
    except Exception as exc:
        execution.status = AgentExecutionStatus.failed
        execution.completed_at = datetime.now(tz=UTC)
        execution.error = str(exc)[:1000]
        session.commit()
        raise
