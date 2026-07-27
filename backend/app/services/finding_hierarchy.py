"""Non-destructive entity hierarchy projected from authoritative Finding rows.

The projection intentionally stores nothing. Every leaf keeps its original Finding ID,
so validation, evidence, remediation, reports, and audit history retain their existing
semantics while the analyst gets a Nessus-style asset-first workspace.
"""

from __future__ import annotations

import hashlib
import ipaddress
from collections.abc import Iterable
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from app.models import Finding, FindingStatus, ScopeItem, ScopeKind, Severity
from app.schemas.finding_hierarchy import (
    FindingHierarchyCounts,
    FindingHierarchyFindingRef,
    FindingHierarchyItem,
    FindingHierarchyResponse,
    FindingHierarchyRollup,
)
from app.services.entities import extract_finding_entities
from app.services.entity_identity import normalize_entity_value
from app.services.finding_grouping import extract_items

PROJECTION_VERSION = "finding-hierarchy-v1"
_INVENTORY_TOOLS = {
    "subfinder",
    "crt_sh",
    "dns_lookup",
    "whois_lookup",
    "reverse_dns",
    "freeipapi",
    "ipinfo",
    "httpx_probe",
    "portscan",
    "subnet_sweep",
    "service_detect",
}
_SEVERITY_RANK = {
    Severity.info: 0,
    Severity.low: 1,
    Severity.medium: 2,
    Severity.high: 3,
    Severity.critical: 4,
}


def _stable_id(engagement_id: UUID, kind: str, canonical_key: str) -> str:
    raw = f"{PROJECTION_VERSION}|{engagement_id}|{kind}|{canonical_key}"
    return "fh_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _canonical_ip(value: object) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.startswith("[") and "]" in raw:
        raw = raw[1 : raw.index("]")]
    else:
        try:
            return ipaddress.ip_address(raw).compressed
        except ValueError:
            # IPv4 targets frequently carry :port. An unbracketed IPv6 address
            # is retried whole above and is never split here.
            if raw.count(":") == 1:
                raw = raw.rsplit(":", 1)[0]
    try:
        return ipaddress.ip_address(raw).compressed
    except ValueError:
        return None


def _canonical_domain(value: object) -> str | None:
    normalized = normalize_entity_value("domain", value)
    if normalized.startswith("*."):
        normalized = normalized[2:]
    if not normalized or "." not in normalized:
        return None
    try:
        ipaddress.ip_address(normalized)
    except ValueError:
        return normalized
    return None


def _domain_root(domain: str, roots: list[str]) -> str:
    matches = [root for root in roots if domain == root or domain.endswith(f".{root}")]
    return max(matches, key=len) if matches else domain


def _bucket(finding: Finding) -> str:
    if finding.exclusion is not None or finding.status in {
        FindingStatus.rejected,
        FindingStatus.false_positive,
    }:
        return "resolved_excluded"
    if finding.status in {FindingStatus.pending_validation, FindingStatus.needs_review}:
        return "needs_review"
    if finding.status is FindingStatus.validated and (
        finding.severity is not Severity.info
        or finding.source_tool in {"manual", "manual_promotion"}
    ):
        return "actionable"
    return "inventory"


def _finding_ref(finding: Finding) -> FindingHierarchyFindingRef:
    return FindingHierarchyFindingRef(
        id=finding.id,
        title=finding.title,
        tool=finding.source_tool,
        target=finding.target,
        severity=finding.severity,
        phase=finding.phase,
        status=finding.status,
        exclusion=finding.exclusion,
        observed_at=finding.observed_at,
        created_at=finding.created_at,
        bucket=_bucket(finding),
    )


