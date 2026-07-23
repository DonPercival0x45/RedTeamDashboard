"""Strategic watcher — the Phase 9 planner.

This agent assists analysts during **authorized security engagements** by analyzing
findings and suggesting follow-up enumeration and scanning tasks.

**Charter:** Agents perform **enumeration and scanning only**. This agent is a pure
observer — it never executes tools, never dispatches. The analyst reviews suggestions
and explicitly accepts them to create Tasks. Validation/proof-of-concept work
(``TaskKind.exploit``) is **analyst-only** — filtered out even if the model proposes it.

Given a finding, it asks the LLM "what passive scan/enum tasks would dig into
this?" and writes the answers as ``Suggestion`` rows the analyst reviews from
the findings slide-over. The analyst's accept-click is what creates a Task
(and only then does ``TacticalAgent`` consider dispatching).

The LLM is asked for structured JSON via ``with_structured_output``; we don't
trust freeform text here.
"""
from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

import structlog
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    AgentExecution,
    AgentExecutionStatus,
    AgentName,
    AgentTrigger,
    Engagement,
    Finding,
    OwnerEligibility,
    ScopeItem,
    Suggestion,
    SuggestionKind,
    SuggestionStatus,
    TaskKind,
)
from app.orchestrator.llm import default_provider_model
from app.orchestrator.tools import all_tools
from app.services.agent_model_resolver import resolve_agent_model

logger = structlog.get_logger(__name__)


# Strategic produces scan + enum tasks only. exploit slipping through the
# structured-output schema is still filtered out post-LLM as a defense.
_AGENT_TASK_KINDS = (TaskKind.scan, TaskKind.enum)


class _ProposedTask(BaseModel):
    """LLM-side row shape for a single proposed next step."""

    title: str = Field(..., description="One-line task title shown to the analyst.")
    rationale: str = Field(
        ..., description="Why this is the right next step given the finding."
    )
    kind: TaskKind = Field(
        ...,
        description=(
            "scan = active probing (portscan, subnet_sweep, service_detect). "
            "enum = passive enumeration (subfinder, crt_sh, dns_lookup, "
            "whois_lookup, httpx_probe, reverse_dns). "
            "exploit = forbidden; agents never exploit."
        ),
    )
    owner_eligibility: OwnerEligibility = Field(
        OwnerEligibility.either,
        description=(
            "agent = safe for the worker to run autonomously after analyst "
            "accept. analyst = manual-only. either = analyst chooses."
        ),
    )
    tool: str = Field(
        ...,
        description="OSINT tool name (must be one of the listed registered tools).",
    )
    target: str = Field(
        ...,
        description="Concrete target the tool runs against (domain/ip/cidr/url).",
    )


class _StrategicProposal(BaseModel):
    """Structured-output envelope from the Strategic LLM call."""

    summary: str = Field(
        ...,
        description="2-3 sentence read on the finding from a red-team perspective.",
    )
    tasks: list[_ProposedTask] = Field(
        default_factory=list,
        description="Concrete next-step tasks. Empty list = nothing to add right now.",
    )

    @field_validator("tasks")
    @classmethod
    def _cap_tasks(cls, value: list[_ProposedTask]) -> list[_ProposedTask]:
        # Backstop the QUANTITY guidance in the system prompt: even if the
        # model over-produces, bound the analyst's hand-triage queue. The
        # prompt asks for highest-value first, so keeping the head preserves
        # the best proposals.
        cap = 8
        return value[:cap] if len(value) > cap else value


STRATEGIC_SYSTEM_PROMPT = (
    """You are the Strategic watcher in a red-team orchestrator. \
Your job is to read one finding and propose what passive enumeration or \
active scan tasks would develop it further.

HARD RULES (never break):
- Agents scan, analysts exploit. NEVER propose exploit-kind tasks. Only \
scan or enum.
- Only propose tools from the provided registry. Inventing a tool name is a \
failure.
- Targets MUST be inside the engagement's scope. If the finding's target sits \
outside scope, return an empty task list.
- Each proposed task must be one concrete next step (one tool + one target). \
Do not stack steps.
- If the finding doesn't suggest a useful next step right now, return tasks=[]. \
Empty is fine.

QUANTITY (important — analysts triage this queue by hand):
- Propose AT MOST 8 tasks, and only that many if each is genuinely distinct \
and high-value. Two to five is typical; zero is correct when nothing materially \
advances the finding.
- Do NOT enumerate every applicable tool against every target. Name the single \
highest-value next step first; add a second only if it develops a different, \
non-redundant angle.
- Never propose two tasks that run the same tool against the same target, or \
redundant tools against the same target (e.g. subfinder AND crt_sh against one \
apex are duplicates — pick one).

You are a pure observer. Your output is a recommendation; nothing runs until \
the analyst accepts.
"""
)


