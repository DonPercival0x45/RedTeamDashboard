"""Active TCP connect port scanner (pure-Python asyncio).

Risk: **active** — it opens real TCP connections to the target, so every call
must clear the human-approval gate (see ``ToolSpec.risk``). We use a connect
scan (``asyncio.open_connection``) rather than a raw SYN scan: SYN needs root +
libpcap, while connect is fully portable and needs no extra binaries.

The ``target`` is expected to already be an IP literal — the dispatch node
resolves hostnames to an IP *before* the scope gate (see
``graph._resolve_to_ip``) so we authorize and scan the same address. ``ports``
may be omitted (scans the default ~1000), a list of ints, or a string of
comma/space-separated ports and ``"A-B"`` ranges.
"""
from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import re
from collections.abc import Mapping
from typing import Any

from app.orchestrator.tools.runtime import ToolResult

CONNECT_TIMEOUT_S = 1.0
MAX_CONCURRENCY = 200
MAX_PORTS = 5000

# Well-known service ports above 1000 worth scanning by default. Combined with
# the whole 1-1000 well-known range this gives ~1050 ports — a transparent
# stand-in for an "nmap top 1000" frequency scan. Pass an explicit `ports` arg
# to override.
_COMMON_HIGH_PORTS: frozenset[int] = frozenset({
    1080, 1194, 1433, 1521, 1723, 2049, 2082, 2083, 2086, 2087, 2095, 2096,
    2222, 2375, 2376, 3000, 3128, 3306, 3389, 4444, 4567, 5000, 5060, 5432,
    5601, 5672, 5900, 5901, 5985, 5986, 6379, 6443, 7001, 7077, 8000, 8008,
    8080, 8081, 8086, 8088, 8443, 8500, 8888, 9000, 9092, 9200, 9300, 9418,
    9999, 10000, 11211, 15672, 27017, 27018, 50070,
})

DEFAULT_PORTS: tuple[int, ...] = tuple(
    sorted(set(range(1, 1001)) | _COMMON_HIGH_PORTS)
)

# Minimal port -> service hints, surfaced in findings for readability.
_SERVICE_NAMES: dict[int, str] = {
    21: "ftp", 22: "ssh", 23: "telnet", 25: "smtp", 53: "dns", 80: "http",
    110: "pop3", 111: "rpcbind", 135: "msrpc", 139: "netbios-ssn",
    143: "imap", 389: "ldap", 443: "https", 445: "smb", 465: "smtps",
    587: "submission", 636: "ldaps", 993: "imaps", 995: "pop3s",
    1433: "mssql", 1521: "oracle", 2049: "nfs", 3306: "mysql", 3389: "rdp",
    5432: "postgres", 5900: "vnc", 6379: "redis", 8080: "http-alt",
    8443: "https-alt", 9200: "elasticsearch", 11211: "memcached",
    27017: "mongodb",
}

# Operator-priority bands for an open port. These are not vuln severities — they
# describe where to look first. High = remote-access / unauth-by-default DBs that
# pay off fast. Medium = auth / lateral / file-share. Low = SSH and web ports.
# Anything not listed defaults to info. ``service_detect`` may bump these on
# signal (e.g. redis NOAUTH -> critical).
_HIGH_SEV_PORTS: frozenset[int] = frozenset({
    23, 1433, 1521, 3306, 3389, 5432, 5900, 5901, 6379, 9200,
    11211, 27017, 27018,
})
_MEDIUM_SEV_PORTS: frozenset[int] = frozenset({
    21, 25, 110, 135, 139, 143, 389, 445, 587, 636, 993, 995, 2049,
    5985, 5986,
})
_LOW_SEV_PORTS: frozenset[int] = frozenset({
    22, 53, 80, 443, 465, 8080, 8081, 8443,
})