def _new_node(
    engagement_id: UUID,
    *,
    kind: str,
    canonical_key: str,
    label: str,
    value: str | None = None,
    **fields: Any,
) -> dict[str, Any]:
    return {
        "id": _stable_id(engagement_id, kind, canonical_key),
        "kind": kind,
        "canonical_key": canonical_key,
        "label": label,
        "value": value,
        "finding_refs": {},
        "children_by_key": {},
        "create_finding_allowed": True,
        "suggested_title": label,
        "suggested_target": value,
        **fields,
    }


def _add_ref(node: dict[str, Any], ref: FindingHierarchyFindingRef) -> None:
    node["finding_refs"][str(ref.id)] = ref


def _all_refs(node: dict[str, Any]) -> dict[str, FindingHierarchyFindingRef]:
    refs = dict(node["finding_refs"])
    for child in node["children_by_key"].values():
        refs.update(_all_refs(child))
    return refs


def _rollup(node: dict[str, Any]) -> FindingHierarchyRollup:
    refs = list(_all_refs(node).values())
    counts = {
        "needs_review": 0,
        "actionable": 0,
        "inventory": 0,
        "resolved_excluded": 0,
    }
    active = []
    latest: datetime | None = None
    for ref in refs:
        counts[ref.bucket] += 1
        when = ref.observed_at or ref.created_at
        latest = when if latest is None or when > latest else latest
        if ref.bucket != "resolved_excluded":
            active.append(ref.severity)
    max_severity = max(active, key=_SEVERITY_RANK.get) if active else Severity.info
    return FindingHierarchyRollup(
        max_severity=max_severity,
        distinct_findings=len(refs),
        latest_at=latest,
        **counts,
    )


def _finalize(node: dict[str, Any]) -> FindingHierarchyItem:
    children = [_finalize(child) for child in node["children_by_key"].values()]
    children.sort(
        key=lambda child: (
            -_SEVERITY_RANK[child.rollup.max_severity],
            -(child.rollup.needs_review > 0),
            child.port if child.port is not None else 0,
            child.label.casefold(),
        )
    )
    payload = {key: value for key, value in node.items() if key != "children_by_key"}
    payload["finding_refs"] = sorted(
        node["finding_refs"].values(),
        key=lambda ref: (
            -_SEVERITY_RANK[ref.severity],
            -(ref.bucket == "needs_review"),
            ref.title.casefold(),
        ),
    )
    payload["children"] = children
    payload["rollup"] = _rollup(node)
    return FindingHierarchyItem.model_validate(payload)


def _details_and_items(finding: Finding) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    details = dict(finding.details or {})
    details.pop("thread_id", None)
    args = details.pop("args", {})
    raw_items = details.get("items")
    promotion = details.get("hierarchy_promotion")
    if isinstance(raw_items, list):
        items = [dict(item) for item in raw_items if isinstance(item, dict)]
    elif isinstance(promotion, dict):
        items = [dict(promotion)]
    else:
        items = extract_items(finding.source_tool, details)
    for item in items:
        if isinstance(args, dict):
            for key in ("host", "target", "ip", "port", "protocol", "transport"):
                if key not in item and args.get(key) is not None:
                    item[key] = args[key]
    return details, items or [details]