LEASE_POLICY_SYSTEM_PROMPT = (
    """You are the Strategic policy advisor in a red-team orchestrator. A Task \
is about to dispatch to a worker, and you are shaping the curated MCP \
surface that single run will see.

You are NOT choosing the tool, target, or whether the task runs. Those \
are already decided. You decide TWO things:

1. TOOLS — which subset of the PACK_DEFAULTS this run actually needs. \
Strict NARROW-ONLY: every tool you return MUST already appear in \
PACK_DEFAULTS. Adding a tool not on that list is a failure; your output \
will be filtered and the run will fall back to pack defaults. \
DISPATCH_TOOL must always appear in your output — without it the worker \
cannot execute the task at all.

2. CONTAINER — whether this run executes against the ISOLATED MCP host \
(process-separated, scale-to-zero) or the COLOCATED one (faster, shares \
the backend process). Return requires_container=True when the task has \
elevated blast-radius:
- kind=scan with active tooling (port_scan, subnet_sweep, service_detect, \
or any risk=active tool)
- tasks against a HIGH- or CRITICAL-severity source finding
- wide-fan-out targets (CIDR larger than /28; many subdomains)
Return requires_container=False for passive enum on a single target. \
When uncertain, prefer requires_container=True — isolation costs $0 when \
idle and the cold-start hit is one-time per scale-up.

HARD RULES (never break):
- Agents scan, analysts exploit. NEVER include any exploit-kind tool in \
your output, even if PACK_DEFAULTS mistakenly listed one.
- Stay inside PACK_DEFAULTS. Narrow, never widen.
- DISPATCH_TOOL must be present.

Provide a 1–2 sentence `reason` explaining your two choices. This is \
recorded on the AgentExecution row and shown in the Costs tab so the \
analyst can review your policy decisions after the fact.
"""
)


class _LeasePolicy(BaseModel):
    """LLM-side output for a single per-lease policy decision."""

    tools: list[str] = Field(
        ...,
        description=(
            "Subset of PACK_DEFAULTS this run needs. DISPATCH_TOOL must "
            "appear. Narrow only — never widen."
        ),
    )
    requires_container: bool = Field(
        ...,
        description=(
            "True → isolated MCP App; False → colocated. Default True on "
            "active scans or HIGH/CRITICAL source findings."
        ),
    )
    reason: str = Field(
        ...,
        description=(
            "1–2 sentences explaining the tools + container choice. Used "
            "for audit + Costs tab visibility."
        ),
    )


def _scope_summary(scope_items: Iterable[ScopeItem]) -> str:
    lines = []
    for item in scope_items:
        marker = "EXCLUDE" if item.is_exclusion else "INCLUDE"
        lines.append(f"  {marker} {item.kind.value}: {item.value}")
    return "\n".join(lines) if lines else "  (no scope items defined)"


def _tools_summary() -> str:
    lines = []
    for spec in all_tools():
        lines.append(
            f"  - {spec.name} (risk={spec.risk.value}, "
            f"target={spec.target_arg}/{spec.kind.value}): {spec.description}"
        )
    return "\n".join(lines)


def _pack_defaults_summary(pack_defaults: list[str]) -> str:
    """Render the pack-default tool list with risk + kind + description.
    Stage 3's policy prompt needs this granularity so the LLM can reason
    about *which* tools to drop without consulting the registry itself."""
    from app.orchestrator.tools import get_tool

    lines: list[str] = []
    for name in pack_defaults:
        spec = get_tool(name)
        if spec is None:
            lines.append(f"  - {name} (unknown — drop if unsure)")
            continue
        lines.append(
            f"  - {spec.name} (risk={spec.risk.value}, kind={spec.kind.value}): "
            f"{spec.description}"
        )
    return "\n".join(lines) if lines else "  (pack is empty)"


