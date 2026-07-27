"""Server-owned catalog policy for analyst-authored playbooks.

The UI may choose from this registry, but it never defines transports, risk,
credentials, target arguments, or coverage. Those properties remain owned by
server code so a custom recipe cannot turn free-form JSON into a new executor.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

MAX_PLAYBOOK_CALLS = 500

PLAYBOOK_CATEGORIES: tuple[str, ...] = (
    "discovery",
    "enumeration",
    "posture",
    "exposure",
    "validation",
    "scope_review",
    "other",
)
ENTITY_TYPES: tuple[str, ...] = (
    "domain",
    "subdomain",
    "host",
    "ip",
    "cidr",
    "url",
    "email",
    "scope",
)

_ENTITY_KIND = {
    "domain": "domain",
    "subdomain": "domain",
    "host": "domain",
    "ip": "ip",
    "cidr": "cidr",
    "url": "url",
    "email": "email",
    "scope": "scope",
}


@dataclass(frozen=True)
class PlaybookToolSpec:
    slug: str
    name: str
    description: str
    target_kinds: tuple[str, ...]
    transport: str
    risk: str = "passive"
    credential: str | None = None


_TOOL_SPECS: tuple[PlaybookToolSpec, ...] = (
    PlaybookToolSpec(
        "dns-inventory", "DNS inventory", "Collect common DNS records.", ("domain",), "internal"
    ),
    PlaybookToolSpec(
        "whois",
        "WHOIS lookup",
        "Collect registration and registrar metadata.",
        ("domain",),
        "internal",
    ),
    PlaybookToolSpec(
        "subfinder",
        "Passive subdomain discovery",
        "Discover subdomains from passive sources.",
        ("domain",),
        "internal",
    ),
    PlaybookToolSpec(
        "crtsh",
        "Certificate transparency",
        "Search certificate transparency records.",
        ("domain",),
        "internal",
    ),
    PlaybookToolSpec(
        "breach-lookup",
        "Imported exposure lookup",
        "Match a domain or exact mailbox against imported evidence.",
        ("domain", "email"),
        "internal",
    ),
    PlaybookToolSpec(
        "scope-hygiene",
        "Scope hygiene review",
        "Review exact scope records for hygiene issues.",
        ("scope",),
        "internal",
    ),
    PlaybookToolSpec(
        "dns-ownership-boundary",
        "DNS ownership boundary",
        "Identify likely provider-owned DNS branches.",
        ("domain",),
        "internal",
    ),
    PlaybookToolSpec(
        "dangling-dns-triage",
        "Dangling DNS triage",
        "Check deterministic dangling-DNS indicators.",
        ("domain",),
        "internal",
    ),
    PlaybookToolSpec(
        "web-security-baseline",
        "Web security baseline",
        "Check deterministic web posture signals.",
        ("domain",),
        "internal",
    ),
    PlaybookToolSpec(
        "mail-auth-posture",
        "Mail authentication posture",
        "Check SPF, DMARC, MTA-STS, and TLS reporting.",
        ("domain",),
        "internal",
    ),
    PlaybookToolSpec(
        "cloud-edge-boundary",
        "Cloud and edge boundary",
        "Identify likely cloud and CDN boundaries.",
        ("domain",),
        "internal",
    ),
    PlaybookToolSpec(
        "crt_sh",
        "Connected certificate search",
        "Run certificate search through the connected tool service.",
        ("domain",),
        "mcp",
    ),
    PlaybookToolSpec(
        "dns_lookup",
        "Connected DNS lookup",
        "Run DNS lookup through the connected tool service.",
        ("domain",),
        "mcp",
    ),
    PlaybookToolSpec(
        "mcp_subfinder",
        "Connected subdomain discovery",
        "Run subdomain discovery through the connected tool service.",
        ("domain",),
        "mcp",
    ),
    PlaybookToolSpec(
        "mcp_crt_sh",
        "Connected certificate search",
        "Run certificate search through the connected tool service.",
        ("domain",),
        "mcp",
    ),
    PlaybookToolSpec(
        "mcp_dns_lookup",
        "Connected DNS lookup",
        "Run DNS lookup through the connected tool service.",
        ("domain",),
        "mcp",
    ),
    PlaybookToolSpec(
        "httpx_probe", "HTTP probe", "Probe an explicitly scoped URL.", ("url",), "mcp"
    ),
    PlaybookToolSpec(
        "mcp_httpx_probe",
        "Connected HTTP probe",
        "Probe an explicitly scoped URL through the connected tool service.",
        ("url",),
        "mcp",
    ),
    PlaybookToolSpec(
        "reverse_dns",
        "Reverse DNS",
        "Resolve PTR records for an explicitly scoped IP.",
        ("ip",),
        "mcp",
    ),
    PlaybookToolSpec(
        "freeipapi",
        "IP geolocation",
        "Collect IP geolocation and network metadata.",
        ("ip",),
        "mcp",
        credential="freeipapi",
    ),
    PlaybookToolSpec(
        "ipinfo",
        "IP ownership enrichment",
        "Collect ASN and network-owner metadata.",
        ("ip",),
        "mcp",
        credential="ipinfo",
    ),
    PlaybookToolSpec(
        "port_scan",
        "Port scan",
        "Scan a bounded target using the connected tool service.",
        ("domain", "ip"),
        "mcp",
        risk="active",
    ),
    PlaybookToolSpec(
        "service_detect",
        "Service detection",
        "Fingerprint services on an explicitly scoped target.",
        ("domain", "ip"),
        "mcp",
        risk="active",
    ),
    PlaybookToolSpec(
        "subnet_sweep",
        "Subnet sweep",
        "Discover live hosts in an explicitly scoped CIDR.",
        ("cidr",),
        "mcp",
        risk="active",
    ),
    PlaybookToolSpec(
        "mcp_reverse_dns",
        "Connected reverse DNS",
        "Resolve PTR records through the connected tool service.",
        ("ip",),
        "mcp",
    ),
    PlaybookToolSpec(
        "mcp_port_scan",
        "Connected port scan",
        "Scan a bounded target through the connected tool service.",
        ("domain", "ip"),
        "mcp",
        risk="active",
    ),
    PlaybookToolSpec(
        "mcp_service_detect",
        "Connected service detection",
        "Fingerprint services through the connected tool service.",
        ("domain", "ip"),
        "mcp",
        risk="active",
    ),
    PlaybookToolSpec(
        "mcp_subnet_sweep",
        "Connected subnet sweep",
        "Discover live hosts in an explicitly scoped CIDR.",
        ("cidr",),
        "mcp",
        risk="active",
    ),
)
TOOL_SPECS = {spec.slug: spec for spec in _TOOL_SPECS}


def normalize_entity_types(values: Iterable[str]) -> list[str]:
    normalized: list[str] = []
    for raw in values:
        value = str(raw).strip().lower()
        if value not in _ENTITY_KIND:
            raise ValueError(f"unsupported entity type: {raw}")
        if value not in normalized:
            normalized.append(value)
    if not normalized:
        raise ValueError("at least one applicable entity type is required")
    kinds = {_ENTITY_KIND[value] for value in normalized}
    if "scope" in kinds and len(kinds) > 1:
        raise ValueError("scope cannot be combined with other entity types")
    if len(kinds) > 1:
        raise ValueError("applicable entity types must share one execution target kind")
    return normalized


def execution_target_kind(entity_types: Iterable[str]) -> str:
    normalized = normalize_entity_types(entity_types)
    return _ENTITY_KIND[normalized[0]]


def validate_category(category: str) -> str:
    normalized = str(category).strip().lower()
    if normalized not in PLAYBOOK_CATEGORIES:
        raise ValueError(f"unsupported playbook category: {category}")
    return normalized


def tool_spec(tool_slug: str) -> PlaybookToolSpec:
    try:
        return TOOL_SPECS[str(tool_slug)]
    except KeyError as exc:
        raise ValueError(f"unsupported playbook tools: {tool_slug}") from exc


def validate_tool_for_entity_types(tool_slug: str, entity_types: Iterable[str]) -> PlaybookToolSpec:
    spec = tool_spec(tool_slug)
    kind = execution_target_kind(entity_types)
    if kind not in spec.target_kinds:
        raise ValueError(f"tool '{tool_slug}' does not support {kind} playbook targets")
    return spec


def default_args_template(tool_slug: str, entity_types: Iterable[str]) -> dict[str, Any]:
    kind = execution_target_kind(entity_types)
    validate_tool_for_entity_types(tool_slug, entity_types)
    if tool_slug == "breach-lookup":
        return {"email" if kind == "email" else "domain": "{{scope_item}}"}
    target_arg = {
        "domain": "domain",
        "ip": "ip",
        "cidr": "cidr",
        "url": "url",
        "email": "email",
        "scope": "scope_item",
    }[kind]
    base_slug = tool_slug.removeprefix("mcp_")
    if base_slug in {"port_scan", "service_detect"}:
        target_arg = "target"
    template: dict[str, Any] = {target_arg: "{{scope_item}}"}
    if base_slug in {"port_scan", "service_detect"}:
        template["ports"] = "21,22,25,53,80,110,143,443,445,3389,5432,6379,8080,8443"
    elif base_slug == "subnet_sweep":
        template["ports"] = "22,80,443,445,3389,8080,8443"
    if base_slug == "port_scan":
        template["__on_error"] = "stop"
    return template


def recipe_requires_approval(tool_slugs: Iterable[str]) -> bool:
    return any(tool_spec(slug).risk == "active" for slug in tool_slugs)


def required_credentials(tool_slugs: Iterable[str]) -> list[str]:
    return sorted(
        {spec.credential for slug in tool_slugs if (spec := tool_spec(slug)).credential is not None}
    )


def catalog_tool_specs() -> list[PlaybookToolSpec]:
    return list(_TOOL_SPECS)
