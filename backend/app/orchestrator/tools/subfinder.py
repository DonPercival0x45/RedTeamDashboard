"""Subdomain enumeration.

Phase 0: delegates to crt.sh (the single biggest source for passive subdomain
discovery). A future revision can shell out to the ProjectDiscovery subfinder
binary or layer additional passive sources (VT, AlienVault OTX, etc.) — the
tool contract stays the same.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.orchestrator.tools.crt_sh import crt_sh_impl
from app.orchestrator.tools.runtime import ToolResult


def subfinder_impl(args: Mapping[str, Any]) -> ToolResult:
    result = crt_sh_impl(args)
    if not result.ok:
        return result
    subdomains = list(result.data.get("subdomains") or [])
    return ToolResult(
        ok=True,
        data={
            "domain": result.data.get("domain"),
            "source": "crt.sh",
            "count": len(subdomains),
            "subdomains": subdomains,
        },
    )