def _service_parts(
    finding: Finding, item: dict[str, Any], details: dict[str, Any]
) -> tuple[str, str, str, int, str | None, str | None] | None:
    port_raw = item.get("port")
    if (
        port_raw is None
        and finding.target
        and (
            (finding.target.startswith("[") and "]:" in finding.target)
            or finding.target.count(":") == 1
        )
    ):
        port_raw = finding.target.rsplit(":", 1)[-1]
    try:
        port = int(port_raw)
    except (TypeError, ValueError):
        return None
    if port < 1 or port > 65535:
        return None
    host = (
        item.get("host")
        or item.get("ip")
        or item.get("hostname")
        or item.get("target")
        or details.get("host")
        or details.get("ip")
        or details.get("target")
        or finding.target
    )
    ip = _canonical_ip(host)
    host_kind = "ip"
    canonical_host = ip
    if canonical_host is None:
        raw_host = str(host or "").strip()
        if raw_host.startswith(("http://", "https://")):
            try:
                raw_host = urlsplit(raw_host).hostname or ""
            except ValueError:
                return None
        elif raw_host.count(":") == 1:
            raw_host = raw_host.rsplit(":", 1)[0]
        canonical_host = _canonical_domain(raw_host)
        host_kind = "domain"
    if canonical_host is None:
        return None
    protocol_raw = item.get("protocol") or item.get("transport") or item.get("proto")
    if protocol_raw is None and finding.source_tool in {
        "portscan",
        "subnet_sweep",
        "service_detect",
    }:
        protocol_raw = "tcp"
    protocol = str(protocol_raw or "unknown").strip().lower()
    if protocol not in {"tcp", "udp", "unknown"}:
        protocol = "unknown"
    service = item.get("service") or item.get("name")
    product_parts = [item.get("product"), item.get("version")]
    product = " ".join(str(value).strip() for value in product_parts if value).strip() or None
    return (
        host_kind,
        canonical_host,
        protocol,
        port,
        str(service).strip() if service else None,
        product,
    )


def _web_surface(value: object) -> tuple[str, str] | None:
    raw = str(value or "").strip()
    if not raw.startswith(("http://", "https://")):
        return None
    try:
        parts = urlsplit(raw)
        host = _canonical_domain(parts.hostname)
        if host is None:
            return None
        port = parts.port or (443 if parts.scheme.lower() == "https" else 80)
    except ValueError:
        return None
    return host, f"{parts.scheme.lower()}://{host}:{port}"


