"""Resolve which (provider, model) an agent should use for a given
(user, engagement, role) tuple, per v1.24.0 Settings > Configurations.

Chain:
  1. AgentModelPreference row for (user, engagement, role) -> use that
  2. users.default_llm_model (+ users.default_llm_provider) on the acting
     user -> use that
  3. None -> caller falls back to hardcoded default_provider_model()

Model strings can be stored bare (``claude-opus-4-8``) or provider-qualified
(``anthropic:claude-opus-4-8``). The qualified form wins when present;
otherwise the provider is inferred from the model-name prefix. Unknown
prefixes fall back to the settings default provider so agents keep
running instead of hard-failing on an analyst typo.
"""
from __future__ import annotations

import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AgentModelPreference, AgentName, User

logger = structlog.get_logger(__name__)


def provider_for_model(model: str) -> str | None:
    """Best-effort inference: model prefix -> provider slug.

    Kept intentionally narrow — only the vendor patterns we've actually
    routed at run-time land here. When you add a provider to the LLM
    factory in ``strategic.py``, add the matching prefix here so config
    entries typed without a ``provider:`` qualifier still route.
    """
    if not model:
        return None
    m = model.lower().strip()
    # Anthropic family — claude-*, plus Anthropic's model-family aliases.
    if m.startswith("claude") or m.startswith(("opus", "sonnet", "haiku", "fable")):
        return "anthropic"
    if m.startswith("gpt-") or m.startswith("o1") or m.startswith("o3"):
        return "openai"
    if m.startswith("grok"):
        return "xai"
    if m.startswith("deepseek"):
        return "deepseek"
    if m.startswith("mistral") or m.startswith("codestral"):
        return "mistral"
    if m.startswith("gemini") or m.startswith("google/"):
        return "google"
    if m.startswith("command"):
        return "cohere"
    if m.startswith("llama") or m.startswith("qwen") or m.startswith("phi"):
        # Ollama-shaped local models — safe default. Analyst can override
        # with an explicit ``ollama:`` prefix.
        return "ollama"
    return None


def parse_model_string(model: str) -> tuple[str | None, str]:
    """Split ``provider:model`` into ``(provider, model)``. Bare strings
    return ``(None, model)`` and let the caller infer or fall through."""
    if ":" in model:
        head, _, tail = model.partition(":")
        head = head.strip().lower()
        tail = tail.strip()
        if head and tail:
            return head, tail
    return None, model.strip()


def resolve_agent_model(
    session: Session,
    *,
    user_id: uuid.UUID,
    engagement_id: uuid.UUID | None,
    role: AgentName,
) -> tuple[str | None, str] | None:
    """Return ``(provider, model_name)`` for this (user, engagement, role)
    or ``None`` if no preference is set and no user default is available.

    ``engagement_id`` is ``None`` for engagement-less agents (planner
    today; kept optional so this resolver stays reusable). When engagement
    is None we short-circuit past the preference table since preferences
    are engagement-scoped.
    """
    # 1. Preference row wins.
    if engagement_id is not None:
        pref = session.execute(
            select(AgentModelPreference).where(
                AgentModelPreference.user_id == user_id,
                AgentModelPreference.engagement_id == engagement_id,
                AgentModelPreference.agent_role == role,
            )
        ).scalar_one_or_none()
        if pref is not None:
            provider, model = parse_model_string(pref.model)
            if provider is None:
                provider = provider_for_model(model)
            # v2.25.2: log which layer (pref/user-default/process-default)
            # actually picked the model. When an analyst reports "config
            # didn't stick", this tells us whether the pref row matched.
            logger.info(
                "agent_model.resolved",
                source="engagement_pref",
                user_id=str(user_id),
                engagement_id=str(engagement_id),
                role=role.value if hasattr(role, "value") else str(role),
                pref_id=str(pref.id),
                stored_model=pref.model,
                provider=provider,
                model=model,
            )
            return (provider, model)

    # 2. User default. The user-level default lives in TWO columns on the
    #    users table — ``default_llm_provider`` + ``default_llm_model``
    #    (user.py:51-52), NOT a single ``default_model`` string.
    #
    #    v2.25.3: this tier was dead code from v1.24.0 until now. The old
    #    line did ``getattr(user, "default_model", None)`` against a column
    #    that never existed, so it ALWAYS returned None and every tier-1
    #    miss fell straight through to the process default. That's the root
    #    cause of "config didn't stick" (Kendall's gpt-4o-mini pin lost to
    #    the process default Sol whenever the dispatch's acting_user_id
    #    wasn't the pinning analyst). Read the real columns: the provider
    #    column wins when set; otherwise infer from the model-name prefix.
    user = session.get(User, user_id)
    if user is not None:
        default_model = getattr(user, "default_llm_model", None)
        if default_model:
            stored_provider = getattr(user, "default_llm_provider", None)
            provider, model = parse_model_string(default_model)
            if provider is None:
                provider = (stored_provider or "").strip().lower() or None
            if provider is None:
                provider = provider_for_model(model)
            logger.info(
                "agent_model.resolved",
                source="user_default",
                user_id=str(user_id),
                engagement_id=str(engagement_id) if engagement_id else None,
                role=role.value if hasattr(role, "value") else str(role),
                stored_model=default_model,
                stored_provider=stored_provider,
                provider=provider,
                model=model,
            )
            return (provider, model)

    # 3. No preference and no user default — caller falls back to the
    #    process-wide default_provider_model().
    logger.info(
        "agent_model.resolved",
        source="process_default_fallback",
        user_id=str(user_id),
        engagement_id=str(engagement_id) if engagement_id else None,
        role=role.value if hasattr(role, "value") else str(role),
    )
    return None
