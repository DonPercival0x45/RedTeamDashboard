"""Platform-owned Suggestion Box evaluator configuration."""
from __future__ import annotations

import uuid

import pytest

from app.agents import planner
from app.core.config import settings
from app.services import roadmap_suggestions


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, int] = {}
        self.expirations: dict[str, int] = {}

    def incr(self, key: str) -> int:
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]

    def expire(self, key: str, seconds: int) -> None:
        self.expirations[key] = seconds


def test_planner_prefers_platform_key(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    sentinel = object()

    def fake_model(provider: str, model: str, **kwargs: object) -> object:
        captured.update(provider=provider, model=model, **kwargs)
        return sentinel

    monkeypatch.setattr(planner, "_make_chat_model", fake_model)
    monkeypatch.setattr(settings, "planner_provider", "openai")
    monkeypatch.setattr(settings, "planner_model", "gpt-4o-mini")
    monkeypatch.setattr(settings, "planner_api_key", "platform-secret")
    monkeypatch.setattr(settings, "planner_endpoint", "https://models.example/v1")

    model, provider, name = planner.PlanningAgent()._resolve_llm(
        acting_user_id=uuid.uuid4()
    )
    assert model is sentinel
    assert provider == "openai"
    assert name == "gpt-4o-mini"
    assert captured["api_key"] == "platform-secret"
    assert captured["endpoint"] == "https://models.example/v1"


def test_platform_planner_daily_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = FakeRedis()
    user_id = uuid.uuid4()
    monkeypatch.setattr(settings, "planner_api_key", "platform-secret")
    monkeypatch.setattr(settings, "planner_daily_limit_per_user", 2)

    roadmap_suggestions.enforce_planner_rate_limit(redis, user_id=user_id)
    roadmap_suggestions.enforce_planner_rate_limit(redis, user_id=user_id)
    with pytest.raises(roadmap_suggestions.PlannerRateLimitExceeded):
        roadmap_suggestions.enforce_planner_rate_limit(redis, user_id=user_id)
    assert next(iter(redis.expirations.values())) == 90_000
