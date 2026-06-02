"""DNS resolution: A, AAAA, CNAME, MX, NS, TXT for a domain.

Bundles the common record types into one tool call so the agent doesn't need
to know about each separately — keeps the tool surface narrow.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import dns.exception
import dns.resolver

from app.orchestrator.tools.runtime import ToolResult

TIMEOUT_S = 5.0
RECORD_TYPES = ("A", "AAAA", "CNAME", "MX", "NS", "TXT")


def _query(resolver: dns.resolver.Resolver, name: str, rtype: str) -> list[str]:
    try:
        answers = resolver.resolve(name, rtype)
    except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN, dns.exception.Timeout):
        return []
    except dns.exception.DNSException:
        return []
    return sorted(str(rdata).rstrip(".") for rdata in answers)


def dns_lookup_impl(args: Mapping[str, Any]) -> ToolResult:
    domain = str(args.get("domain") or "").strip().rstrip(".")
    if not domain:
        return ToolResult(ok=False, error="missing or empty 'domain' arg")

    resolver = dns.resolver.Resolver()
    resolver.timeout = TIMEOUT_S
    resolver.lifetime = TIMEOUT_S

    records = {rtype.lower(): _query(resolver, domain, rtype) for rtype in RECORD_TYPES}

    return ToolResult(
        ok=True,
        data={"domain": domain, **records},
    )