def build_finding_hierarchy(
    *,
    engagement_id: UUID,
    findings: Iterable[Finding],
    scope_items: Iterable[ScopeItem],
) -> FindingHierarchyResponse:
    """Build a complete hierarchy without mutating any ORM row."""
    finding_rows = list(findings)
    domain_roots = sorted(
        {
            root
            for item in scope_items
            if item.kind is ScopeKind.domain and not item.is_exclusion
            if (root := _canonical_domain(item.value)) is not None
        },
        key=len,
        reverse=True,
    )
    assets: dict[tuple[str, str], dict[str, Any]] = {}
    mapped_ids: set[UUID] = set()

    def ip_asset(ip: str) -> dict[str, Any]:
        key = ("ip", ip)
        node = assets.get(key)
        if node is None:
            node = _new_node(
                engagement_id,
                kind="ip",
                canonical_key=f"ip:{ip}",
                label=f"IP: {ip}",
                value=ip,
                ip=ip,
                suggested_title=f"Finding on IP({ip})",
                suggested_target=ip,
            )
            assets[key] = node
        return node

    def domain_asset(root: str) -> dict[str, Any]:
        key = ("domain", root)
        node = assets.get(key)
        if node is None:
            node = _new_node(
                engagement_id,
                kind="domain",
                canonical_key=f"domain:{root}",
                label=f"Domain: {root}",
                value=root,
                hostname=root,
                suggested_title=f"Finding on {root}",
                suggested_target=root,
            )
            assets[key] = node
        return node

    def service_parent(host_kind: str, host: str) -> dict[str, Any]:
        if host_kind == "ip":
            asset = ip_asset(host)
            asset["label"] = f"Service Detection: IP({host})"
            return asset
        root = _domain_root(host, domain_roots)
        asset = domain_asset(root)
        if host == root:
            return asset
        host_key = f"host:{host}"
        return asset["children_by_key"].setdefault(
            host_key,
            _new_node(
                engagement_id,
                kind="subdomain",
                canonical_key=f"domain:{root}:{host_key}",
                label=host,
                value=host,
                hostname=host,
                suggested_title=f"Finding on {host}",
                suggested_target=host,
            ),
        )

    for finding in finding_rows:
        ref = _finding_ref(finding)
        details, items = _details_and_items(finding)
        service_mapped = False
        for item in items:
            service_parts = _service_parts(finding, item, details)
            if service_parts is None:
                continue
            host_kind, host, protocol, port, service, product = service_parts
            parent = service_parent(host_kind, host)
            service_key = f"service:{protocol}:{port}"
            child = parent["children_by_key"].get(service_key)
            if child is None:
                descriptors = [f"{port}/{protocol}"]
                if service:
                    descriptors.append(service.upper() if len(service) <= 8 else service)
                if product:
                    descriptors.append(product)
                target_host = f"[{host}]" if host_kind == "ip" and ":" in host else host
                child = _new_node(
                    engagement_id,
                    kind="service",
                    canonical_key=f"{host_kind}:{host}:{service_key}",
                    label=" · ".join(descriptors),
                    value=f"{target_host}:{port}/{protocol}",
                    ip=host if host_kind == "ip" else None,
                    hostname=host if host_kind == "domain" else None,
                    protocol=protocol,
                    port=port,
                    service=service,
                    suggested_title=(
                        f"{service or 'Service'} exposure on {target_host}:{port}/{protocol}"
                    ),
                    suggested_target=f"{target_host}:{port}",
                )
                parent["children_by_key"][service_key] = child
            elif service or product:
                descriptors = [f"{port}/{protocol}"]
                current_service = service or child.get("service")
                if current_service:
                    descriptors.append(
                        current_service.upper() if len(current_service) <= 8 else current_service
                    )
                if product:
                    descriptors.append(product)
                child["label"] = " · ".join(descriptors)
                child["service"] = current_service
            _add_ref(child, ref)
            mapped_ids.add(finding.id)
            service_mapped = True

        if not service_mapped:
            direct_ip = _canonical_ip(details.get("ip") or details.get("host") or finding.target)
            if direct_ip is not None:
                _add_ref(ip_asset(direct_ip), ref)
                mapped_ids.add(finding.id)

        entities = extract_finding_entities(finding)
        for entity_type, raw_value in entities:
            if entity_type == "ip":
                ip = _canonical_ip(raw_value)
                if ip is not None and not service_mapped:
                    _add_ref(ip_asset(ip), ref)
                    mapped_ids.add(finding.id)
                continue
            if entity_type == "url":
                surface = _web_surface(raw_value)
                if surface is None:
                    continue
                host, surface_key = surface
                root = _domain_root(host, domain_roots)
                asset = domain_asset(root)
                host_node = asset
                if host != root:
                    host_key = f"host:{host}"
                    host_node = asset["children_by_key"].setdefault(
                        host_key,
                        _new_node(
                            engagement_id,
                            kind="subdomain",
                            canonical_key=f"domain:{root}:{host_key}",
                            label=host,
                            value=host,
                            hostname=host,
                            suggested_title=f"Finding on {host}",
                            suggested_target=host,
                        ),
                    )
                web_key = f"web:{surface_key}"
                web_node = host_node["children_by_key"].setdefault(
                    web_key,
                    _new_node(
                        engagement_id,
                        kind="web_surface",
                        canonical_key=f"domain:{root}:{web_key}",
                        label=surface_key,
                        value=surface_key,
                        hostname=host,
                        url=str(raw_value),
                        suggested_title=f"Web finding on {host}",
                        suggested_target=str(raw_value),
                    ),
                )
                _add_ref(web_node, ref)
                mapped_ids.add(finding.id)
                continue
            if entity_type not in {"domain", "subdomain", "host"}:
                continue
            domain = _canonical_domain(raw_value)
            if domain is None:
                continue
            root = _domain_root(domain, domain_roots)
            asset = domain_asset(root)
            target_node = asset
            if domain != root:
                host_key = f"host:{domain}"
                target_node = asset["children_by_key"].setdefault(
                    host_key,
                    _new_node(
                        engagement_id,
                        kind="subdomain",
                        canonical_key=f"domain:{root}:{host_key}",
                        label=domain,
                        value=domain,
                        hostname=domain,
                        suggested_title=f"Finding on {domain}",
                        suggested_target=domain,
                    ),
                )
            _add_ref(target_node, ref)
            mapped_ids.add(finding.id)

    finalized_assets = [_finalize(node) for node in assets.values()]
    finalized_assets.sort(
        key=lambda asset: (
            -(asset.rollup.needs_review > 0),
            -_SEVERITY_RANK[asset.rollup.max_severity],
            -asset.rollup.actionable,
            -(asset.rollup.latest_at.timestamp() if asset.rollup.latest_at else 0),
            asset.label.casefold(),
        )
    )

    ungrouped: list[FindingHierarchyItem] = []
    for finding in finding_rows:
        if finding.id in mapped_ids:
            continue
        ref = _finding_ref(finding)
        node = _new_node(
            engagement_id,
            kind="finding",
            canonical_key=f"finding:{finding.id}",
            label=finding.title,
            value=finding.target,
            suggested_title=finding.title,
            suggested_target=finding.target,
        )
        _add_ref(node, ref)
        ungrouped.append(_finalize(node))

    all_top = [*finalized_assets, *ungrouped]
    counts = FindingHierarchyCounts(
        focus=sum(1 for node in all_top if node.rollup.needs_review or node.rollup.actionable),
        needs_review=sum(1 for node in all_top if node.rollup.needs_review),
        actionable=sum(1 for node in all_top if node.rollup.actionable),
        inventory=sum(1 for node in all_top if node.rollup.inventory),
        resolved_excluded=sum(1 for node in all_top if node.rollup.resolved_excluded),
        distinct_findings=len(finding_rows),
    )
    return FindingHierarchyResponse(
        assets=finalized_assets,
        ungrouped=ungrouped,
        counts=counts,
        generated_at=datetime.now(tz=UTC),
        projection_version=PROJECTION_VERSION,
    )


