"""PTR (reverse DNS) lookup for an IP."""
from __future__ import annotations

import ipaddress
from collections.abc import Mapping
from typing import Any

import dns.exception
import dns.resolver
import dns.reversename

from app.orchestrator.tools.runtime import ToolResult

TIMEOUT_S = 5.0


def reverse_dns_impl(args: Mapping[str, Any]) -> ToolResult:
    raw = str(args.get("ip") or "").strip()
    if not raw:
        return ToolResult(ok=False, error="missing or empty 'ip' arg")

    try:
        ipaddress.ip_address(raw)
    except ValueError:
        return ToolResult(ok=False, error=f"invalid ip: {raw!r}")

    resolver = dns.resolver.Resolver()
    resolver.timeout = TIMEOUT_S
    resolver.lifetime = TIMEOUT_S

    try:
        rev_name = dns.reversename.from_address(raw)
        answers = resolver.resolve(rev_name, "PTR")
        ptrs = sorted(str(rdata).rstrip(".") for rdata in answers)
    except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):
        ptrs = []
    except dns.exception.DNSException as exc:
        return ToolResult(ok=False, error=f"reverse dns failed: {exc}")

    return ToolResult(ok=True, data={"ip": raw, "ptr": ptrs})
