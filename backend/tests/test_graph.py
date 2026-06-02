"""LangGraph OSINT runtime: gate-integrated dispatch + interrupt/resume flow.

The LLM is faked end-to-end with scripted ``AIMessage`` responses, so these
tests are deterministic and need no Anthropic API key.
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Iterable
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.types import Command

from app.models import RiskLevel, ScopeKind
from app.orchestrator import ToolSpec, build_graph
from app.orchestrator.scope import ScopeSnapshot
from app.orchestrator.tools.runtime import ToolResult
from tests._stub_tools import STUB_IMPLEMENTATIONS


class FakeLLM:
    """Scripted chat model. Pops the next AIMessage on each invoke()."""

    def __init__(self, scripted: Iterable[AIMessage]) -> None:
        self._queue: list[AIMessage] = list(scripted)
        self.calls: list[list[Any]] = []

    def invoke(self, input: Any, config: Any = None, **kwargs: Any) -> AIMessage:
        self.calls.append(list(input))
        if not self._queue:
            return AIMessage(content="(no more scripted responses)")
        return self._queue.pop(0)


def _scope_item(
    kind: ScopeKind, value: str, *, is_exclusion: bool = False
) -> ScopeSnapshot:
    return ScopeSnapshot(
        id=uuid.uuid4(),
        kind=kind,
        value=value,
        is_exclusion=is_exclusion,
    )


def _config() -> dict[str, Any]:
    return {"configurable": {"thread_id": str(uuid.uuid4())}}


def _portscan_overrides() -> tuple[dict[str, ToolSpec], dict[str, Any]]:
    """Inject an active tool so we can exercise the interrupt path."""
    spec = ToolSpec(
        name="portscan",
        risk=RiskLevel.active,
        target_arg="ip",
        kind=ScopeKind.ip,
        description="Aggressive TCP port scan.",
    )
    registry = {"portscan": spec}
    impls = {
        "portscan": lambda args: ToolResult(
            ok=True, data={"ip": args["ip"], "open_ports": [22, 443]}
        ),
    }
    return registry, impls


# ---------------------------------------------------------------------------
# Passive auto-approve
# ---------------------------------------------------------------------------


def test_in_scope_passive_tool_auto_approves_and_runs() -> None:
    llm = FakeLLM(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "subfinder",
                        "args": {"domain": "acme.com"},
                        "id": "call-1",
                    }
                ],
            ),
            AIMessage(content="enumeration complete"),
        ]
    )
    graph = build_graph(llm=llm, implementations=STUB_IMPLEMENTATIONS)

    final = graph.invoke(
        {
            "messages": [HumanMessage(content="enumerate acme.com")],
            "scope_items": [_scope_item(ScopeKind.domain, "acme.com")],
        },
        config=_config(),
    )

    findings = final.get("findings") or []
    assert len(findings) == 1
    assert findings[0]["tool"] == "subfinder"
    assert "www.acme.com" in findings[0]["data"]["subdomains"]

    tool_messages = [m for m in final["messages"] if isinstance(m, ToolMessage)]
    assert len(tool_messages) == 1
    assert tool_messages[0].tool_call_id == "call-1"

    assert not final.get("denials")
    assert not final.get("pending")


def test_subdomain_target_auto_approves_via_subdomain_rule() -> None:
    llm = FakeLLM(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "dns_lookup",
                        "args": {"domain": "mail.acme.com"},
                        "id": "call-1",
                    }
                ],
            ),
            AIMessage(content="done"),
        ]
    )
    graph = build_graph(llm=llm, implementations=STUB_IMPLEMENTATIONS)

    final = graph.invoke(
        {
            "messages": [HumanMessage(content="resolve mail.acme.com")],
            "scope_items": [_scope_item(ScopeKind.domain, "acme.com")],
        },
        config=_config(),
    )

    findings = final.get("findings") or []
    assert len(findings) == 1
    assert findings[0]["tool"] == "dns_lookup"


# ---------------------------------------------------------------------------
# Deny paths
# ---------------------------------------------------------------------------


def test_out_of_scope_tool_call_is_denied() -> None:
    llm = FakeLLM(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "subfinder",
                        "args": {"domain": "evil.com"},
                        "id": "call-1",
                    }
                ],
            ),
            AIMessage(content="ack denial"),
        ]
    )
    graph = build_graph(llm=llm, implementations=STUB_IMPLEMENTATIONS)

    final = graph.invoke(
        {
            "messages": [HumanMessage(content="enumerate")],
            "scope_items": [_scope_item(ScopeKind.domain, "acme.com")],
        },
        config=_config(),
    )

    assert not (final.get("findings") or [])
    denials = final.get("denials") or []
    assert len(denials) == 1
    assert denials[0]["tool"] == "subfinder"
    assert denials[0]["args"]["domain"] == "evil.com"
    tool_messages = [m for m in final["messages"] if isinstance(m, ToolMessage)]
    assert "denied" in tool_messages[0].content.lower()


def test_list_arg_fans_out_into_per_target_runs() -> None:
    """Llama sometimes batches targets into a list. Dispatch fans out into
    N gate evaluations + N findings, with ONE aggregated ToolMessage back."""
    llm = FakeLLM(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "subfinder",
                        "args": {
                            "domain": ["acme.com", "evil.com", "mail.acme.com"],
                        },
                        "id": "call-1",
                    }
                ],
            ),
            AIMessage(content="done"),
        ]
    )
    graph = build_graph(llm=llm, implementations=STUB_IMPLEMENTATIONS)

    final = graph.invoke(
        {
            "messages": [HumanMessage(content="enumerate everything")],
            "scope_items": [_scope_item(ScopeKind.domain, "acme.com")],
        },
        config=_config(),
    )

    # Two findings: acme.com and mail.acme.com both match the domain scope item.
    findings = final.get("findings") or []
    assert len(findings) == 2
    domains = {f["args"]["domain"] for f in findings}
    assert domains == {"acme.com", "mail.acme.com"}

    # One denial: evil.com is out of scope.
    denials = final.get("denials") or []
    assert len(denials) == 1
    assert denials[0]["args"]["domain"] == "evil.com"

    # Exactly ONE ToolMessage for the (one) batched tool_call, with per-target detail.
    tool_messages = [m for m in final["messages"] if isinstance(m, ToolMessage)]
    assert len(tool_messages) == 1
    assert tool_messages[0].tool_call_id == "call-1"
    payload = json.loads(tool_messages[0].content)
    assert payload["fanned_out"] is True
    assert payload["targets"] == 3
    assert len(payload["per_target"]) == 3


def test_comma_joined_string_fans_out_into_per_target_runs() -> None:
    """Llama also batches by joining targets into a single comma/space string,
    e.g. ``{"domain": "acme.com, evil.com,mail.acme.com"}``. Dispatch must split
    and fan out exactly like the JSON-list case — one ToolMessage, per-target
    findings/denials."""
    llm = FakeLLM(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "subfinder",
                        "args": {"domain": "acme.com, evil.com,mail.acme.com"},
                        "id": "call-1",
                    }
                ],
            ),
            AIMessage(content="done"),
        ]
    )
    graph = build_graph(llm=llm, implementations=STUB_IMPLEMENTATIONS)

    final = graph.invoke(
        {
            "messages": [HumanMessage(content="enumerate everything")],
            "scope_items": [_scope_item(ScopeKind.domain, "acme.com")],
        },
        config=_config(),
    )

    # acme.com + mail.acme.com match scope; evil.com is denied.
    findings = final.get("findings") or []
    assert len(findings) == 2
    domains = {f["args"]["domain"] for f in findings}
    assert domains == {"acme.com", "mail.acme.com"}

    denials = final.get("denials") or []
    assert len(denials) == 1
    assert denials[0]["args"]["domain"] == "evil.com"

    # One aggregated ToolMessage; the comma string was split into 3 targets.
    tool_messages = [m for m in final["messages"] if isinstance(m, ToolMessage)]
    assert len(tool_messages) == 1
    assert tool_messages[0].tool_call_id == "call-1"
    payload = json.loads(tool_messages[0].content)
    assert payload["fanned_out"] is True
    assert payload["targets"] == 3
    assert len(payload["per_target"]) == 3


def test_unknown_tool_is_denied_by_registry() -> None:
    llm = FakeLLM(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "nmap_aggressive",
                        "args": {"ip": "10.0.0.5"},
                        "id": "call-1",
                    }
                ],
            ),
            AIMessage(content="ack"),
        ]
    )
    graph = build_graph(llm=llm, implementations=STUB_IMPLEMENTATIONS)

    final = graph.invoke(
        {
            "messages": [HumanMessage(content="scan")],
            "scope_items": [_scope_item(ScopeKind.cidr, "10.0.0.0/24")],
        },
        config=_config(),
    )
    denials = final.get("denials") or []
    assert len(denials) == 1
    assert "unknown tool" in denials[0]["reason"].lower()


# ---------------------------------------------------------------------------
# Interrupt + resume
# ---------------------------------------------------------------------------


def test_active_tool_interrupts_then_resumes_approved() -> None:
    registry, impls = _portscan_overrides()
    llm = FakeLLM(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "portscan",
                        "args": {"ip": "10.0.0.5"},
                        "id": "call-1",
                    }
                ],
            ),
            AIMessage(content="scan complete"),
        ]
    )
    graph = build_graph(llm=llm, registry=registry, implementations=impls)
    config = _config()

    graph.invoke(
        {
            "messages": [HumanMessage(content="scan 10.0.0.5")],
            "scope_items": [_scope_item(ScopeKind.cidr, "10.0.0.0/24")],
        },
        config=config,
    )

    snapshot = graph.get_state(config)
    assert snapshot.next, "expected graph to be paused on interrupt()"

    final = graph.invoke(Command(resume={"approved": True}), config=config)

    findings = final.get("findings") or []
    assert len(findings) == 1
    assert findings[0]["data"]["open_ports"] == [22, 443]

    after = graph.get_state(config)
    assert not after.next, "graph should have run to END after resume"


def test_active_tool_interrupts_then_resumes_denied() -> None:
    registry, impls = _portscan_overrides()
    llm = FakeLLM(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "portscan",
                        "args": {"ip": "10.0.0.5"},
                        "id": "call-1",
                    }
                ],
            ),
            AIMessage(content="ack denial"),
        ]
    )
    graph = build_graph(llm=llm, registry=registry, implementations=impls)
    config = _config()

    graph.invoke(
        {
            "messages": [HumanMessage(content="scan 10.0.0.5")],
            "scope_items": [_scope_item(ScopeKind.cidr, "10.0.0.0/24")],
        },
        config=config,
    )

    final = graph.invoke(
        Command(resume={"approved": False, "reason": "out of agreed window"}),
        config=config,
    )

    assert not (final.get("findings") or [])
    denials = final.get("denials") or []
    assert len(denials) == 1
    assert "agreed window" in denials[0]["reason"]


# ---------------------------------------------------------------------------
# Host resolution before the gate (portscan: accept IP or hostname)
# ---------------------------------------------------------------------------


def test_resolve_host_tool_resolves_before_gate_then_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """portscan accepts a hostname; the dispatch node resolves it to an IP
    BEFORE the gate, authorizes that IP, interrupts for approval, then scans.
    The resolved IP becomes the target; the original host is kept as
    ``resolved_from`` for the approval/finding context."""
    monkeypatch.setattr(
        "app.orchestrator.graph._resolve_to_ip", lambda host: "10.0.0.5"
    )
    llm = FakeLLM(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "portscan",
                        "args": {"target": "scanme.example.com"},
                        "id": "call-1",
                    }
                ],
            ),
            AIMessage(content="scan complete"),
        ]
    )
    graph = build_graph(llm=llm, implementations=STUB_IMPLEMENTATIONS)
    config = _config()

    graph.invoke(
        {
            "messages": [HumanMessage(content="port scan scanme.example.com")],
            "scope_items": [_scope_item(ScopeKind.cidr, "10.0.0.0/24")],
        },
        config=config,
    )
    snapshot = graph.get_state(config)
    assert snapshot.next, "expected interrupt before an active scan runs"

    final = graph.invoke(Command(resume={"approved": True}), config=config)
    findings = final.get("findings") or []
    assert len(findings) == 1
    assert findings[0]["args"]["target"] == "10.0.0.5"
    assert findings[0]["args"]["resolved_from"] == "scanme.example.com"


def test_resolve_host_unresolvable_is_denied_without_interrupt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.orchestrator.graph._resolve_to_ip", lambda host: None)
    llm = FakeLLM(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "portscan",
                        "args": {"target": "nope.invalid"},
                        "id": "call-1",
                    }
                ],
            ),
            AIMessage(content="ack"),
        ]
    )
    graph = build_graph(llm=llm, implementations=STUB_IMPLEMENTATIONS)
    config = _config()

    final = graph.invoke(
        {
            "messages": [HumanMessage(content="scan nope.invalid")],
            "scope_items": [_scope_item(ScopeKind.cidr, "10.0.0.0/24")],
        },
        config=config,
    )
    assert not (final.get("findings") or [])
    denials = final.get("denials") or []
    assert len(denials) == 1
    assert "could not resolve" in denials[0]["reason"]
    assert not graph.get_state(config).next, "must not reach the approval step"


def test_resolve_host_resolved_ip_out_of_scope_is_denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hostname that resolves to an out-of-scope IP is denied by the gate
    on the resolved address — no interrupt."""
    monkeypatch.setattr("app.orchestrator.graph._resolve_to_ip", lambda host: "8.8.8.8")
    llm = FakeLLM(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "portscan",
                        "args": {"target": "evil.example.com"},
                        "id": "call-1",
                    }
                ],
            ),
            AIMessage(content="ack"),
        ]
    )
    graph = build_graph(llm=llm, implementations=STUB_IMPLEMENTATIONS)
    config = _config()

    final = graph.invoke(
        {
            "messages": [HumanMessage(content="scan evil.example.com")],
            "scope_items": [_scope_item(ScopeKind.cidr, "10.0.0.0/24")],
        },
        config=config,
    )
    assert not (final.get("findings") or [])
    denials = final.get("denials") or []
    assert len(denials) == 1
    assert "8.8.8.8" in denials[0]["reason"]
    assert not graph.get_state(config).next


