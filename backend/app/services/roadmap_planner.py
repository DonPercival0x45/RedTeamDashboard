"""LLM-driven roadmap prioritization + combine detection (v0.16.0).

Two operations, both BYO-key (same pattern as triage / tool_llm_review):

- :func:`detect_combine_clusters` — reads the open suggestion pool and
  proposes clusters of duplicates or "resolvable-by-one-solution"
  entries. Does NOT mutate; analyst confirms each merge via
  ``POST /roadmap-suggestions/{id}/combine``.

- :func:`bulk_rank_suggestions` — reads the open pool and returns a
  priority (1..10, 1 = highest) for each row. Admin confirms in the
  UI dialog before we apply.

Both emit one ``AgentExecution`` row per call (``agent='planner'``,
``trigger='manual'``) so the Costs tab shows the spend alongside
Strategic / Tactical / Triage / tool_review.

Guardrails:

- Cap the pool at 200 suggestions per call. Larger pools raise
  :class:`PoolTooLargeError` which the endpoint maps to a 400 with a
  hint to approve/reject some rows first.
- All model output goes through :func:`_parse_json` which handles
  fenced replies and prose-before-JSON gracefully.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
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
    RoadmapSuggestion,
)
from app.orchestrator.llm import default_provider_model
from app.services.ephemeral_provider_key import (
    NoProviderKeyError,
    resolve_for_user,
)

_MAX_POOL_SIZE = 200
_MAX_BODY_CHARS = 800  # per-row truncation in the prompt so token counts stay bounded


class PoolTooLargeError(ValueError):
    """Raised when the caller passes more suggestions than the LLM can
    reasonably handle in one prompt. Endpoint returns 400."""

    def __init__(self, size: int, cap: int = _MAX_POOL_SIZE) -> None:
        super().__init__(
            f"pool has {size} suggestions; cap is {cap} per call. "
            "Approve or reject some rows first, then retry."
        )
        self.size = size
        self.cap = cap


@dataclass
class CombineCluster:
    primary_id: uuid.UUID
    member_ids: list[uuid.UUID]
    reasoning: str

    def to_json(self) -> dict[str, Any]:
        return {
            "primary_id": str(self.primary_id),
            "member_ids": [str(m) for m in self.member_ids],
            "reasoning": self.reasoning,
        }


@dataclass
class CombineResult:
    clusters: list[CombineCluster] = field(default_factory=list)
    model: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    execution_id: uuid.UUID | None = None
    error: str | None = None


@dataclass
class RankedRow:
    id: uuid.UUID
    priority: int
    reasoning: str


@dataclass
class RankResult:
    rankings: list[RankedRow] = field(default_factory=list)
    model: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    execution_id: uuid.UUID | None = None
    error: str | None = None


_COMBINE_SYSTEM = (
    "You review a pool of product-feedback suggestions for a security "
    "engagement dashboard and identify clusters that describe the same "
    "underlying problem — either exact duplicates or entries that would "
    "be resolved by the same solution. You return JSON only.\n\n"
    "Return an object of the shape:\n"
    '{"clusters": [{"primary_id": "<uuid>", '
    '"member_ids": ["<uuid>", ...], '
    '"reasoning": "one sentence"}, ...]}\n\n'
    "- ``primary_id`` is the entry that should survive the merge — pick "
    "the one that's most complete or most representative.\n"
    "- ``member_ids`` is the list of OTHER ids to fold into the primary. "
    "Do NOT include primary_id in member_ids. Do NOT list the primary "
    "twice.\n"
    "- Only cluster entries that are TRULY the same underlying request. "
    "When in doubt, leave them separate — the analyst can always ask "
    "again later.\n"
    "- Return an empty ``clusters`` list if nothing seems mergeable.\n\n"
    "Return ONLY the JSON — no prose, no fences, no lead-in."
)


_RANK_SYSTEM = (
    "You are a product manager triaging a backlog of feedback for a "
    "defensive-security engagement dashboard. Assign each entry a "
    "priority integer 1-10 where 1 is HIGHEST priority (most urgent / "
    "highest impact / most user pain) and 10 is LOWEST. Higher urgency "
    "AND higher impact AND lower effort push toward 1.\n\n"
    "Return JSON only, of the shape:\n"
    '{"rankings": [{"id": "<uuid>", "priority": <1..10>, '
    '"reasoning": "one sentence"}, ...]}\n\n'
    "- Include EVERY entry from the input pool in your rankings — do "
    "not skip any.\n"
    "- Distribute priorities across the 1-10 range; do not assign "
    "every entry the same priority.\n"
    "- 'reasoning' is one sentence explaining the priority choice.\n\n"
    "Return ONLY the JSON — no prose, no fences, no lead-in."
)


def _serialize_pool(pool: list[RoadmapSuggestion]) -> str:
    payload = []
    for s in pool:
        body = (s.body or "").strip()
        if len(body) > _MAX_BODY_CHARS:
            body = body[: _MAX_BODY_CHARS - 3] + "..."
        payload.append(
            {
                "id": str(s.id),
                "body": body,
                "status": s.status.value,
                "source": s.source,
                "pros_count": len(s.agent_pros or []),
                "cons_count": len(s.agent_cons or []),
            }
        )
    return json.dumps(payload, indent=2)


def _open_execution(
    session: Session, provider: str, model_name: str, op_name: str
) -> AgentExecution:
    execution = AgentExecution(
        engagement_id=None,  # planner is tenant-global
        agent=AgentName.planner,
        trigger=AgentTrigger.manual,
        input={"op": op_name},
        model_provider=provider,
        model_name=model_name,
        status=AgentExecutionStatus.running,
        started_at=datetime.now(tz=UTC),
    )
    session.add(execution)
    session.commit()
    session.refresh(execution)
    return execution


def _parse_json(text: str) -> dict[str, Any]:
    """Coerce model output into a dict. Strips ```json fences and
    prose-before-JSON prefixes; returns a shape-safe empty dict on
    parse failure so callers can still complete."""
    stripped = text.strip()
    if stripped.startswith("```"):
        parts = stripped.split("\n", 1)
        stripped = parts[1] if len(parts) == 2 else ""
        if stripped.endswith("```"):
            stripped = stripped[:-3].rstrip()
    if "{" in stripped and not stripped.startswith("{"):
        stripped = stripped[stripped.index("{") :]
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return {}


