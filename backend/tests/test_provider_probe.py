"""Provider-key probe — liveness test + dynamic model discovery.

Roadmap item ``019f1eae-...`` parts 1 & 2: a Test button that pings the
key + endpoint, and dynamic model discovery so the analyst doesn't
hand-type a model name. Both collapse into one model-list call — a 200
proves the key is alive *and* returns the catalog.

These tests hit ``provider_probe.probe`` directly with ``httpx.get``
mocked, so they need no network and no Redis. Endpoint wiring
(``POST /me/provider-keys/probe``) is covered by CI's DB/Redis-backed
suite; here we pin the pure decision logic.
"""
from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from app.services import provider_probe


class _Resp:
    def __init__(self, status_code: int, data: object) -> None:
        self.status_code = status_code
        self._data = data

    def json(self) -> object:
        if self._data is _NO_JSON:
            raise ValueError("no json body")
        return self._data


_NO_JSON = object()


def _mock_get(resp: _Resp):
    return patch("app.services.provider_probe.httpx.get", return_value=resp)


# ── happy path ────────────────────────────────────────────────────────────


def test_openai_200_lists_models() -> None:
    payload = {"data": [{"id": "gpt-4o"}, {"id": "o1"}, {"id": "gpt-4o"}]}
    with _mock_get(_Resp(200, payload)):
        r = provider_probe.probe("openai", api_key="sk-x", endpoint=None)
    assert r.ok and r.reachable
    assert r.models == ["gpt-4o", "o1"]  # sorted + de-duped
    assert r.checked_url == "https://api.openai.com/v1/models"
    assert r.latency_ms is not None


def test_anthropic_uses_versioned_models_route() -> None:
    payload = {"data": [{"id": "claude-opus-4"}]}
    with patch(
        "app.services.provider_probe.httpx.get", return_value=_Resp(200, payload)
    ) as m:
        r = provider_probe.probe("anthropic", api_key="sk-ant", endpoint=None)
    assert r.ok and r.models == ["claude-opus-4"]
    url = m.call_args.args[0]
    headers = m.call_args.kwargs["headers"]
    assert url == "https://api.anthropic.com/v1/models"
    assert headers["x-api-key"] == "sk-ant"
    assert "anthropic-version" in headers


def test_moonshot_uses_kimi_model_list_route() -> None:
    payload = {"data": [{"id": "kimi-k2-turbo-preview"}]}
    with patch(
        "app.services.provider_probe.httpx.get", return_value=_Resp(200, payload)
    ) as request:
        result = provider_probe.probe("moonshot", api_key="sk-kimi", endpoint=None)
    assert result.ok and result.models == ["kimi-k2-turbo-preview"]
    assert request.call_args.args[0] == "https://api.moonshot.cn/v1/models"
    assert request.call_args.kwargs["headers"]["Authorization"] == "Bearer sk-kimi"


def test_ollama_is_keyless_and_parses_tags() -> None:
    payload = {"models": [{"name": "llama3.1:8b"}, {"name": "qwen2:7b"}]}
    with _mock_get(_Resp(200, payload)):
        r = provider_probe.probe("ollama", api_key=None, endpoint=None)
    assert r.ok
    assert r.models == ["llama3.1:8b", "qwen2:7b"]


def test_reachable_but_empty_model_list() -> None:
    with _mock_get(_Resp(200, {"data": []})):
        r = provider_probe.probe("openai", api_key="sk-x", endpoint=None)
    assert r.ok and r.reachable and r.models == []
    assert r.error and "no models" in r.error


# ── auth / reachability failures ──────────────────────────────────────────


@pytest.mark.parametrize(
    ("status", "needle"),
    [(401, "unauthorized"), (403, "unauthorized"), (404, "404"), (429, "throttled")],
)
def test_reachable_but_rejected(status: int, needle: str) -> None:
    with _mock_get(_Resp(status, {"error": "x"})):
        r = provider_probe.probe("openai", api_key="bad", endpoint=None)
    assert not r.ok and r.reachable
    assert r.status_code == status
    assert needle in (r.error or "")


def test_unreachable_host() -> None:
    with patch(
        "app.services.provider_probe.httpx.get",
        side_effect=httpx.ConnectError("boom"),
    ):
        r = provider_probe.probe("groq", api_key="x", endpoint=None)
    assert not r.ok and not r.reachable
    assert "could not reach" in (r.error or "")


# ── guards that never touch the network ───────────────────────────────────


def test_azure_reported_unsupported() -> None:
    r = provider_probe.probe("azure", api_key="x", endpoint="https://e")
    assert not r.ok and not r.supported
    assert "deployment" in (r.error or "")


def test_custom_without_endpoint_is_rejected() -> None:
    r = provider_probe.probe("custom", api_key="x", endpoint=None)
    assert not r.ok and not r.reachable
    assert "endpoint" in (r.error or "")


def test_missing_key_for_remote_provider() -> None:
    r = provider_probe.probe("openai", api_key=None, endpoint=None)
    assert not r.ok
    assert "no API key" in (r.error or "")


def test_endpoint_override_wins_over_default() -> None:
    with patch(
        "app.services.provider_probe.httpx.get", return_value=_Resp(200, {"data": []})
    ) as m:
        provider_probe.probe(
            "openai", api_key="sk-x", endpoint="https://proxy.internal/v1"
        )
    assert m.call_args.args[0] == "https://proxy.internal/v1/models"
