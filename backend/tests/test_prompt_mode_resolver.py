"""Prompt-mode model resolver tests (v3 B4a).

Proves the §C.2 layered resolution keyed on the v3 ``mode`` axis:
mode-specific preference -> analyst's default_llm_model -> None. Mirrors v1's
``resolve_agent_model`` shape; the two coexist (v1's role-based resolver is
untouched).
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.models import (
    AgentModeModelPreference,
    AgentPromptMode,
    Engagement,
    EngagementStatus,
    EngagementWorkState,
    User,
    UserRole,
)
from app.services.agent_model_resolver import (
    resolve_llm_for_mode,
    resolve_model_for_mode,
    resolve_model_for_mode_with_default,
)


@pytest.fixture()
def engagement(db: Session) -> Engagement:
    eng = Engagement(
        name="Resolver Test",
        slug=f"rez-{uuid.uuid4().hex[:8]}",
        status=EngagementStatus.active,
        work_state=EngagementWorkState.active,
    )
    db.add(eng)
    db.flush()
    return eng


@pytest.fixture()
def user(db: Session) -> User:
    u = User(
        email=f"rez-{uuid.uuid4().hex[:6]}@example.com",
        role=UserRole.user,
    )
    db.add(u)
    db.flush()
    return u


def _pref(
    db: Session, user: User, engagement: Engagement, mode: AgentPromptMode, model: str
) -> AgentModeModelPreference:
    row = AgentModeModelPreference(
        user_id=user.id,
        engagement_id=engagement.id,
        mode=mode,
        model=model,
    )
    db.add(row)
    db.flush()
    return row


def test_mode_specific_preference_wins(db: Session, user: User, engagement: Engagement) -> None:
    _pref(db, user, engagement, AgentPromptMode.ideation, "anthropic:claude-opus-4")

    result = resolve_model_for_mode(
        db, user_id=user.id, engagement_id=engagement.id, mode=AgentPromptMode.ideation
    )

    assert result == ("anthropic", "claude-opus-4")


def test_mode_preference_does_not_leak_across_modes(
    db: Session, user: User, engagement: Engagement
) -> None:
    _pref(db, user, engagement, AgentPromptMode.ideation, "anthropic:claude-opus-4")

    # Analysis mode has no specific pref -> falls through (no user default here)
    result = resolve_model_for_mode(
        db, user_id=user.id, engagement_id=engagement.id, mode=AgentPromptMode.analysis
    )

    assert result is None


def test_falls_back_to_user_default_when_no_mode_pref(
    db: Session, user: User, engagement: Engagement
) -> None:
    user.default_llm_provider = "openai"
    user.default_llm_model = "gpt-4o-mini"
    db.flush()

    result = resolve_model_for_mode(
        db, user_id=user.id, engagement_id=engagement.id, mode=AgentPromptMode.strategy
    )

    assert result == ("openai", "gpt-4o-mini")


def test_mode_pref_beats_user_default(db: Session, user: User, engagement: Engagement) -> None:
    user.default_llm_model = "gpt-4o-mini"  # the weaker default
    _pref(db, user, engagement, AgentPromptMode.ideation, "anthropic:claude-opus-4")
    db.flush()

    result = resolve_model_for_mode(
        db, user_id=user.id, engagement_id=engagement.id, mode=AgentPromptMode.ideation
    )

    # Mode-specific pref wins over the user default.
    assert result == ("anthropic", "claude-opus-4")


def test_returns_none_with_no_pref_and_no_default(
    db: Session, user: User, engagement: Engagement
) -> None:
    result = resolve_model_for_mode(
        db, user_id=user.id, engagement_id=engagement.id, mode=AgentPromptMode.coverage_review
    )
    assert result is None


def test_bare_model_string_infers_provider(db: Session, user: User, engagement: Engagement) -> None:
    # Bare model name (no provider: prefix) -> provider inferred from prefix.
    _pref(db, user, engagement, AgentPromptMode.analysis, "claude-sonnet-4")

    result = resolve_model_for_mode(
        db, user_id=user.id, engagement_id=engagement.id, mode=AgentPromptMode.analysis
    )

    assert result == ("anthropic", "claude-sonnet-4")


def test_unknown_bare_model_keeps_name_and_uses_default_provider(
    db: Session,
    user: User,
    engagement: Engagement,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pref(db, user, engagement, AgentPromptMode.analysis, "bespoke-model")
    monkeypatch.setattr(
        "app.orchestrator.llm.default_provider_model",
        lambda: ("custom", "process-default-model"),
    )

    result = resolve_model_for_mode_with_default(
        db,
        user_id=user.id,
        engagement_id=engagement.id,
        mode=AgentPromptMode.analysis,
    )

    assert result == ("custom", "bespoke-model")


def test_llm_builder_uses_mode_preference_and_acting_user_key(
    db: Session,
    user: User,
    engagement: Engagement,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pref(db, user, engagement, AgentPromptMode.analysis, "openai:gpt-4o-mini")
    llm = object()
    captured: dict[str, Any] = {}
    redis_client = object()

    def resolve_key(redis: object, **kwargs: Any) -> SimpleNamespace:
        captured["redis"] = redis
        captured["key_kwargs"] = kwargs
        return SimpleNamespace(api_key="secret", endpoint="https://models.example/v1")

    def make_model(provider: str, model: str, **kwargs: Any) -> object:
        captured["model_args"] = (provider, model)
        captured["model_kwargs"] = kwargs
        return llm

    monkeypatch.setattr(
        "app.services.ephemeral_provider_key.resolve_for_user", resolve_key
    )
    monkeypatch.setattr("app.agents.strategic._make_chat_model", make_model)

    result = resolve_llm_for_mode(
        db,
        redis_client=redis_client,
        user_id=user.id,
        engagement_id=engagement.id,
        mode=AgentPromptMode.analysis,
    )

    assert result == (llm, "openai", "gpt-4o-mini")
    assert captured == {
        "redis": redis_client,
        "key_kwargs": {"user_id": user.id, "provider": "openai"},
        "model_args": ("openai", "gpt-4o-mini"),
        "model_kwargs": {
            "api_key": "secret",
            "endpoint": "https://models.example/v1",
        },
    }