def _build_lease_policy_user_prompt(
    *,
    engagement: Engagement | None,
    task: Any,
    pack_defaults: list[str],
    dispatch_tool: str,
    finding: Finding | None,
    scope_items: list[ScopeItem],
) -> str:
    if engagement is None:
        engagement_block = "(orphaned task — no engagement record)"
    else:
        engagement_block = f"{engagement.name} ({engagement.slug})"
    finding_block = "(none — task created directly, not from a finding)"
    if finding is not None:
        finding_block = (
            f"id:       {finding.id}\n"
            f"  title:    {finding.title}\n"
            f"  severity: {finding.severity.value}\n"
            f"  phase:    {finding.phase.value}\n"
            f"  tool:     {finding.source_tool or '(unknown)'}\n"
            f"  target:   {finding.target or '(none)'}"
        )
    return f"""ENGAGEMENT: {engagement_block}

TASK:
  id:               {task.id}
  kind:             {task.kind.value}
  title:            {task.title}
  dispatch_tool:    {dispatch_tool or '(none)'}
  dispatch_target:  {(task.payload or {}).get('target', '(none)')}

PACK_DEFAULTS (the unfiltered tool surface for kind={task.kind.value}):
{_pack_defaults_summary(pack_defaults)}

SCOPE:
{_scope_summary(scope_items)}

SOURCE_FINDING:
  {finding_block}

Return JSON matching the required schema.
"""


def _build_user_prompt(engagement: Engagement, finding: Finding, scope: str) -> str:
    return f"""ENGAGEMENT: {engagement.name} ({engagement.slug})
Description: {engagement.description or "(none)"}

SCOPE:
{scope}

REGISTERED TOOLS:
{_tools_summary()}

FINDING:
  id:       {finding.id}
  title:    {finding.title}
  phase:    {finding.phase.value}
  severity: {finding.severity.value}
  tool:     {finding.source_tool or "(unknown)"}
  target:   {finding.target or "(none)"}
  data:     {finding.details!r}

Propose next-step tasks per the rules in your system prompt. Return JSON \
matching the required schema.
"""


def _extract_usage(response: Any) -> tuple[int | None, int | None]:
    """Pull (input_tokens, output_tokens) out of a langchain response if present.

    Langchain wraps token usage on either ``response_metadata['usage']``
    (Anthropic) or ``response_metadata['token_usage']`` (OpenAI), and the
    structured-output wrapper hides the underlying message. We dig defensively
    and return ``(None, None)`` when we can't find anything — non-fatal.
    """
    if isinstance(response, list):
        totals = [_extract_usage(item) for item in response]
        in_values = [value for value, _ in totals if value is not None]
        out_values = [value for _, value in totals if value is not None]
        return (
            sum(in_values) if in_values else None,
            sum(out_values) if out_values else None,
        )
    meta = getattr(response, "response_metadata", None) or {}
    usage = meta.get("usage") or meta.get("token_usage") or {}
    return (
        usage.get("input_tokens") or usage.get("prompt_tokens"),
        usage.get("output_tokens") or usage.get("completion_tokens"),
    )


# v0.8.1: OpenAI-compatible bases for providers that ship an OpenAI-shaped
# API surface. The analyst's stored endpoint (per-key, on the BYO upload)
# wins over this default — useful for self-hosted gateways or future
# region/edge variants. ``custom`` has no default base; the stored endpoint
# is mandatory.
_OPENAI_COMPATIBLE_BASES: dict[str, str] = {
    "xai": "https://api.x.ai/v1",
    "together": "https://api.together.xyz/v1",
    "groq": "https://api.groq.com/openai/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "mistral": "https://api.mistral.ai/v1",
    "google": "https://generativelanguage.googleapis.com/v1beta/openai",
    "cohere": "https://api.cohere.com/compatibility/v1",
    "moonshot": "https://api.moonshot.cn/v1",
    "custom": "",
}