def test_subnet_sweep_interrupts_then_injects_exclusions(
) -> None:
    """A CIDR sweep gets ONE approval; on resume the dispatch injects the
    engagement's ip/cidr exclusions so the tool skips carved-out hosts."""
    llm = FakeLLM(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "subnet_sweep",
                        "args": {"cidr": "10.0.0.0/24"},
                        "id": "call-1",
                    }
                ],
            ),
            AIMessage(content="sweep complete"),
        ]
    )
    graph = build_graph(llm=llm, implementations=STUB_IMPLEMENTATIONS)
    config = _config()

    graph.invoke(
        {
            "messages": [HumanMessage(content="sweep 10.0.0.0/24")],
            "scope_items": [
                _scope_item(ScopeKind.cidr, "10.0.0.0/24"),
                _scope_item(ScopeKind.ip, "10.0.0.5", is_exclusion=True),
            ],
        },
        config=config,
    )
    assert graph.get_state(config).next, "expected one interrupt for the CIDR"

    final = graph.invoke(Command(resume={"approved": True}), config=config)
    findings = final.get("findings") or []
    assert len(findings) == 1
    assert findings[0]["args"]["cidr"] == "10.0.0.0/24"
    # The excluded host was injected so the tool can skip it.
    assert findings[0]["args"]["exclude"] == ["10.0.0.5"]


