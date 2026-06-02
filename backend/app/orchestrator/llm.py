"""LLM factory.

``default_llm()`` returns a ChatAnthropic bound to the registry's tool schemas.
Tests inject a fake by passing ``llm=...`` to ``build_graph``; that path never
imports langchain-anthropic and so doesn't require an API key.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.orchestrator.tools import ToolSpec, all_tools


def tool_schemas(registry: Mapping[str, ToolSpec] | None = None) -> list[dict[str, Any]]:
    """JSON-schema descriptions of every registered tool, for LLM tool-calling."""
    specs = list(registry.values()) if registry else all_tools()
    schemas: list[dict[str, Any]] = []
    for spec in specs:
        properties: dict[str, Any] = {spec.target_arg: {"type": "string"}}
        if spec.extra_properties:
            properties.update(spec.extra_properties)
        schemas.append(
            {
                "name": spec.name,
                "description": spec.description or f"{spec.name} tool",
                "input_schema": {
                    "type": "object",
                    "properties": properties,
                    "required": [spec.target_arg],
                },
            }
        )
    return schemas


def default_llm() -> Any:
    """Return a chat model bound to the tool registry.

    Provider is chosen by ``settings.llm_provider`` (env: ``LLM_PROVIDER``):

    - ``anthropic`` (default) — Claude API. Needs ``ANTHROPIC_API_KEY``;
      model from ``ANTHROPIC_MODEL`` (default ``claude-opus-4-7``).
    - ``ollama`` — local Ollama. Free; needs the ``ollama`` compose service
      running and the model pulled (e.g. ``ollama pull llama3.1:8b``).
    - ``azure`` — Azure OpenAI. Production target on AKS; needs the four
      ``AZURE_OPENAI_*`` env vars (endpoint, api key, deployment, api version).

    All client libs are imported lazily so swapping providers doesn't require
    every other lib to be installed. Tests inject a fake via ``build_graph(llm=)``
    and never reach this function.
    """
    from app.core.config import settings

    provider = settings.llm_provider.lower()

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        llm = ChatAnthropic(model=settings.anthropic_model, max_tokens=4096)
    elif provider == "ollama":
        from langchain_ollama import ChatOllama

        llm = ChatOllama(
            model=settings.ollama_model,
            base_url=settings.ollama_host,
        )
    elif provider == "azure":
        from langchain_openai import AzureChatOpenAI

        if not (settings.azure_openai_endpoint and settings.azure_openai_deployment):
            raise RuntimeError(
                "LLM_PROVIDER=azure requires AZURE_OPENAI_ENDPOINT and "
                "AZURE_OPENAI_DEPLOYMENT to be set."
            )
        llm = AzureChatOpenAI(
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key or None,
            azure_deployment=settings.azure_openai_deployment,
            api_version=settings.azure_openai_api_version,
        )
    else:
        raise ValueError(
            f"unknown LLM_PROVIDER {provider!r}; expected one of: "
            "anthropic, ollama, azure"
        )

    return llm.bind_tools(tool_schemas())
