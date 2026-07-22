"""LLM factory.

``make_llm(provider, model_name)`` returns a tool-bound chat model for the
requested provider+model. ``default_llm()`` is sugar that reads provider +
model from ``settings`` — used when a run doesn't pick one explicitly.

Tests inject a fake by passing ``llm=...`` to ``build_graph`` and never reach
this module.

v0.8.1: providers expanded to match the /settings/keys Quick Add list (12
total). The 8 OpenAI-compatible vendors (xAI, Together, Groq, DeepSeek,
Mistral, Google, Cohere, Custom) route through ChatOpenAI with a
per-provider base_url — no new langchain packages required.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.orchestrator.tools import ToolSpec, all_tools

# v0.8.1: per-provider default base URLs for OpenAI-compatible vendors.
# An analyst-supplied endpoint on their BYO key wins over these defaults.
# ``custom`` has no default — the analyst MUST upload an endpoint.
_OPENAI_COMPATIBLE_BASES: dict[str, str] = {
    "xai": "https://api.x.ai/v1",
    "together": "https://api.together.xyz/v1",
    "groq": "https://api.groq.com/openai/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "mistral": "https://api.mistral.ai/v1",
    "google": "https://generativelanguage.googleapis.com/v1beta/openai",
    "cohere": "https://api.cohere.com/compatibility/v1",
    "custom": "",
}


def _is_gpt5_family(model_name: str) -> bool:
    """True for OpenAI-shaped model names that langchain-openai auto-tags
    with ``reasoning_effort`` (gpt-5.*, o1, o3 today). Custom sibling models
    that share the ``gpt-5`` prefix (e.g. ``gpt-5.6-sol``, ``gpt-5.6-fable``)
    inherit the same auto-tagging quirk and the same downstream failure
    when the vendor's ``/v1/chat/completions`` refuses ``reasoning_effort``
    alongside function tools.

    v2.25.1: added after Sol rejected the tool-bound call with
    ``Function tools with reasoning_effort are not supported for
    gpt-5.6-sol in /v1/chat/completions`` — the vendor's own remediation
    is ``set reasoning_effort to 'none'`` (or switch to /v1/responses).
    """
    m = (model_name or "").lower().strip()
    return m.startswith("gpt-5") or m.startswith("o1") or m.startswith("o3")


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


def make_llm(
    provider: str,
    model_name: str,
    *,
    api_key: str | None = None,
    endpoint: str | None = None,
    registry: Mapping[str, ToolSpec] | None = None,
) -> Any:
    """Return a tool-bound chat model for an explicit (provider, model_name).

    Client libs are imported lazily so swapping providers doesn't require
    every other lib to be installed.

    ``api_key`` / ``endpoint`` are the BYO-key wiring path: when present,
    they override the env defaults (resolved per-run from the kicking
    analyst's ephemeral Redis cache by
    ``app.services.ephemeral_provider_key``). When omitted, the LLM
    constructors fall back to their library's env-var auto-detection so
    the existing test paths still work.

    ``registry`` narrows the tool surface bound to the LLM — Stage 1 of the
    MCP lease wiring filtered the dispatch node but left the LLM seeing
    every tool. Threading the lease's allowed_tools through here closes
    that gap so the agent only ever proposes leased tools.
    """
    provider = provider.lower()

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        kwargs: dict[str, Any] = {"model": model_name, "max_tokens": 4096}
        if api_key:
            kwargs["api_key"] = api_key
        llm = ChatAnthropic(**kwargs)
    elif provider == "openai":
        from langchain_openai import ChatOpenAI

        kwargs = {"model": model_name}
        if api_key:
            kwargs["api_key"] = api_key
        if endpoint:
            kwargs["base_url"] = endpoint
        if _is_gpt5_family(model_name):
            # gpt-5-family models refuse ``reasoning_effort`` together with
            # function tools on ``/v1/chat/completions``. bind_tools() below
            # will fire, so we pre-emptively disable the reasoning flag.
            kwargs["reasoning_effort"] = "none"
        llm = ChatOpenAI(**kwargs)
    elif provider == "ollama":
        from langchain_ollama import ChatOllama

        from app.core.config import settings

        # Ollama is keyless; per-user endpoint override (analyst pointing at
        # their own local box) wins over the deployment default.
        llm = ChatOllama(
            model=model_name,
            base_url=endpoint or settings.ollama_host,
        )
    elif provider == "azure":
        from langchain_openai import AzureChatOpenAI

        from app.core.config import settings

        resolved_endpoint = endpoint or settings.azure_openai_endpoint
        resolved_key = api_key or settings.azure_openai_api_key or None
        if not (resolved_endpoint and settings.azure_openai_deployment):
            raise RuntimeError(
                "provider=azure requires endpoint + AZURE_OPENAI_DEPLOYMENT to be set."
            )
        # `model_name` for Azure is the *deployment* — usually pinned at
        # deploy time. We accept the run-supplied name as the deployment to
        # talk to, falling back to the env default.
        llm = AzureChatOpenAI(
            azure_endpoint=resolved_endpoint,
            api_key=resolved_key,
            azure_deployment=model_name or settings.azure_openai_deployment,
            api_version=settings.azure_openai_api_version,
        )
    elif provider in _OPENAI_COMPATIBLE_BASES:
        from langchain_openai import ChatOpenAI

        base = endpoint or _OPENAI_COMPATIBLE_BASES[provider]
        if not base:
            raise RuntimeError(
                f"provider={provider!r} requires an endpoint on the BYO "
                "key — re-upload at /settings/keys with the API base URL "
                "filled in."
            )
        kwargs = {"model": model_name, "base_url": base}
        if api_key:
            kwargs["api_key"] = api_key
        if _is_gpt5_family(model_name):
            kwargs["reasoning_effort"] = "none"
        llm = ChatOpenAI(**kwargs)
    else:
        raise ValueError(
            f"unknown LLM provider {provider!r}; expected one of: "
            "anthropic, openai, ollama, azure, google, xai, mistral, "
            "cohere, together, groq, deepseek, custom"
        )

    return llm.bind_tools(tool_schemas(registry))


def default_provider_model() -> tuple[str, str]:
    """Resolve ``settings``-derived (provider, model) for runs that don't pick one."""
    from app.core.config import settings

    provider = settings.llm_provider.lower()
    if provider == "anthropic":
        return provider, settings.anthropic_model
    if provider == "openai":
        return provider, settings.openai_model
    if provider == "ollama":
        return provider, settings.ollama_model
    if provider == "azure":
        return provider, settings.azure_openai_deployment
    raise ValueError(f"unknown settings.llm_provider {provider!r}")


def default_llm() -> Any:
    """Tool-bound chat model from settings defaults — backwards-compat shim."""
    provider, model = default_provider_model()
    return make_llm(provider, model)