def _make_chat_model(
    provider: str,
    name: str,
    *,
    api_key: str | None = None,
    endpoint: str | None = None,
    max_tokens: int | None = None,
) -> Any:
    """Provider-agnostic chat model factory used by Strategic.

    Cousin of ``app.orchestrator.llm.make_llm`` but WITHOUT ``.bind_tools()`` —
    Strategic doesn't tool-call, it returns structured JSON. Imports lazily so
    the unused providers' SDKs aren't required at import time.

    ``api_key`` / ``endpoint`` come from the KICKING analyst's ephemeral
    Redis-cached key (resolved by :func:`_resolve_llm`). When the analyst
    has no key for the provider, the resolver raises and this factory is
    not called.

    v0.8.1+: the OpenAI-compatible providers (xAI, Together, Groq,
    DeepSeek, Mistral, Google, Cohere, Moonshot/Kimi, Custom) route through ChatOpenAI
    with a per-provider ``base_url``. No new langchain packages required;
    those vendors all expose an OpenAI-shaped surface. ``Custom`` requires
    the analyst to upload the key with an ``endpoint`` so we know where
    to point.
    """
    provider = provider.lower()
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        kwargs: dict[str, Any] = {"model": name, "max_tokens": max_tokens or 4096}
        if api_key:
            kwargs["api_key"] = api_key
        return ChatAnthropic(**kwargs)
    if provider == "openai":
        from langchain_openai import ChatOpenAI

        kwargs = {"model": name}
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        if api_key:
            kwargs["api_key"] = api_key
        if endpoint:
            kwargs["base_url"] = endpoint
        return ChatOpenAI(**kwargs)
    if provider == "ollama":
        from langchain_ollama import ChatOllama

        from app.core.config import settings

        # Ollama is keyless; per-user endpoint override wins over deployment default.
        return ChatOllama(model=name, base_url=endpoint or settings.ollama_host)
    if provider == "azure":
        from langchain_openai import AzureChatOpenAI

        from app.core.config import settings

        kwargs = {
            "azure_endpoint": endpoint or settings.azure_openai_endpoint,
            "api_key": api_key or settings.azure_openai_api_key or None,
            "azure_deployment": name or settings.azure_openai_deployment,
            "api_version": settings.azure_openai_api_version,
        }
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        return AzureChatOpenAI(**kwargs)
    if provider in _OPENAI_COMPATIBLE_BASES:
        from langchain_openai import ChatOpenAI

        base = endpoint or _OPENAI_COMPATIBLE_BASES[provider]
        if not base:
            raise ValueError(
                f"provider '{provider}' requires an endpoint on the BYO key "
                "(re-upload at /settings/keys with the API base URL filled in)"
            )
        kwargs = {"model": name, "base_url": base}
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        if api_key:
            kwargs["api_key"] = api_key
        return ChatOpenAI(**kwargs)
    raise ValueError(f"unknown LLM provider {provider!r}")


