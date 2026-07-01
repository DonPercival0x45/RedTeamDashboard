"""LLM safety review for uploaded tools (v0.13.0, layer 3 of the
analyst lane gate).

Runs synchronously during ``POST /tools`` for the analyst lane (Python
+ shell kinds; binary skips this entirely per the arc — LLM can't
audit a compiled artifact). Reads the source alongside the parsed
manifest and returns a structured verdict:

    {
      "safe": bool,
      "reason": str,                    # one-sentence verdict
      "concerns": [str, ...],           # specific things noticed
      "matches_stated_intent": bool,    # does the code do what the manifest claims?
      "model": str,
      "tokens_in": int,
      "tokens_out": int,
    }

BYO-key model, same as triage: the *uploading* analyst's ephemeral
Redis-cached key satisfies the call. No key → the review is skipped
(``{"skipped": "no provider key"}``) and the upload still succeeds so
the admin sees the gap.

Cost accounting: every call writes one ``AgentExecution`` row keyed to
``agent='tool_review'`` (added by migration 0031), same as the other
LLM-driven services. Failed calls also land on the Costs tab so the
admin can spot pattern issues.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
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
)
from app.orchestrator.llm import default_provider_model
from app.services.ephemeral_provider_key import (
    NoProviderKeyError,
    resolve_for_user,
)

_MAX_SOURCE_CHARS = 40_000  # roughly ~10k tokens of source at the outside


@dataclass
class LLMReviewResult:
    safe: bool
    reason: str
    concerns: list[str]
    matches_stated_intent: bool
    model: str
    tokens_in: int
    tokens_out: int
    skipped: str | None = None
    error: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "safe": self.safe,
            "reason": self.reason,
            "concerns": self.concerns,
            "matches_stated_intent": self.matches_stated_intent,
            "model": self.model,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            **({"skipped": self.skipped} if self.skipped else {}),
            **({"error": self.error} if self.error else {}),
        }


def _skipped(reason: str) -> LLMReviewResult:
    return LLMReviewResult(
        safe=False,
        reason=f"review skipped: {reason}",
        concerns=[],
        matches_stated_intent=False,
        model="",
        tokens_in=0,
        tokens_out=0,
        skipped=reason,
    )


_SYSTEM_PROMPT = (
    "You are a code-safety reviewer for a defensive-security engagement "
    "management platform. Analysts upload small first-party tools "
    "(Python or shell) that run inside a sandboxed container against "
    "engagement-scoped targets. Your job: read the tool's source AND "
    "its declared manifest, decide whether the tool is safe to add to "
    "the catalog, and whether the code matches what the manifest "
    "claims.\n\n"
    "Definitions:\n"
    "- 'safe' = the tool does what a first-party recon / enumeration / "
    "scanning tool should do. It reads inputs from the args payload, "
    "makes outbound network calls to targets that were declared in "
    "the engagement scope, prints results.\n"
    "- 'unsafe' = the tool tries to persist state, install packages "
    "beyond what the manifest declares, hit hosts outside the args-"
    "derived scope, embed credentials, exfiltrate data to a hardcoded "
    "endpoint, execute encoded payloads, or take actions matching "
    "exploitation / persistence / lateral-movement / destruction.\n"
    "- 'matches_stated_intent' = the code's behaviour is consistent "
    "with the manifest's declared task_kind (enum|scan|exploit), "
    "risk_level (passive|active|destructive), and network_egress. If "
    "the manifest says risk=passive but the code writes files or "
    "executes commands against targets, that mismatch is a NO.\n\n"
    "Return a single JSON object with the exact shape:\n"
    '{"safe": bool, "reason": "one sentence", "concerns": '
    '["specific concern 1", ...], "matches_stated_intent": bool}\n\n'
    "Return ONLY the JSON — no prose, no code fences, no lead-in."
)


def _build_user_prompt(
    manifest: dict[str, Any],
    source: str,
    kind: str,
) -> str:
    truncated = source
    if len(truncated) > _MAX_SOURCE_CHARS:
        truncated = truncated[:_MAX_SOURCE_CHARS] + "\n\n# [source truncated]"
    manifest_json = json.dumps(manifest, indent=2, sort_keys=True)
    return (
        f"Tool kind: {kind}\n\n"
        f"Manifest:\n```yaml\n{manifest_json}\n```\n\n"
        f"Source:\n```{kind}\n{truncated}\n```\n\n"
        "Review the tool now. Return the JSON verdict."
    )


def review_tool_source(
    session: Session,
    redis_client: Any,
    *,
    source: str,
    kind: str,
    manifest: dict[str, Any],
    tool_name: str,
    acting_user_id: uuid.UUID,
) -> LLMReviewResult:
    """Ask the LLM whether the tool is safe. Never raises — infra and
    parse failures land as ``error`` on the result, not exceptions,
    so the upload path always makes progress and the admin sees the
    verdict verbatim.

    Persists one ``AgentExecution`` row per call (running → completed /
    failed) so cost accounting lines up with Strategic / Tactical /
    Triage."""
    provider, model_name = default_provider_model()

    try:
        resolved = resolve_for_user(
            redis_client, user_id=acting_user_id, provider=provider
        )
    except NoProviderKeyError:
        return _skipped("no provider key")

    llm = _make_chat_model(
        provider,
        model_name,
        api_key=resolved.api_key,
        endpoint=resolved.endpoint,
    )

    execution = AgentExecution(
        engagement_id=None,  # tool review is tenant-global, not per-engagement
        agent=AgentName.tool_review,
        trigger=AgentTrigger.manual,
        input={"tool_name": tool_name, "kind": kind},
        model_provider=provider,
        model_name=model_name,
        status=AgentExecutionStatus.running,
        started_at=datetime.now(tz=UTC),
    )
    session.add(execution)
    # Commit immediately so the Status tab sees the running row while
    # we wait on the LLM (same pattern as triage.py).
    session.commit()
    session.refresh(execution)

    try:
        response = llm.invoke(
            [
                ("system", _SYSTEM_PROMPT),
                ("user", _build_user_prompt(manifest, source, kind)),
            ]
        )
        raw = response.content
        text = (raw if isinstance(raw, str) else str(raw)).strip()
        tokens_in, tokens_out = _extract_usage(response)
        cost = pricing.cost_usd(model_name, tokens_in, tokens_out, provider=provider)

        parsed = _parse_verdict(text)
        execution.status = AgentExecutionStatus.completed
        execution.completed_at = datetime.now(tz=UTC)
        execution.tokens_in = tokens_in
        execution.tokens_out = tokens_out
        execution.cost_usd = cost
        execution.output = {
            "safe": parsed.get("safe"),
            "concerns_count": len(parsed.get("concerns", []) or []),
        }
        session.commit()
        session.refresh(execution)
        return LLMReviewResult(
            safe=bool(parsed.get("safe", False)),
            reason=str(parsed.get("reason", "") or "").strip(),
            concerns=[str(c) for c in (parsed.get("concerns") or [])],
            matches_stated_intent=bool(parsed.get("matches_stated_intent", False)),
            model=f"{provider}/{model_name}",
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        )
    except Exception as exc:
        execution.status = AgentExecutionStatus.failed
        execution.completed_at = datetime.now(tz=UTC)
        execution.error = str(exc)[:1000]
        session.commit()
        return LLMReviewResult(
            safe=False,
            reason=f"LLM review errored: {exc}",
            concerns=[],
            matches_stated_intent=False,
            model=f"{provider}/{model_name}",
            tokens_in=0,
            tokens_out=0,
            error=str(exc)[:500],
        )


def _parse_verdict(text: str) -> dict[str, Any]:
    """Coerce whatever the model returned into a dict. Models sometimes
    wrap the JSON in ```json … ``` fences or prepend prose; strip both."""
    stripped = text.strip()
    # Fenced?
    if stripped.startswith("```"):
        # drop the first line (``` or ```json) and the trailing ```
        parts = stripped.split("\n", 1)
        stripped = parts[1] if len(parts) == 2 else ""
        if stripped.endswith("```"):
            stripped = stripped[: -3].rstrip()
    # Prose-then-JSON?
    if "{" in stripped and not stripped.startswith("{"):
        stripped = stripped[stripped.index("{") :]
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return {
            "safe": False,
            "reason": f"could not parse verdict — got: {text[:200]}",
            "concerns": [],
            "matches_stated_intent": False,
        }
