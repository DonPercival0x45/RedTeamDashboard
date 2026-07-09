"""Resolve which (provider, model) an agent should use for a given
(user, engagement, role) tuple, per v1.24.0 Settings > Configurations.

Chain:
  1. AgentModelPreference row for (user, engagement, role) -> use that
  2. users.default_model on the acting user -> use that (with default
     provider from settings)
  3. None -> caller falls back to hardcoded default_provider_model()

Model strings can be stored bare (``claude-opus-4-8``) or provider-qualified
(``anthropic:claude-opus-4-8``). The qualified form wins when present;
otherwise the provider is inferred from the model-name prefix. Unknown
prefixes fall back to the settings default provider so agents keep
running instead of hard-failing on an analyst typo.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AgentModelPreference, AgentName, User


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
            return (provider, model)

    # 2. User default (added by Kendall in v1.13.0). Column is called
    #    ``default_model`` on the users table.
    user = session.get(User, user_id)
    if user is not None:
        default = getattr(user, "default_model", None)
        if default:
            provider, model = parse_model_string(default)
            if provider is None:
                provider = provider_for_model(model)
            return (provider, model)

    # 3. No preference and no user default — caller falls back to the
    #    process-wide default_provider_model().
    return None