class StrategicAgent:
    """Pure-watcher planner. ``analyze_finding`` is the only entry point.

    The ``acting_user_id`` on every call MUST be the kicking analyst — the
    person who caused this Strategic invocation (clicked Analyze, kicked
    the run that produced the finding, accepted the suggestion that became
    the task). The engagement creator is no longer consulted. This closes
    the cross-user-key-reuse path locked 2026-06-29.
    """

    def __init__(
        self,
        *,
        provider: str | None = None,
        model_name: str | None = None,
        llm: Any | None = None,
        redis_client: Any | None = None,
    ) -> None:
        """Use ``llm=...`` in tests to inject a fake; otherwise the agent
        resolves the active provider/model from settings on first ``invoke``.

        ``redis_client`` is required for any real LLM call (the agent looks
        up the kicking analyst's ephemeral key in Redis). Tests that inject
        ``llm`` can omit it.
        """
        self._llm = llm
        self._provider = provider
        self._model_name = model_name
        self._redis = redis_client

    def _resolve_llm(
        self,
        *,
        acting_user_id: uuid.UUID,
        session: Session | None = None,
        engagement_id: uuid.UUID | None = None,
        role: AgentName = AgentName.strategic,
    ) -> tuple[Any, str, str]:
        """Build the LLM using the kicking analyst's ephemeral BYO key.

        ``acting_user_id`` is required and unambiguous — there is no
        engagement-creator fallback. If the kicker has no Redis-cached key
        for the resolved provider, :class:`NoProviderKeyError` propagates
        and the caller records the failure on the AgentExecution row
        (visible in the Costs tab).

        v1.24.0 — if a Settings > Configurations pinning exists for
        (user, engagement, role), it overrides ``default_provider_model``.
        Callers pass ``session`` + ``engagement_id`` when they have them
        (analyze_finding / provision_lease do). Tests that inject
        ``self._llm`` bypass this whole path.
        """
        if self._llm is not None:
            return (
                self._llm,
                self._provider or "test",
                self._model_name or "test",
            )
        provider = self._provider
        model_name = self._model_name
        if not (provider and model_name) and session is not None:
            resolved = resolve_agent_model(
                session,
                user_id=acting_user_id,
                engagement_id=engagement_id,
                role=role,
            )
            if resolved is not None:
                p, m = resolved
                if m:
                    model_name = m
                if p:
                    provider = p
        if not (provider and model_name):
            provider, model_name = default_provider_model()
        if self._redis is None:
            raise RuntimeError(
                "StrategicAgent needs a redis_client to resolve the "
                "acting analyst's BYO key — construct with "
                "StrategicAgent(redis_client=...)"
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

    def analyze_finding(
        self,
        session: Session,
        *,
        finding: Finding,
        trigger: AgentTrigger,
        acting_user_id: uuid.UUID,
        execution_id: uuid.UUID | None = None,
    ) -> tuple[AgentExecution, list[Suggestion]]:
        """Run Strategic over a finding and persist suggestions + execution row.

        ``acting_user_id`` is the kicking analyst — for ``finding`` trigger,
        the analyst whose run produced the finding; for ``manual``, the
        analyst who clicked Analyze. The BYO key lookup uses this id, not
        the engagement creator's.

        Caller commits the session — we add but don't commit so this composes
        cleanly inside an API request transaction.
        """
        engagement = session.get(Engagement, finding.engagement_id)
        if engagement is None:
            raise ValueError(f"finding {finding.id} has no engagement")
        execution = session.get(AgentExecution, execution_id) if execution_id else None
        if execution is not None:
            if execution.engagement_id != engagement.id or str(
                (execution.input or {}).get("finding_id")
            ) != str(finding.id):
                raise ValueError("Strategic execution identity does not match finding event")
            if execution.status != AgentExecutionStatus.running:
                # Receipt completion may have been interrupted after the
                # execution already reached a terminal state. Never create a
                # second accounting row or overwrite cancellation.
                return execution, []
        # Token-saving kill-switch: skip automatic background generation when
        # the engagement has auto-assess disabled. The manual Analyze button
        # (trigger=manual) is an explicit user action and stays unaffected.
        if trigger != AgentTrigger.manual and not engagement.auto_assess_enabled:
            structlog.get_logger(__name__).info(
                "strategic.auto_assess_skipped", engagement_id=str(engagement.id)
            )
            now = datetime.now(tz=UTC)
            if execution is None:
                execution = AgentExecution(
                    id=execution_id,
                    engagement_id=engagement.id,
                    agent=AgentName.strategic,
                    trigger=trigger,
                    input={"finding_id": str(finding.id), "auto_assess_disabled": True},
                    status=AgentExecutionStatus.cancelled,
                    started_at=now,
                    completed_at=now,
                )
                session.add(execution)
            else:
                execution.status = AgentExecutionStatus.cancelled
                execution.completed_at = now
            return execution, []
        scope_items = list(
            session.execute(
                select(ScopeItem).where(ScopeItem.engagement_id == engagement.id)
            ).scalars()
        )

        prompt = _build_user_prompt(engagement, finding, _scope_summary(scope_items))

        if execution is None:
            execution = AgentExecution(
                id=execution_id,
                engagement_id=engagement.id,
                agent=AgentName.strategic,
                trigger=trigger,
                input={
                    "finding_id": str(finding.id),
                    "engagement_slug": engagement.slug,
                },
                status=AgentExecutionStatus.running,
                started_at=datetime.now(tz=UTC),
            )
            session.add(execution)
        # Commit the one stable execution identity for Status visibility. A
        # receipt-bound replay reloads this same running row rather than
        # creating another accounting entry.
        session.commit()
        session.refresh(execution)

        try:
            # BYO key: Strategic uses the KICKING analyst's ephemeral key.
            # No engagement-creator fallback — that was the cross-user
            # reuse bug. If the kicker has no key cached in Redis, this
            # raises NoProviderKeyError and lands as a failed
            # AgentExecution surfacing "re-upload at /settings/keys".
            llm, provider, model_name = self._resolve_llm(
                acting_user_id=acting_user_id,
                session=session,
                engagement_id=engagement.id,
                role=AgentName.strategic,
            )
            execution.model_provider = provider
            execution.model_name = model_name
            structured = llm.with_structured_output(_StrategicProposal)
            messages = [
                ("system", STRATEGIC_SYSTEM_PROMPT),
                ("user", prompt),
            ]
            raw_response: Any = structured.invoke(messages)
            # with_structured_output gives us back the parsed Pydantic model
            # directly. Token counting needs the raw response; some langchain
            # versions wrap with .with_raw_response so the parsed model has
            # the metadata attached. We try our best, ignore if missing.
            proposal: _StrategicProposal = (
                raw_response
                if isinstance(raw_response, _StrategicProposal)
                else _StrategicProposal.model_validate(raw_response)
            )
            tokens_in, tokens_out = _extract_usage(raw_response)
            execution.tokens_in = tokens_in
            execution.tokens_out = tokens_out
        except Exception as exc:  # noqa: BLE001 — any LLM failure → mark failed
            # Serialize with synchronous cancellation. If the analyst already
            # cancelled, preserve that terminal state instead of overwriting it.
            session.refresh(execution, with_for_update=True)
            if execution.status == AgentExecutionStatus.cancelled:
                return execution, []
            execution.status = AgentExecutionStatus.failed
            execution.error = str(exc)[:2000]
            execution.completed_at = datetime.now(tz=UTC)
            logger.warning(
                "strategic.failed",
                finding_id=str(finding.id),
                error=str(exc),
            )
            return execution, []

        # Cancellation and terminal effects share this row lock. A cancellation
        # that committed during the LLM call wins; otherwise completion and its
        # suggestions commit atomically before a later cancel can observe it.
        session.refresh(execution, with_for_update=True)
        if execution.status == AgentExecutionStatus.cancelled:
            return execution, []
        execution.model_provider = provider
        execution.model_name = model_name
        execution.tokens_in = tokens_in
        execution.tokens_out = tokens_out

        suggestions = self._persist_suggestions(
            session,
            engagement_id=engagement.id,
            finding_id=finding.id,
            proposal=proposal,
        )

        execution.output = {
            "summary": proposal.summary,
            "suggestion_ids": [str(s.id) for s in suggestions],
            "rejected_exploit_count": sum(
                1 for t in proposal.tasks if t.kind == TaskKind.exploit
            ),
        }
        execution.status = AgentExecutionStatus.completed
        execution.completed_at = datetime.now(tz=UTC)

        return execution, suggestions

    def provision_lease(
        self,
        session: Session,
        *,
        task: Any,
        acting_user_id: uuid.UUID,
        ttl_seconds: int = 3600,
        requires_container: bool | None = None,
    ) -> Any:
        """Stage 1 of per-task MCP composition: Strategic curates the
        tool/context/prompt surface for one Execution Agent run via tool
        packs keyed by ``task.kind``, then mints an active lease record.
        The lease's id is the bearer token Tactical stamps on the worker
        envelope. Caller commits the session.

        Stage 2 added ``requires_container`` — when True, Tactical points
        the worker at the secondary scale-to-zero MCP App.

        Stage 3 adds the LLM policy call: by default this method asks
        Strategic to narrow the pack default tool list and decide
        ``requires_container`` via an LLM call. ``requires_container``
        passed explicitly (or as a tests-only override) bypasses the LLM
        entirely — useful for callers who already know what they want.
        Failure of the LLM call is non-fatal: pack defaults + the
        conservative ``_decide_requires_container`` seed are used and a
        failed ``AgentExecution`` is recorded so the analyst can see why
        their key/prompt didn't fire.
        """
        # Local import keeps the orchestrator HTTP module from pulling the
        # lease service in at import time.
        from app.services import mcp_lease, tool_packs

        pack_defaults = tool_packs.tools_for_task(task)
        context = tool_packs.context_for_task(session, task)
        prompt_keys = tool_packs.prompts_for_task(task)

        if requires_container is None:
            allowed_tools, requires_container = self._provision_policy(
                session,
                task=task,
                pack_defaults=pack_defaults,
                acting_user_id=acting_user_id,
            )
        else:
            # Explicit override — bypass the LLM. Callers who pass this
            # already decided; we honor it verbatim and use pack defaults
            # for the tool surface.
            allowed_tools = pack_defaults

        return mcp_lease.mint(
            session,
            task=task,
            allowed_tools=allowed_tools,
            context=context,
            prompt_keys=prompt_keys,
            ttl_seconds=ttl_seconds,
            requires_container=requires_container,
        )

    def _decide_requires_container(self, task: Any) -> bool:
        """Conservative seed value used when the Stage 3 policy LLM call
        can't run (no provider key, LLM error). Returns False so leases
        keep flowing through the colocated path on failure.

        Tests monkeypatch this to flip the failure-fallback into a
        positive value without standing up a fake LLM.
        """
        return False

    def _provision_policy(
        self,
        session: Session,
        *,
        task: Any,
        pack_defaults: list[str],
        acting_user_id: uuid.UUID,
    ) -> tuple[list[str], bool]:
        """Stage 3: ask the LLM to narrow the pack and pick the container
        target. Returns ``(allowed_tools, requires_container)``. Writes
        an ``AgentExecution`` row regardless of success or failure so the
        Costs tab and audit log see the call.

        Failure modes (no provider key, LLM raise, structured-output
        validation error) are caught and reported via the execution row;
        the function falls back to ``(pack_defaults,
        _decide_requires_container(task))``.
        """
        dispatch_tool = (task.payload or {}).get("tool", "")
        engagement = session.get(Engagement, task.engagement_id)
        finding = (
            session.get(Finding, task.finding_id)
            if getattr(task, "finding_id", None) is not None
            else None
        )

        execution = AgentExecution(
            engagement_id=task.engagement_id,
            agent=AgentName.strategic,
            trigger=AgentTrigger.lease_provision,
            input={
                "task_id": str(task.id),
                "task_kind": task.kind.value,
                "dispatch_tool": dispatch_tool,
                "pack_defaults": list(pack_defaults),
            },
            status=AgentExecutionStatus.running,
            started_at=datetime.now(tz=UTC),
        )
        # Caller-owned transaction: never commit accepted suggestion/Task
        # state before Tactical has staged its lease and command outbox.
        session.add(execution)
        session.flush()

        try:
            llm, provider, model_name = self._resolve_llm(
                acting_user_id=acting_user_id,
                session=session,
                engagement_id=task.engagement_id,
                role=AgentName.strategic,
            )
            execution.model_provider = provider
            execution.model_name = model_name
            user_prompt = _build_lease_policy_user_prompt(
                engagement=engagement,
                task=task,
                pack_defaults=pack_defaults,
                dispatch_tool=dispatch_tool,
                finding=finding,
                scope_items=list(
                    session.execute(
                        select(ScopeItem).where(
                            ScopeItem.engagement_id == task.engagement_id
                        )
                    ).scalars()
                ),
            )
            structured = llm.with_structured_output(_LeasePolicy)
            raw: Any = structured.invoke(
                [
                    ("system", LEASE_POLICY_SYSTEM_PROMPT),
                    ("user", user_prompt),
                ]
            )
            policy: _LeasePolicy = (
                raw if isinstance(raw, _LeasePolicy) else _LeasePolicy.model_validate(raw)
            )
            tokens_in, tokens_out = _extract_usage(raw)
            execution.tokens_in = tokens_in
            execution.tokens_out = tokens_out
        except Exception as exc:  # noqa: BLE001 — any failure → fall back safely
            execution.status = AgentExecutionStatus.failed
            execution.error = str(exc)[:2000]
            execution.completed_at = datetime.now(tz=UTC)
            logger.warning(
                "strategic.lease_policy_failed",
                task_id=str(task.id),
                error=str(exc),
            )
            return list(pack_defaults), self._decide_requires_container(task)

        allowed_tools = self._narrow_to_pack(
            policy.tools,
            pack_defaults=pack_defaults,
            dispatch_tool=dispatch_tool,
        )
        execution.output = {
            "tools": allowed_tools,
            "requires_container": policy.requires_container,
            "reason": policy.reason,
            "llm_proposed_tools": list(policy.tools),
        }
        execution.status = AgentExecutionStatus.completed
        execution.completed_at = datetime.now(tz=UTC)
        logger.info(
            "strategic.lease_policy",
            task_id=str(task.id),
            requires_container=policy.requires_container,
            tools_kept=len(allowed_tools),
            tools_dropped=max(0, len(pack_defaults) - len(allowed_tools)),
        )
        return allowed_tools, policy.requires_container

    def _narrow_to_pack(
        self,
        llm_tools: list[str],
        *,
        pack_defaults: list[str],
        dispatch_tool: str,
    ) -> list[str]:
        """Narrow-only filter: keep order-preserving intersection of
        ``llm_tools`` and ``pack_defaults``. Drops any tool the LLM
        invented (widening attempt) or that the registry no longer
        knows. Defense-in-depth drop on exploit-kind tools — packs
        already exclude them but a misconfigured pack shouldn't blow
        the charter.

        Always preserves ``dispatch_tool`` so the worker can execute
        the task, even if the LLM omitted it. If the dispatch tool
        isn't in pack defaults at all (unusual but possible if a caller
        constructs a task by hand), we still keep it — the alternative
        is a guaranteed worker failure.
        """
        from app.orchestrator.tools import get_tool

        pack_set = set(pack_defaults)
        seen: set[str] = set()
        narrowed: list[str] = []
        for name in llm_tools:
            if name in seen or name not in pack_set:
                continue
            spec = get_tool(name)
            if spec is None:
                continue
            if spec.kind == TaskKind.exploit:
                continue
            narrowed.append(name)
            seen.add(name)
        if dispatch_tool and dispatch_tool not in seen:
            narrowed.append(dispatch_tool)
        return narrowed

    def release_lease(
        self,
        session: Session,
        *,
        lease_id: uuid.UUID,
        reason: str,
    ) -> None:
        """Idempotent — safe to call on already-released or unknown leases."""
        from app.services import mcp_lease

        mcp_lease.release(session, lease_id=lease_id, reason=reason)

    def _persist_suggestions(
        self,
        session: Session,
        *,
        engagement_id: uuid.UUID,
        finding_id: uuid.UUID,
        proposal: _StrategicProposal,
    ) -> list[Suggestion]:
        suggestions: list[Suggestion] = []
        seen_keys: set[str] = set()
        for task in proposal.tasks:
            if task.kind not in _AGENT_TASK_KINDS:
                # Defense in depth: even if the LLM tries to propose exploit,
                # we silently drop it. The rejection count goes on the
                # execution.output for visibility.
                continue
            # Key by (tool, target, kind) — NOT finding_id — so the same
            # follow-up work (e.g. "Resolve cwa.example") dedupes across the
            # multiple findings that can reference one target, instead of
            # stacking one open suggestion per source finding. proposal_key
            # also backs the uq_suggestions_open_proposal_key unique index as a
            # backstop; we pre-check here to skip quietly rather than surface
            # IntegrityErrors.
            proposal_key = "strategic:" + hashlib.sha256(
                f"{task.tool}|{task.target}|{task.kind.value}".encode()
            ).hexdigest()[:24]
            if proposal_key in seen_keys:
                continue
            already_decided = session.execute(
                select(Suggestion.id).where(
                    Suggestion.engagement_id == engagement_id,
                    Suggestion.proposal_key == proposal_key,
                    # Block re-creation if this exact work is already OPEN
                    # (proposed) or was DISMISSED (analyst said no). Without
                    # the dismissed branch the strategic watcher re-created
                    # the same suggestion on every finding event, stacking
                    # duplicates the analyst had already rejected.
                    Suggestion.status.in_(
                        (SuggestionStatus.open, SuggestionStatus.dismissed)
                    ),
                )
            ).first()
            if already_decided:
                continue
            seen_keys.add(proposal_key)
            suggestion = Suggestion(
                engagement_id=engagement_id,
                finding_id=finding_id,
                title=task.title,
                body=task.rationale,
                kind=SuggestionKind.task,
                payload={
                    "tool": task.tool,
                    "target": task.target,
                    "task_kind": task.kind.value,
                    "owner_eligibility": task.owner_eligibility.value,
                },
                status=SuggestionStatus.open,
                created_by_agent=AgentName.strategic,
                proposal_key=proposal_key,
            )
            session.add(suggestion)
            session.flush()
            suggestions.append(suggestion)
        return suggestions