def test_subnet_sweep_out_of_scope_cidr_denied_without_interrupt() -> None:
    llm = FakeLLM(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "subnet_sweep",
                        "args": {"cidr": "192.168.0.0/24"},
                        "id": "call-1",
                    }
                ],
            ),
            AIMessage(content="ack"),
        ]
    )
    graph = build_graph(llm=llm, implementations=STUB_IMPLEMENTATIONS)
    config = _config()

    final = graph.invoke(
        {
            "messages": [HumanMessage(content="sweep 192.168.0.0/24")],
            "scope_items": [_scope_item(ScopeKind.cidr, "10.0.0.0/24")],
        },
        config=config,
    )
    assert not (final.get("findings") or [])
    assert len(final.get("denials") or []) == 1
    assert not graph.get_state(config).next


def test_service_detect_active_interrupts_then_runs() -> None:
    """service_detect is active: an in-scope IP target interrupts for approval,
    then runs on resume (the resolve-before-gate path is shared with portscan
    and covered above; here the target is already an IP)."""
    llm = FakeLLM(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "service_detect",
                        "args": {"target": "10.0.0.5", "ports": "22"},
                        "id": "call-1",
                    }
                ],
            ),
            AIMessage(content="fingerprint complete"),
        ]
    )
    graph = build_graph(llm=llm, implementations=STUB_IMPLEMENTATIONS)
    config = _config()

    graph.invoke(
        {
            "messages": [HumanMessage(content="fingerprint 10.0.0.5:22")],
            "scope_items": [_scope_item(ScopeKind.cidr, "10.0.0.0/24")],
        },
        config=config,
    )
    assert graph.get_state(config).next, "expected interrupt before fingerprinting"

    final = graph.invoke(Command(resume={"approved": True}), config=config)
    findings = final.get("findings") or []
    assert len(findings) == 1
    assert findings[0]["tool"] == "service_detect"
    assert findings[0]["args"]["target"] == "10.0.0.5"


