"""Regression tests for propagating the actual MRU fallback model."""
from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AgentExecution, AgentName, User
from app.services import tool_llm_review


def test_tool_review_attributes_actual_fallback_provider_and_model(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = User(email=f"tool-review-fallback-{uuid.uuid4().hex[:8]}@example.test")
    db.add(user)
    db.commit()
    db.refresh(user)
    captured: dict[str, Any] = {}
    tool_name = f"passive-test-{uuid.uuid4().hex[:8]}"

    monkeypatch.setattr(
        tool_llm_review,
        "resolve_agent_model_with_default",
        lambda *_args, **_kwargs: ("anthropic", "claude-preferred"),
    )

    def fallback(_redis: object, **kwargs: Any) -> tuple[str, str, object]:
        captured["preferred"] = kwargs
        return (
            "openai",
            "gpt-mru",
            SimpleNamespace(api_key="sk-mru", endpoint="https://mru.test/v1"),
        )

    monkeypatch.setattr(
        tool_llm_review, "resolve_for_user_with_fallback", fallback
    )

    class FakeLLM:
        def invoke(self, _messages: object) -> SimpleNamespace:
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "safe": True,
                        "reason": "matches the passive manifest",
                        "concerns": [],
                        "matches_stated_intent": True,
                    }
                ),
                response_metadata={},
            )

    def make_model(provider: str, model: str, **kwargs: Any) -> FakeLLM:
        captured["model"] = (provider, model, kwargs)
        return FakeLLM()

    monkeypatch.setattr(tool_llm_review, "_make_chat_model", make_model)
    monkeypatch.setattr(
        tool_llm_review.pricing,
        "cost_usd",
        lambda model, *_args, provider=None, **_kwargs: captured.setdefault(
            "pricing", (provider, model)
        )
        and 0.0,
    )

    result = tool_llm_review.review_tool_source(
        db,
        object(),
        source="print('passive')",
        kind="python",
        manifest={"risk_level": "passive"},
        tool_name=tool_name,
        acting_user_id=user.id,
    )

    execution = db.execute(
        select(AgentExecution).where(
            AgentExecution.agent == AgentName.tool_review,
            AgentExecution.input["tool_name"].astext == tool_name,
        )
    ).scalar_one()
    assert captured["preferred"] == {
        "user_id": user.id,
        "preferred_provider": "anthropic",
        "preferred_model": "claude-preferred",
    }
    assert captured["model"] == (
        "openai",
        "gpt-mru",
        {"api_key": "sk-mru", "endpoint": "https://mru.test/v1"},
    )
    assert captured["pricing"] == ("openai", "gpt-mru")
    assert result.model == "openai/gpt-mru"
    assert (execution.model_provider, execution.model_name) == (
        "openai",
        "gpt-mru",
    )