def port_severity(port: int) -> str:
    """Return an operator-priority severity string for an open port.

    Pure function so the same heuristic is used by portscan, subnet_sweep, and
    service_detect (as a baseline before service-signal bumps).
    """
    if port in _HIGH_SEV_PORTS:
        return "high"
    if port in _MEDIUM_SEV_PORTS:
        return "medium"
    if port in _LOW_SEV_PORTS:
        return "low"
    return "info"


def port_finding(host: str, port: int) -> dict[str, Any]:
    """Build the standard per-(host, open_port) finding shape used by portscan
    and subnet_sweep — same severity heuristic, same title format, same data
    keys — so they hydrate and render identically in the UI."""
    service = _SERVICE_NAMES.get(port)
    label = service or "unknown"
    return {
        "target": f"{host}:{port}",
        "severity": port_severity(port),
        "title": f"{label} open on {host}:{port}",
        "data": {
            "host": host,
            "port": port,
            "service": service,
        },
    }


def _parse_ports(raw: Any) -> list[int]:
    """Coerce the ``ports`` arg into a sorted list of valid TCP ports.

    Accepts None/"" (-> default set), a list/tuple of ints, or a string of
    comma/whitespace-separated tokens, each either ``"N"`` or ``"A-B"``.
    """
    if raw is None or raw == "":
        return list(DEFAULT_PORTS)

    if isinstance(raw, (list, tuple)):
        tokens = [str(t).strip() for t in raw]
    else:
        tokens = [t for t in re.split(r"[,\s]+", str(raw)) if t]

    ports: set[int] = set()
    for tok in tokens:
        if not tok:
            continue
        if "-" in tok:
            lo_s, _, hi_s = tok.partition("-")
            lo, hi = int(lo_s), int(hi_s)
            ports.update(range(min(lo, hi), max(lo, hi) + 1))
        else:
            ports.add(int(tok))
    return sorted(p for p in ports if 1 <= p <= 65535)


async def _scan_port(ip: str, port: int, sem: asyncio.Semaphore) -> int | None:
    async with sem:
        try:
            fut = asyncio.open_connection(ip, port)
            _reader, writer = await asyncio.wait_for(fut, timeout=CONNECT_TIMEOUT_S)
        except (OSError, TimeoutError):
            return None
        writer.close()
        with contextlib.suppress(OSError):
            await writer.wait_closed()
        return port


async def _scan(ip: str, ports: list[int]) -> list[int]:
    sem = asyncio.Semaphore(MAX_CONCURRENCY)
    results = await asyncio.gather(*(_scan_port(ip, p, sem) for p in ports))
    return sorted(p for p in results if p is not None)


def portscan_impl(args: Mapping[str, Any]) -> ToolResult:
    target = str(args.get("target") or "").strip()
    if not target:
        return ToolResult(ok=False, error="missing or empty 'target' arg")
    try:
        ip = str(ipaddress.ip_address(target))
    except ValueError:
        return ToolResult(
            ok=False,
            error=f"target {target!r} is not an IP address (resolve it first)",
        )

    try:
        ports = _parse_ports(args.get("ports"))
    except (ValueError, TypeError) as exc:
        return ToolResult(ok=False, error=f"bad 'ports' arg: {exc}")
    if not ports:
        return ToolResult(ok=False, error="no valid ports to scan")
    if len(ports) > MAX_PORTS:
        return ToolResult(
            ok=False,
            error=f"too many ports ({len(ports)} > {MAX_PORTS}); narrow the range",
        )

    open_ports = asyncio.run(_scan(ip, ports))

    data: dict[str, Any] = {
        "target": ip,
        "ports_scanned": len(ports),
        "open_ports": open_ports,
        "open_count": len(open_ports),
        "services": {str(p): _SERVICE_NAMES[p] for p in open_ports if p in _SERVICE_NAMES},
    }
    if args.get("resolved_from"):
        data["resolved_from"] = args["resolved_from"]

    findings = [port_finding(ip, p) for p in open_ports]
    if args.get("resolved_from"):
        for finding in findings:
            finding["data"]["resolved_from"] = args["resolved_from"]

    return ToolResult(ok=True, data=data, findings=findings)
