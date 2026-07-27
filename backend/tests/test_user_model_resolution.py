"""Regression coverage for acting-user model selection.

User-triggered helpers must honor the user's live default before the process
fallback so a non-Anthropic analyst is never asked for an Anthropic key merely
because the installation default is Anthropic.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

from app.models import AgentName, User
from app.services import agent_model_resolver as resolver


class _Session:
    def __init__(self, user: Any) -> None:
        self.user = user

    def get(self, model: type[Any], row_id: uuid.UUID) -> Any:
        assert model is User
        assert row_id == self.user.id
        return self.user


def _user(*, provider: str | None, model: str | None) -> Any:
    return SimpleNamespace(
        id=uuid.uuid4(),
        default_llm_provider=provider,
        default_llm_model=model,
    )


def test_generic_user_workflow_prefers_non_anthropic_default(
    monkeypatch,
) -> None:
    user = _user(provider="moonshot", model="kimi-k2-turbo-preview")
    monkeypatch.setattr(
        "app.orchestrator.llm.default_provider_model",
        lambda: ("anthropic", "claude-opus-4-7"),
    )

    assert resolver.resolve_user_model_with_default(
        _Session(user), user_id=user.id
    ) == ("moonshot", "kimi-k2-turbo-preview")


def test_qualified_user_model_overrides_stored_provider(monkeypatch) -> None:
    user = _user(provider="anthropic", model="openai:gpt-4o-mini")
    monkeypatch.setattr(
        "app.orchestrator.llm.default_provider_model",
        lambda: ("anthropic", "claude-opus-4-7"),
    )

    assert resolver.resolve_user_row_model_with_default(user) == (
        "openai",
        "gpt-4o-mini",
    )


def test_generic_user_workflow_uses_process_fallback_without_user_default(
    monkeypatch,
) -> None:
    user = _user(provider=None, model=None)
    monkeypatch.setattr(
        "app.orchestrator.llm.default_provider_model",
        lambda: ("ollama", "llama3.1:8b"),
    )

    assert resolver.resolve_user_model_with_default(
        _Session(user), user_id=user.id
    ) == ("ollama", "llama3.1:8b")


def test_role_helper_uses_user_default_when_no_role_preference(
    monkeypatch,
) -> None:
    user = _user(provider="openai", model="gpt-4o-mini")
    session = _Session(user)
    monkeypatch.setattr(
        resolver,
        "resolve_agent_model",
        lambda *_args, **_kwargs: ("openai", "gpt-4o-mini"),
    )
    monkeypatch.setattr(
        "app.orchestrator.llm.default_provider_model",
        lambda: ("anthropic", "claude-opus-4-7"),
    )

    assert resolver.resolve_agent_model_with_default(
        session,
        user_id=user.id,
        engagement_id=uuid.uuid4(),
        role=AgentName.triage,
    ) == ("openai", "gpt-4o-mini")
