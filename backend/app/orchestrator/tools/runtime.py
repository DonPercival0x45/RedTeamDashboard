"""Tool runtime: ``ToolResult`` envelope, the IMPLEMENTATIONS registry, and
``run_tool`` dispatch.

Each tool's real implementation lives in its own module under
``app.orchestrator.tools.<name>``. This file just wires the registry, so
swapping in a new source (e.g. shelling to subfinder, switching DNS lib)
is one import change here.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ToolResult:
    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    # Optional per-row findings the tool wants persisted/displayed. When set, the
    # graph dispatch fans these out into N findings (each with its own severity,
    # title, target) instead of synthesizing a single info-severity row from
    # ``data``. ``data`` is still the summary the LLM sees in the ToolMessage.
    findings: list[dict[str, Any]] | None = None


ToolImpl = Callable[[Mapping[str, Any]], "ToolResult"]


# Imports are at the bottom of the file because each tool module imports
# ``ToolResult`` from here; defining ToolResult first avoids a circular import.
from app.orchestrator.tools.crt_sh import crt_sh_impl  # noqa: E402
from app.orchestrator.tools.dns_lookup import dns_lookup_impl  # noqa: E402
from app.orchestrator.tools.freeipapi import freeipapi_impl  # noqa: E402
from app.orchestrator.tools.httpx_probe import httpx_probe_impl  # noqa: E402
from app.orchestrator.tools.ipinfo import ipinfo_impl  # noqa: E402
from app.orchestrator.tools.portscan import portscan_impl  # noqa: E402
from app.orchestrator.tools.reverse_dns import reverse_dns_impl  # noqa: E402
from app.orchestrator.tools.service_detect import service_detect_impl  # noqa: E402
from app.orchestrator.tools.subfinder import subfinder_impl  # noqa: E402
from app.orchestrator.tools.subnet_sweep import subnet_sweep_impl  # noqa: E402
from app.orchestrator.tools.whois_lookup import whois_lookup_impl  # noqa: E402

IMPLEMENTATIONS: dict[str, ToolImpl] = {
    "subfinder": subfinder_impl,
    "crt_sh": crt_sh_impl,
    "dns_lookup": dns_lookup_impl,
    "whois_lookup": whois_lookup_impl,
    "httpx_probe": httpx_probe_impl,
    "reverse_dns": reverse_dns_impl,
    "freeipapi": freeipapi_impl,
    "ipinfo": ipinfo_impl,
    "portscan": portscan_impl,
    "subnet_sweep": subnet_sweep_impl,
    "service_detect": service_detect_impl,
}


def run_tool(
    name: str,
    args: Mapping[str, Any],
    implementations: Mapping[str, ToolImpl] | None = None,
) -> ToolResult:
    impls = implementations if implementations is not None else IMPLEMENTATIONS
    impl = impls.get(name)
    if impl is None:
        return ToolResult(ok=False, error=f"no implementation for {name!r}")
    try:
        return impl(args)
    except Exception as exc:  # noqa: BLE001 — bubble up as a structured error
        return ToolResult(ok=False, error=f"{type(exc).__name__}: {exc}")
