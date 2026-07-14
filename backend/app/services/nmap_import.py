"""Nmap XML import for the Phase 10 import-first workflow.

Analysts run Nmap on authorized infrastructure and upload the XML result. Open
ports become grouped, pending-validation findings; the dashboard never shells
out to Nmap from this importer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from defusedxml import ElementTree

from app.models import FindingPhase, ScopeItem, Severity
from app.services.scope_matcher import evaluate_scope_candidates, infer_scope_kind


@dataclass
class ParsedItem:
    title: str
    severity: Severity
    phase: FindingPhase
    summary: str | None
    target: str
    source_tool: str
    details: dict[str, Any] = field(default_factory=dict)
    observed_at: datetime | None = None
    group_key: str | None = None


@dataclass
class ParseResult:
    items: list[ParsedItem]
    total_ports: int
    skipped_closed: int
    skipped_out_of_scope: int
    observed_at: datetime | None


def _scan_time(root: Any) -> datetime | None:
    raw = root.attrib.get("start")
    if not raw:
        return None
    try:
        return datetime.fromtimestamp(int(raw), tz=UTC)
    except (ValueError, OSError, OverflowError):
        return None


def _host_identity(host: Any) -> tuple[str, set[str], dict[str, str]]:
    addresses: dict[str, str] = {}
    for node in host.findall("address"):
        addr = (node.attrib.get("addr") or "").strip()
        if addr:
            addresses[node.attrib.get("addrtype", "unknown")] = addr
    names = {
        (node.attrib.get("name") or "").strip().lower()
        for node in host.findall("./hostnames/hostname")
        if (node.attrib.get("name") or "").strip()
    }
    known = set(addresses.values()) | names
    target = next(iter(sorted(names)), "") or addresses.get("ipv4") or addresses.get("ipv6")
    return target or next(iter(known), "unknown-host"), known, addresses


def _in_scope(known: set[str], scope_items: list[ScopeItem]) -> bool:
    return evaluate_scope_candidates(
        [(value, infer_scope_kind(value)) for value in known],
        scope_items,
        empty_scope_allowed=True,
    ).allowed


def parse_nmap_xml(
    xml_bytes: bytes,
    *,
    scope_items: list[ScopeItem] | None = None,
) -> ParseResult:
    """Parse an ``nmap -oX`` document into finding-shaped open services."""
    try:
        root = ElementTree.fromstring(xml_bytes)
    except ElementTree.ParseError as exc:
        raise ValueError(f"invalid Nmap XML: {exc}") from exc
    if root.tag != "nmaprun":
        raise ValueError(f"expected <nmaprun> root element, got {root.tag!r}")

    observed_at = _scan_time(root)
    items: list[ParsedItem] = []
    total_ports = 0
    skipped_closed = 0
    skipped_out_of_scope = 0

    for host in root.findall("host"):
        status = host.find("status")
        if status is not None and status.attrib.get("state") != "up":
            continue
        target, known, addresses = _host_identity(host)
        host_allowed = _in_scope(known, scope_items or [])
        host_scripts = [
            {"id": script.attrib.get("id", ""), "output": script.attrib.get("output", "")}
            for script in host.findall("./hostscript/script")
        ]

        for port in host.findall("./ports/port"):
            total_ports += 1
            state = port.find("state")
            state_name = state.attrib.get("state", "unknown") if state is not None else "unknown"
            if state_name != "open":
                skipped_closed += 1
                continue
            if not host_allowed:
                skipped_out_of_scope += 1
                continue

            protocol = port.attrib.get("protocol", "tcp")
            port_id = port.attrib.get("portid", "0")
            service = port.find("service")
            service_name = (
                service.attrib.get("name", "unknown")
                if service is not None
                else "unknown"
            )
            product = service.attrib.get("product") if service is not None else None
            version = service.attrib.get("version") if service is not None else None
            extra = service.attrib.get("extrainfo") if service is not None else None
            banner = " ".join(part for part in (product, version, extra) if part)
            scripts = [
                {"id": script.attrib.get("id", ""), "output": script.attrib.get("output", "")}
                for script in port.findall("script")
            ]
            details = {
                "host": target,
                "addresses": addresses,
                "port": port_id,
                "protocol": protocol,
                "state": state_name,
                "service": service_name,
                "product": product,
                "version": version,
                "extra_info": extra,
                "scripts": scripts,
                "host_scripts": host_scripts,
            }
            details = {key: value for key, value in details.items() if value not in (None, [], {})}
            items.append(
                ParsedItem(
                    title=f"Open {service_name} service on {port_id}/{protocol}",
                    severity=Severity.info,
                    phase=FindingPhase.vuln_scan,
                    summary=(
                        f"Nmap observed {service_name} on {target}:{port_id}/{protocol}"
                        + (f" ({banner})." if banner else ".")
                    ),
                    target=f"{target}:{port_id}",
                    source_tool="nmap_import",
                    details=details,
                    observed_at=observed_at,
                    group_key=f"nmap:{protocol}:{port_id}:{service_name}",
                )
            )

    return ParseResult(
        items=items,
        total_ports=total_ports,
        skipped_closed=skipped_closed,
        skipped_out_of_scope=skipped_out_of_scope,
        observed_at=observed_at,
    )
