"""Stub tool implementations for tests that need deterministic, offline data.

The real impls in ``app.orchestrator.tools.*`` hit crt.sh, do DNS, and make
HTTP requests — fine for production but flaky in tests. Tests that exercise
graph / worker plumbing (not the tools themselves) inject these stubs via
``build_graph(implementations=STUB_IMPLEMENTATIONS)``.

Per-tool impl tests (``tests/test_tool_impls.py``) cover the real impls with
respx + monkeypatching.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.orchestrator.tools.runtime import ToolImpl, ToolResult


def _stub_subfinder(args: Mapping[str, Any]) -> ToolResult:
    domain = str(args.get("domain", ""))
    return ToolResult(
        ok=True,
        data={"subdomains": [f"www.{domain}", f"mail.{domain}", f"api.{domain}"]},
    )


def _stub_crt_sh(args: Mapping[str, Any]) -> ToolResult:
    domain = str(args.get("domain", ""))
    return ToolResult(
        ok=True,
        data={"certs": [{"common_name": f"*.{domain}", "issuer": "stub-ca"}]},
    )


def _stub_dns_lookup(args: Mapping[str, Any]) -> ToolResult:
    return ToolResult(ok=True, data={"a": ["192.0.2.1"], "aaaa": [], "cname": []})


def _stub_whois_lookup(args: Mapping[str, Any]) -> ToolResult:
    domain = str(args.get("domain", ""))
    return ToolResult(ok=True, data={"registrar": "stub-registrar", "domain": domain})


def _stub_httpx_probe(args: Mapping[str, Any]) -> ToolResult:
    url = str(args.get("url", ""))
    return ToolResult(ok=True, data={"url": url, "status": 200, "title": "stub"})


def _stub_reverse_dns(args: Mapping[str, Any]) -> ToolResult:
    ip = str(args.get("ip", ""))
    return ToolResult(ok=True, data={"ip": ip, "ptr": ["stub.example.com"]})


def _stub_portscan(args: Mapping[str, Any]) -> ToolResult:
    target = str(args.get("target", ""))
    data: dict[str, Any] = {"target": target, "open_ports": [22, 443], "open_count": 2}
    if args.get("resolved_from"):
        data["resolved_from"] = args["resolved_from"]
    return ToolResult(ok=True, data=data)


def _stub_subnet_sweep(args: Mapping[str, Any]) -> ToolResult:
    # Echo back the cidr + injected exclusions so tests can assert the dispatch
    # wired them through without doing any real scanning.
    return ToolResult(
        ok=True,
        data={
            "cidr": args.get("cidr"),
            "exclude": list(args.get("exclude") or []),
            "live_hosts": [],
        },
    )


def _stub_service_detect(args: Mapping[str, Any]) -> ToolResult:
    data: dict[str, Any] = {
        "target": args.get("target"),
        "services": [{"port": 22, "service": "ssh", "product": "OpenSSH_9.0"}],
    }
    if args.get("resolved_from"):
        data["resolved_from"] = args["resolved_from"]
    return ToolResult(ok=True, data=data)


STUB_IMPLEMENTATIONS: dict[str, ToolImpl] = {
    "subfinder": _stub_subfinder,
    "crt_sh": _stub_crt_sh,
    "dns_lookup": _stub_dns_lookup,
    "whois_lookup": _stub_whois_lookup,
    "httpx_probe": _stub_httpx_probe,
    "reverse_dns": _stub_reverse_dns,
    "portscan": _stub_portscan,
    "subnet_sweep": _stub_subnet_sweep,
    "service_detect": _stub_service_detect,
}