def detect_combine_clusters(
    session: Session,
    redis_client: Any,
    *,
    pool: list[RoadmapSuggestion],
    acting_user_id: uuid.UUID,
) -> CombineResult:
    """Ask the LLM which suggestions in ``pool`` describe the same
    underlying problem. Never mutates the pool — analyst confirms each
    merge separately."""
    if len(pool) > _MAX_POOL_SIZE:
        raise PoolTooLargeError(len(pool))
    if len(pool) < 2:
        return CombineResult()

    provider, model_name = default_provider_model()
    try:
        resolved = resolve_for_user(
            redis_client, user_id=acting_user_id, provider=provider
        )
    except NoProviderKeyError as exc:
        raise exc

    llm = _make_chat_model(
        provider, model_name, api_key=resolved.api_key, endpoint=resolved.endpoint
    )
    execution = _open_execution(session, provider, model_name, "detect_combines")

    try:
        response = llm.invoke(
            [
                ("system", _COMBINE_SYSTEM),
                ("user", f"Suggestion pool:\n{_serialize_pool(pool)}"),
            ]
        )
        raw = response.content
        text = (raw if isinstance(raw, str) else str(raw)).strip()
        tokens_in, tokens_out = _extract_usage(response)
        cost = pricing.cost_usd(model_name, tokens_in, tokens_out, provider=provider)

        parsed = _parse_json(text)
        clusters: list[CombineCluster] = []
        valid_ids = {s.id for s in pool}
        for c in parsed.get("clusters", []) or []:
            try:
                primary_id = uuid.UUID(c["primary_id"])
                member_ids = [uuid.UUID(m) for m in c.get("member_ids", []) or []]
            except (KeyError, ValueError, TypeError):
                continue
            # Drop clusters that reference ids outside the pool or that
            # accidentally include the primary in the member list.
            if primary_id not in valid_ids:
                continue
            member_ids = [m for m in member_ids if m in valid_ids and m != primary_id]
            if not member_ids:
                continue
            clusters.append(
                CombineCluster(
                    primary_id=primary_id,
                    member_ids=member_ids,
                    reasoning=str(c.get("reasoning", "") or "")[:500],
                )
            )

        execution.status = AgentExecutionStatus.completed
        execution.completed_at = datetime.now(tz=UTC)
        execution.tokens_in = tokens_in
        execution.tokens_out = tokens_out
        execution.cost_usd = cost
        execution.output = {"clusters_count": len(clusters), "pool_size": len(pool)}
        session.commit()
        session.refresh(execution)
        return CombineResult(
            clusters=clusters,
            model=f"{provider}/{model_name}",
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            execution_id=execution.id,
        )
    except Exception as exc:
        execution.status = AgentExecutionStatus.failed
        execution.completed_at = datetime.now(tz=UTC)
        execution.error = str(exc)[:1000]
        session.commit()
        return CombineResult(
            model=f"{provider}/{model_name}",
            execution_id=execution.id,
            error=str(exc)[:500],
        )