def is_inventory_source_tool(tool: str | None) -> bool:
    return tool in _INVENTORY_TOOLS


def hierarchy_item_finding_refs(
    item: FindingHierarchyItem,
) -> list[FindingHierarchyFindingRef]:
    """Return distinct direct + descendant sources for promotion provenance."""
    refs = {str(ref.id): ref for ref in item.finding_refs}
    for child in item.children:
        refs.update({str(ref.id): ref for ref in hierarchy_item_finding_refs(child)})
    return list(refs.values())


def hierarchy_duplicate_target_key(item: FindingHierarchyItem, target: str | None) -> str | None:
    """Canonical duplicate key for a candidate affected target."""
    if not target:
        return None
    if item.kind in {"ip", "service"}:
        ip = _canonical_ip(target)
        if item.kind == "ip":
            return f"ip:{ip}" if ip else None
        port = item.port
        raw_host = target.strip()
        if raw_host.startswith("[") and "]" in raw_host:
            raw_host = raw_host[1 : raw_host.index("]")]
        elif raw_host.count(":") == 1:
            raw_host, raw_port = raw_host.rsplit(":", 1)
            with suppress(ValueError):
                port = int(raw_port)
        host = ip or _canonical_domain(raw_host)
        host_kind = "ip" if ip else "domain"
        return f"{host_kind}:{host}:port:{port or 0}" if host else None
    if item.kind in {"domain", "subdomain"}:
        domain = _canonical_domain(target)
        return f"domain:{domain}" if domain else None
    if item.kind == "web_surface":
        normalized = normalize_entity_value("url", target)
        return f"url:{normalized}" if normalized else None
    return f"raw:{target.strip().casefold()}"


def find_hierarchy_item(
    response: FindingHierarchyResponse, item_id: str
) -> FindingHierarchyItem | None:
    stack = [*response.assets, *response.ungrouped]
    while stack:
        item = stack.pop()
        if item.id == item_id:
            return item
        stack.extend(item.children)
    return None
