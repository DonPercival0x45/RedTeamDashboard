"""Moonshot/Kimi first-class provider wiring (no live key required)."""
from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any

from app.agents import strategic
from app.orchestrator import llm as orchestrator_llm
from app.schemas.engagement import RunModel


class _FakeChatOpenAI:
    calls: list[dict[str, Any]] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.bound_tools: list[dict[str, Any]] | None = None
        self.__class__.calls.append(kwargs)

    def bind_tools(self, schemas: list[dict[str, Any]]) -> _FakeChatOpenAI:
        self.bound_tools = schemas
        return self


def test_moonshot_is_accepted_by_run_schema_and_inferred_from_kimi_model() -> None:
    selected = RunModel(provider="moonshot", name="kimi-k2-turbo-preview")
    assert selected.provider == "moonshot"
    assert selected.name == "kimi-k2-turbo-preview"


def test_moonshot_uses_openai_compatible_endpoint_in_both_factories(
    monkeypatch,
) -> None:
    fake_module = SimpleNamespace(ChatOpenAI=_FakeChatOpenAI)
    monkeypatch.setitem(sys.modules, "langchain_openai", fake_module)
    _FakeChatOpenAI.calls.clear()

    tool_bound = orchestrator_llm.make_llm(
        "moonshot",
        "kimi-k2-turbo-preview",
        api_key="test-key",
    )
    strategic_model = strategic._make_chat_model(  # noqa: SLF001
        "moonshot",
        "kimi-k2-turbo-preview",
        api_key="test-key",
    )

    expected = "https://api.moonshot.cn/v1"
    assert tool_bound.kwargs["base_url"] == expected
    assert tool_bound.bound_tools is not None
    assert strategic_model.kwargs["base_url"] == expected
    assert len(_FakeChatOpenAI.calls) == 2


def test_moonshot_endpoint_can_be_overridden_for_region_or_gateway(monkeypatch) -> None:
    monkeypatch.setitem(
        sys.modules,
        "langchain_openai",
        SimpleNamespace(ChatOpenAI=_FakeChatOpenAI),
    )
    model = orchestrator_llm.make_llm(
        "moonshot",
        "moonshot-v1-128k",
        api_key="test-key",
        endpoint="https://gateway.example/v1",
    )
    assert model.kwargs["base_url"] == "https://gateway.example/v1"
