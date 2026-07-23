"""Liveness + model-discovery probe for BYO provider keys.

Given a ``(provider, api_key, endpoint)`` triple, this hits the provider's
model-list endpoint. A ``200`` does double duty: it proves the key is
alive *and* returns the catalog of model ids the analyst can pick from —
so the ``/settings/keys`` form can offer a **Test** button and a
**dynamic model dropdown** instead of asking the analyst to hand-type a
model name (roadmap item ``019f1eae-...``, parts 1 & 2).

Model-list endpoints, per provider family:

* ``anthropic``  — ``GET {base}/v1/models`` with ``x-api-key`` +
  ``anthropic-version`` headers; ``{"data": [{"id": ...}]}``.
* ``openai`` and every OpenAI-compatible vendor (xai, together, groq,
  deepseek, mistral, google, cohere, moonshot, custom) — ``GET {base}/models`` with
  ``Authorization: Bearer``; ``{"data": [{"id": ...}]}``.
* ``ollama``    — ``GET {base}/api/tags`` (keyless); ``{"models":
  [{"name": ...}]}``.
* ``azure``     — deployment-based; a portable model list isn't available
  from the data-plane, so we report ``supported=False`` rather than guess.

The probe never persists anything and never echoes the key back — it
returns only booleans, a latency number, the discovered model ids, and an
error string on failure.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import httpx
import structlog

from app.orchestrator.llm import _OPENAI_COMPATIBLE_BASES

logger = structlog.get_logger(__name__)

TIMEOUT_S = 15.0
# Providers whose model list we can enumerate from the data plane.
_ANTHROPIC_VERSION = "2023-06-01"


@dataclass
class ProbeResult:
    """Outcome of one probe. ``ok`` = reachable AND authorized AND we got a
    usable response. ``reachable`` = we got *some* HTTP response (so the
    endpoint host resolves and answers), even if auth failed."""

    ok: bool
    reachable: bool
    supported: bool = True
    status_code: int | None = None
    latency_ms: int | None = None
    models: list[str] = field(default_factory=list)
    checked_url: str | None = None
    error: str | None = None


# ``openai`` itself isn't in ``_OPENAI_COMPATIBLE_BASES`` (that map covers
# only the *other* OpenAI-compatible vendors), so pin its canonical base
# here alongside them.
_OPENAI_DEFAULT_BASE = "https://api.openai.com/v1"


def _openai_base(provider: str, endpoint: str | None) -> str | None:
    """Resolve the OpenAI-compatible base URL for ``provider``. An analyst-
    supplied endpoint always wins over the built-in default; ``custom`` has
    no default so it *must* carry one."""
    if endpoint:
        return endpoint.rstrip("/")
    if provider == "openai":
        return _OPENAI_DEFAULT_BASE
    default = _OPENAI_COMPATIBLE_BASES.get(provider)
    return default.rstrip("/") if default else None


def _parse_openai_models(payload: object) -> list[str]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if not isinstance(data, list):
        return []
    ids = [
        str(row["id"])
        for row in data
        if isinstance(row, dict) and row.get("id")
    ]
    return sorted(set(ids))


def _parse_ollama_models(payload: object) -> list[str]:
    if not isinstance(payload, dict):
        return []
    models = payload.get("models")
    if not isinstance(models, list):
        return []
    names = [
        str(row["name"])
        for row in models
        if isinstance(row, dict) and row.get("name")
    ]
    return sorted(set(names))


def _get_json(url: str, headers: dict[str, str]) -> tuple[int, object]:
    resp = httpx.get(url, headers=headers, timeout=TIMEOUT_S)
    status = resp.status_code
    try:
        body = resp.json()
    except ValueError:
        body = None
    return status, body


def probe(
    provider: str,
    *,
    api_key: str | None,
    endpoint: str | None,
    is_local: bool = False,
    ollama_host: str = "http://ollama:11434",
) -> ProbeResult:
    """Probe ``provider`` for liveness + model list. Never raises — every
    failure mode is folded into a ``ProbeResult`` the API returns as-is."""
    provider = (provider or "").strip().lower()

    if provider == "azure":
        return ProbeResult(
            ok=False,
            reachable=False,
            supported=False,
            error=(
                "Azure OpenAI uses deployment names, not a portable model "
                "list — enter your deployment as the model."
            ),
        )

    if provider == "ollama":
        base = (endpoint or ollama_host).rstrip("/")
        url = f"{base}/api/tags"
        parser = _parse_ollama_models
        headers: dict[str, str] = {}
    elif provider == "anthropic":
        base = (endpoint or "https://api.anthropic.com").rstrip("/")
        url = f"{base}/v1/models"
        parser = _parse_openai_models
        headers = {
            "x-api-key": api_key or "",
            "anthropic-version": _ANTHROPIC_VERSION,
        }
    else:
        resolved_base = _openai_base(provider, endpoint)
        if not resolved_base:
            return ProbeResult(
                ok=False,
                reachable=False,
                error=(
                    f"provider {provider!r} needs an endpoint (API base URL) "
                    "before it can be tested."
                ),
            )
        url = f"{resolved_base}/models"
        parser = _parse_openai_models
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    if not api_key and not is_local and provider != "ollama":
        return ProbeResult(
            ok=False,
            reachable=False,
            checked_url=url,
            error="no API key to test — supply the key first.",
        )

    started = time.monotonic()
    try:
        status, body = _get_json(url, headers)
    except httpx.HTTPError as exc:
        logger.info("provider_probe.unreachable", provider=provider, error=str(exc))
        return ProbeResult(
            ok=False,
            reachable=False,
            checked_url=url,
            error=f"could not reach {url}: {exc}",
        )
    latency_ms = int((time.monotonic() - started) * 1000)

    if status == 200:
        models = parser(body)
        return ProbeResult(
            ok=True,
            reachable=True,
            status_code=status,
            latency_ms=latency_ms,
            models=models,
            checked_url=url,
            error=None if models else "reachable, but no models were listed.",
        )

    # Reachable but the provider rejected the request. Surface the common
    # cases in plain language; fall back to the status code otherwise.
    if status in (401, 403):
        reason = "key rejected (unauthorized) — check the key and endpoint."
    elif status == 404:
        reason = "endpoint has no model-list route (404) — check the base URL."
    elif status == 429:
        reason = "rate-limited (429) — the key is valid but throttled."
    else:
        reason = f"provider returned HTTP {status}."
    return ProbeResult(
        ok=False,
        reachable=True,
        status_code=status,
        latency_ms=latency_ms,
        checked_url=url,
        error=reason,
    )