def bulk_rank_suggestions(
    session: Session,
    redis_client: Any,
    *,
    pool: list[RoadmapSuggestion],
    acting_user_id: uuid.UUID,
) -> RankResult:
    """Ask the LLM to assign a 1-10 priority to every row in the pool.
    Does NOT persist priorities — caller (the API endpoint) applies
    them after the admin's confirmation dialog closes."""
    if len(pool) > _MAX_POOL_SIZE:
        raise PoolTooLargeError(len(pool))
    if not pool:
        return RankResult()

    provider, model_name = default_provider_model()
    try:
        resolved = resolve_for_user(
            redis_client, user_id=acting_user_id, provider=provider
        )
    except NoProviderKeyError as exc:
        raise exc

    llm = _make_chat_model(
        provider, model_name, api_key=resolved.api_key, endpoint=resolved.endpoint
    )
    execution = _open_execution(session, provider, model_name, "bulk_rank")

    try:
        response = llm.invoke(
            [
                ("system", _RANK_SYSTEM),
                ("user", f"Suggestion pool:\n{_serialize_pool(pool)}"),
            ]
        )
        raw = response.content
        text = (raw if isinstance(raw, str) else str(raw)).strip()
        tokens_in, tokens_out = _extract_usage(response)
        cost = pricing.cost_usd(model_name, tokens_in, tokens_out, provider=provider)

        parsed = _parse_json(text)
        rankings: list[RankedRow] = []
        valid_ids = {s.id for s in pool}
        for r in parsed.get("rankings", []) or []:
            try:
                row_id = uuid.UUID(r["id"])
                priority = int(r["priority"])
            except (KeyError, ValueError, TypeError):
                continue
            if row_id not in valid_ids:
                continue
            if not 1 <= priority <= 10:
                continue
            rankings.append(
                RankedRow(
                    id=row_id,
                    priority=priority,
                    reasoning=str(r.get("reasoning", "") or "")[:500],
                )
            )

        execution.status = AgentExecutionStatus.completed
        execution.completed_at = datetime.now(tz=UTC)
        execution.tokens_in = tokens_in
        execution.tokens_out = tokens_out
        execution.cost_usd = cost
        execution.output = {
            "ranked": len(rankings),
            "pool_size": len(pool),
        }
        session.commit()
        session.refresh(execution)
        return RankResult(
            rankings=rankings,
            model=f"{provider}/{model_name}",
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            execution_id=execution.id,
        )
    except Exception as exc:
        execution.status = AgentExecutionStatus.failed
        execution.completed_at = datetime.now(tz=UTC)
        execution.error = str(exc)[:1000]
        session.commit()
        return RankResult(
            model=f"{provider}/{model_name}",
            execution_id=execution.id,
            error=str(exc)[:500],
        )