def test_session_authorization_auto_approves_active_tool() -> None:
    """With a standing session grant for the tool, an in-scope active call
    auto-runs (no interrupt) and is recorded in auto_approvals for auditing."""
    auth_id = uuid.uuid4()
    llm = FakeLLM(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "portscan",
                        "args": {"target": "10.0.0.5"},
                        "id": "call-1",
                    }
                ],
            ),
            AIMessage(content="scan complete"),
        ]
    )
    graph = build_graph(
        llm=llm,
        implementations=STUB_IMPLEMENTATIONS,
        authorizer=lambda _eid, tool: auth_id if tool == "portscan" else None,
    )
    config = _config()

    final = graph.invoke(
        {
            "messages": [HumanMessage(content="scan 10.0.0.5")],
            "scope_items": [_scope_item(ScopeKind.cidr, "10.0.0.0/24")],
        },
        config=config,
    )

    # Ran straight through — never paused for a human.
    assert not graph.get_state(config).next
    assert not (final.get("pending") or [])
    findings = final.get("findings") or []
    assert len(findings) == 1
    assert findings[0]["tool"] == "portscan"

    autos = final.get("auto_approvals") or []
    assert len(autos) == 1
    assert autos[0]["tool"] == "portscan"
    assert autos[0]["authorization_id"] == str(auth_id)
    assert autos[0]["risk"] == "active"


def test_active_tool_out_of_scope_denies_without_interrupt() -> None:
    """Out-of-scope must short-circuit before reaching the human-approval step."""
    registry, impls = _portscan_overrides()
    llm = FakeLLM(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "portscan",
                        "args": {"ip": "8.8.8.8"},
                        "id": "call-1",
                    }
                ],
            ),
            AIMessage(content="ack"),
        ]
    )
    graph = build_graph(llm=llm, registry=registry, implementations=impls)
    config = _config()

    final = graph.invoke(
        {
            "messages": [HumanMessage(content="scan 8.8.8.8")],
            "scope_items": [_scope_item(ScopeKind.cidr, "10.0.0.0/24")],
        },
        config=config,
    )

    assert not (final.get("findings") or [])
    denials = final.get("denials") or []
    assert len(denials) == 1
    after = graph.get_state(config)
    assert not after.next, "graph should not be waiting on a human approval"
