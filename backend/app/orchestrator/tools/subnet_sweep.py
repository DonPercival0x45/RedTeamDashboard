"""Active subnet sweep: TCP connect scan across every host in a CIDR.

Risk: **active**. One approval authorizes the whole range (the scope gate
matches the CIDR against in-scope CIDRs via subnet-of), then this impl fans out
internally to each host — so the operator approves once, not per host.

Scope exclusions are honored: the dispatch node injects the engagement's
ip/cidr exclusions as ``exclude`` and any host inside one is skipped, so a
carved-out box is never touched even when it sits inside an approved range.

Reuses the connect-scan primitive from ``portscan`` (no raw sockets). Bounded
two ways: at most ``MAX_SWEEP_ADDRESSES`` in a range (checked before the host
list is materialized, so an IPv6 /64 can't OOM us), and concurrency capped on
both hosts in flight and total open sockets.
"""
from __future__ import annotations

import asyncio
import ipaddress
import re
from collections.abc import Mapping
from typing import Any

from app.orchestrator.tools.portscan import (
    _SERVICE_NAMES,
    _parse_ports,
    _scan_port,
    port_finding,
)
from app.orchestrator.tools.runtime import ToolResult

Network = ipaddress.IPv4Network | ipaddress.IPv6Network

# A /24 is 256 addresses (254 usable hosts). Checked against num_addresses
# before materializing the host list so a huge range never gets expanded.
MAX_SWEEP_ADDRESSES = 256
SWEEP_HOST_CONCURRENCY = 16   # hosts scanned in parallel
SWEEP_SOCKET_CONCURRENCY = 400  # total open connections across the sweep


def _parse_exclusions(raw: Any) -> list[Network]:
    if not raw:
        return []
    items = raw if isinstance(raw, (list, tuple)) else re.split(r"[,\s]+", str(raw))
    nets: list[Network] = []
    for item in items:
        text = str(item).strip()
        if not text:
            continue
        try:
            nets.append(ipaddress.ip_network(text, strict=False))
        except ValueError:
            continue
    return nets


def _is_excluded(ip: Any, nets: list[Network]) -> bool:
    return any(ip.version == net.version and ip in net for net in nets)


async def _sweep(targets: list[str], ports: list[int]) -> list[tuple[str, list[int]]]:
    sock_sem = asyncio.Semaphore(SWEEP_SOCKET_CONCURRENCY)
    host_sem = asyncio.Semaphore(SWEEP_HOST_CONCURRENCY)

    async def scan_host(ip: str) -> tuple[str, list[int]]:
        async with host_sem:
            results = await asyncio.gather(
                *(_scan_port(ip, p, sock_sem) for p in ports)
            )
            return ip, sorted(p for p in results if p is not None)

    return list(await asyncio.gather(*(scan_host(ip) for ip in targets)))


def subnet_sweep_impl(args: Mapping[str, Any]) -> ToolResult:
    raw_cidr = str(args.get("cidr") or "").strip()
    if not raw_cidr:
        return ToolResult(ok=False, error="missing or empty 'cidr' arg")
    try:
        net = ipaddress.ip_network(raw_cidr, strict=False)
    except ValueError:
        return ToolResult(ok=False, error=f"{raw_cidr!r} is not a valid CIDR")

    if net.num_addresses > MAX_SWEEP_ADDRESSES:
        return ToolResult(
            ok=False,
            error=(
                f"range too large ({net.num_addresses} addresses > "
                f"{MAX_SWEEP_ADDRESSES}); sweep a /24 or smaller"
            ),
        )

    try:
        ports = _parse_ports(args.get("ports"))
    except (ValueError, TypeError) as exc:
        return ToolResult(ok=False, error=f"bad 'ports' arg: {exc}")
    if not ports:
        return ToolResult(ok=False, error="no valid ports to scan")

    all_hosts = list(net.hosts()) or [net.network_address]
    excludes = _parse_exclusions(args.get("exclude"))
    targets = [h for h in all_hosts if not _is_excluded(h, excludes)]

    scanned = asyncio.run(_sweep([str(h) for h in targets], ports))
    live_hosts = [
        {
            "host": ip,
            "open_ports": open_ports,
            "services": {
                str(p): _SERVICE_NAMES[p] for p in open_ports if p in _SERVICE_NAMES
            },
        }
        for ip, open_ports in scanned
        if open_ports
    ]

    # One finding per (host, open_port) so operators triage at the port level
    # (RDP open on host A vs an HTTP banner on host B are different priorities).
    findings: list[dict[str, Any]] = []
    for ip, open_ports in sorted(scanned, key=lambda r: ipaddress.ip_address(r[0])):
        findings.extend(port_finding(ip, p) for p in open_ports)

    return ToolResult(
        ok=True,
        data={
            "cidr": str(net),
            "hosts_total": len(all_hosts),
            "hosts_scanned": len(targets),
            "hosts_excluded": len(all_hosts) - len(targets),
            "ports_per_host": len(ports),
            "live_host_count": len(live_hosts),
            "live_hosts": sorted(live_hosts, key=lambda h: ipaddress.ip_address(h["host"])),
        },
        findings=findings,
    )
