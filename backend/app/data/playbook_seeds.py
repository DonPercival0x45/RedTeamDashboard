"""Seed playbooks — Track A step A3a.

The domain starters map to seeded methodology nodes from A1. Exploration-tier
IP enrichment and exact-mailbox exposure triage are also installed so email
scope has an explicit catalog path instead of falling back to domain runs.

* ``osint-passive-domain`` — satisfies the OSINT-minimal starter's four
  passive domain nodes.
* ``ptes-passive-recon`` — satisfies PTES's passive recon slice (whois,
  subdomains, DNS).

``tool_slug`` values name tools by convention; the ``InternalExecutor`` will
bind them to real implementations in A3b. Tests use a ``MockExecutor`` that
returns canned ``StepResult`` values keyed by tool_slug.

Loader called from a service helper — not auto-installed on migration
(different lifecycle from the methodology catalog: playbooks may be
analyst-curated per tenant).
"""
from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# OSINT passive domain — satisfies osint-minimal v1 baseline nodes
# ---------------------------------------------------------------------------

OSINT_PASSIVE_DOMAIN_V1: dict[str, Any] = {
    "slug": "osint-passive-domain",
    "version": 1,
    "name": "OSINT passive domain recon",
    "description": (
        "Runs the passive-only OSINT domain sweep — subdomain enum via cert "
        "transparency + passive DNS, DNS record lookup, and WHOIS. Satisfies "
        "every baseline node in the OSINT-minimal methodology."
    ),
    "applies_to_asset_class": "domain",
    # ``active`` = False here — passive OSINT bypasses the A5 approval gate.
    # Turn to True on any playbook whose runs should require analyst sign-off
    # before execution (any active-touching probe, hosted-tool call with
    # billed quotas, or high-blast-radius sweep).
    "active": False,
    "steps": [
        {
            "sort_order": 10,
            "tool_slug": "subfinder",
            "args_template": {"domain": "{{scope_item}}"},
            "satisfies_node_ids": ["osint.domain.enum"],
            "description": "Subdomain enumeration via passive sources.",
        },
        {
            "sort_order": 20,
            "tool_slug": "dns-inventory",
            "args_template": {"domain": "{{scope_item}}"},
            "satisfies_node_ids": ["osint.domain.dns"],
            "description": "A / AAAA / MX / TXT / SPF / DMARC records.",
        },
        {
            "sort_order": 30,
            "tool_slug": "crtsh",
            "args_template": {"domain": "{{scope_item}}"},
            "satisfies_node_ids": ["osint.domain.cert"],
            "description": "Certificate transparency scan.",
        },
        {
            "sort_order": 40,
            "tool_slug": "whois",
            "args_template": {"domain": "{{scope_item}}"},
            "satisfies_node_ids": ["osint.domain.whois"],
            "description": "WHOIS registrant + registrar metadata.",
        },
        {
            "sort_order": 50,
            "tool_slug": "breach-lookup",
            "args_template": {"domain": "{{scope_item}}"},
            "satisfies_node_ids": ["osint.domain.breach"],
            "description": "Breach corpus lookup for the domain.",
        },
    ],
}


# ---------------------------------------------------------------------------
# PTES passive recon — subset of the PTES starter tree
# ---------------------------------------------------------------------------

PTES_PASSIVE_RECON_V1: dict[str, Any] = {
    "slug": "ptes-passive-recon",
    "version": 1,
    "name": "PTES passive reconnaissance",
    "description": (
        "PTES's passive reconnaissance slice — subdomain enum, DNS records, "
        "WHOIS. Same shape as the OSINT playbook but tagged to PTES nodes."
    ),
    "applies_to_asset_class": "domain",
    "active": False,
    "steps": [
        {
            "sort_order": 10,
            "tool_slug": "whois",
            "args_template": {"domain": "{{scope_item}}"},
            "satisfies_node_ids": ["recon.passive.whois"],
            "description": "WHOIS registration data.",
        },
        {
            "sort_order": 20,
            "tool_slug": "subfinder",
            "args_template": {"domain": "{{scope_item}}"},
            "satisfies_node_ids": ["recon.passive.subdomains"],
            "description": "Passive subdomain enumeration.",
        },
        {
            "sort_order": 30,
            "tool_slug": "dns-inventory",
            "args_template": {"domain": "{{scope_item}}"},
            "satisfies_node_ids": ["recon.passive.dns"],
            "description": "DNS record inventory.",
        },
    ],
}


OSINT_ENRICHMENT_V1: dict[str, Any] = {
    "slug": "osint-enrichment",
    "version": 1,
    "name": "OSINT IP enrichment (MCP)",
    "description": (
        "Enrichment sweep for a single IP: geo/ISP via freeipapi, ASN + "
        "hosting-provider metadata via ipinfo. Targeted at MCP dispatch — "
        "``executor='mcp'`` on run creation routes to the corresponding MCP "
        "tools. No baseline node satisfaction — enrichment is exploration-tier "
        "context, not a coverage gate."
    ),
    "applies_to_asset_class": "ip",
    "active": False,
    "steps": [
        {
            "sort_order": 10,
            "tool_slug": "freeipapi",
            "args_template": {"ip": "{{scope_item}}"},
            "satisfies_node_ids": [],
            "description": "Geo / ISP / continent lookup via freeipapi.com.",
        },
        {
            "sort_order": 20,
            "tool_slug": "ipinfo",
            "args_template": {"ip": "{{scope_item}}"},
            "satisfies_node_ids": [],
            "description": "ASN + org + hosting metadata via ipinfo.io.",
        },
    ],
}


EMAIL_EXPOSURE_TRIAGE_V1: dict[str, Any] = {
    "slug": "email-exposure-triage",
    "version": 1,
    "name": "Email exposure triage",
    "description": (
        "Checks an explicitly scoped mailbox against DeHashed records already "
        "imported into this engagement. Exact email scope is required; "
        "authorizing a domain does not authorize every mailbox at that domain. "
        "No breach evidence leaves the deployment during this lookup."
    ),
    "applies_to_asset_class": "email",
    "active": False,
    "steps": [
        {
            "sort_order": 10,
            "tool_slug": "breach-lookup",
            "args_template": {"email": "{{scope_item}}"},
            "satisfies_node_ids": [],
            "description": "Match the exact mailbox against imported DeHashed evidence.",
        },
    ],
}


EMAIL_EXPOSURE_TRIAGE_V2: dict[str, Any] = {
    **EMAIL_EXPOSURE_TRIAGE_V1,
    "version": 2,
}


DOMAIN_WEB_SURFACE_V1: dict[str, Any] = {
    "slug": "domain-web-surface",
    "version": 1,
    "name": "Domain and web-surface discovery",
    "description": (
        "Runs real passive collection through the MCP tool plane: subdomain "
        "enumeration, certificate transparency, DNS resolution, and an HTTP "
        "technology probe for each selected domain."
    ),
    "applies_to_asset_class": "domain",
    "active": False,
    "steps": [
        {
            "sort_order": 10,
            "tool_slug": "mcp_subfinder",
            "args_template": {"domain": "{{scope_item}}"},
            "satisfies_node_ids": ["osint.domain.enum"],
            "description": "Real passive subdomain enumeration.",
        },
        {
            "sort_order": 20,
            "tool_slug": "mcp_crt_sh",
            "args_template": {"domain": "{{scope_item}}"},
            "satisfies_node_ids": ["osint.domain.cert"],
            "description": "Certificate-transparency discovery.",
        },
        {
            "sort_order": 30,
            "tool_slug": "mcp_dns_lookup",
            "args_template": {"domain": "{{scope_item}}"},
            "satisfies_node_ids": ["osint.domain.dns"],
            "description": "Resolve current A, AAAA, and CNAME records.",
        },
        {
            "sort_order": 40,
            "tool_slug": "mcp_httpx_probe",
            "args_template": {
                "url": "{{scope_item}}",
                "__target_source": "discovered_domains",
            },
            "satisfies_node_ids": [],
            "description": "Identify reachable web services and technology signals.",
        },
    ],
}


DOMAIN_WEB_SURFACE_V2: dict[str, Any] = {
    **DOMAIN_WEB_SURFACE_V1,
    "version": 2,
    "description": (
        "Runs real passive discovery, then expands authorized subdomains from "
        "Subfinder and certificate transparency into HTTP technology probes. "
        "Every discovered target is rechecked against current exclusions."
    ),
}


DOMAIN_WEB_SURFACE_V3: dict[str, Any] = {
    **DOMAIN_WEB_SURFACE_V2,
    "version": 3,
    "description": (
        "Combines built-in WHOIS with connected passive discovery, then "
        "expands authorized subdomains into HTTP technology probes. Every "
        "discovered target is rechecked against current exclusions."
    ),
    "steps": [
        {
            "tool_slug": "whois",
            "args_template": {"domain": "{{scope_item}}"},
            "satisfies_node_ids": [],
            "description": "Collect registration and nameserver context.",
        },
        *DOMAIN_WEB_SURFACE_V2["steps"],
    ],
}


OSINT_ENRICHMENT_V2: dict[str, Any] = {
    **OSINT_ENRICHMENT_V1,
    "version": 2,
    "name": "IP intelligence and ownership",
    "description": (
        "Passive IP triage: reverse DNS, geolocation/ISP context, and "
        "ASN/hosting/VPN/proxy/Tor intelligence. FreeIPAPI and IPinfo require "
        "requester-owned credentials; reverse DNS is keyless."
    ),
    "steps": [
        {
            "sort_order": 10,
            "tool_slug": "mcp_reverse_dns",
            "args_template": {"ip": "{{scope_item}}"},
            "satisfies_node_ids": [],
            "description": "Resolve the IP's PTR hostname.",
        },
        {
            "sort_order": 20,
            "tool_slug": "freeipapi",
            "args_template": {"ip": "{{scope_item}}"},
            "satisfies_node_ids": [],
            "description": "Geo and ISP context via FreeIPAPI.",
        },
        {
            "sort_order": 30,
            "tool_slug": "ipinfo",
            "args_template": {"ip": "{{scope_item}}"},
            "satisfies_node_ids": [],
            "description": "ASN, ownership, and hosting/privacy signals via IPinfo.",
        },
    ],
}


HOST_SERVICE_VALIDATION_V1: dict[str, Any] = {
    "slug": "host-service-validation",
    "version": 1,
    "name": "Host service validation",
    "description": (
        "Actively validates a bounded set of common exposure ports, then "
        "fingerprints services on the same ports. Requires analyst approval "
        "before any target connection is made."
    ),
    "applies_to_asset_class": "ip",
    "active": True,
    "steps": [
        {
            "sort_order": 10,
            "tool_slug": "mcp_port_scan",
            "args_template": {
                "target": "{{scope_item}}",
                "ports": "21,22,25,53,80,110,143,443,445,3389,5432,6379,8080,8443",
                "__on_error": "stop",
            },
            "satisfies_node_ids": [],
            "description": "TCP-connect scan of a bounded common-port set.",
        },
        {
            "sort_order": 20,
            "tool_slug": "mcp_service_detect",
            "args_template": {
                "target": "{{scope_item}}",
                "ports": "21,22,25,53,80,110,143,443,445,3389,5432,6379,8080,8443",
            },
            "satisfies_node_ids": [],
            "description": "Banner, HTTP, and TLS fingerprinting on the approved ports.",
        },
    ],
}


HOST_SERVICE_VALIDATION_V2: dict[str, Any] = {
    **HOST_SERVICE_VALIDATION_V1,
    "version": 2,
}


CIDR_EXPOSURE_SURVEY_V1: dict[str, Any] = {
    "slug": "cidr-exposure-survey",
    "version": 1,
    "name": "CIDR exposure survey",
    "description": (
        "Actively surveys an authorized CIDR (maximum /24) for a bounded set "
        "of common exposure ports. Explicit host exclusions are enforced by "
        "the tool and analyst approval is required before execution."
    ),
    "applies_to_asset_class": "cidr",
    "active": True,
    "steps": [
        {
            "sort_order": 10,
            "tool_slug": "mcp_subnet_sweep",
            "args_template": {
                "cidr": "{{scope_item}}",
                "ports": "22,80,443,445,3389,8080,8443",
            },
            "satisfies_node_ids": [],
            "description": "Bounded TCP sweep with per-host scope exclusions.",
        }
    ],
}


MAIL_DNS_POSTURE_V1: dict[str, Any] = {
    "slug": "mail-dns-posture",
    "version": 1,
    "name": "Mail and DNS posture collection",
    "description": (
        "Collects authoritative DNS and certificate-transparency evidence for "
        "mail and domain posture review, including MX/TXT evidence persisted "
        "through the canonical DNS finding group."
    ),
    "applies_to_asset_class": "domain",
    "active": False,
    "steps": [
        {
            "sort_order": 10,
            "tool_slug": "mcp_dns_lookup",
            "args_template": {"domain": "{{scope_item}}"},
            "satisfies_node_ids": ["osint.domain.dns"],
            "description": "Collect DNS evidence used for mail posture analysis.",
        },
        {
            "sort_order": 20,
            "tool_slug": "mcp_crt_sh",
            "args_template": {"domain": "{{scope_item}}"},
            "satisfies_node_ids": ["osint.domain.cert"],
            "description": "Collect certificate names related to the domain.",
        },
    ],
}


SEED_PLAYBOOKS: list[dict[str, Any]] = [
    OSINT_PASSIVE_DOMAIN_V1,
    PTES_PASSIVE_RECON_V1,
    OSINT_ENRICHMENT_V1,
    OSINT_ENRICHMENT_V2,
    EMAIL_EXPOSURE_TRIAGE_V1,
    EMAIL_EXPOSURE_TRIAGE_V2,
    DOMAIN_WEB_SURFACE_V1,
    DOMAIN_WEB_SURFACE_V2,
    DOMAIN_WEB_SURFACE_V3,
    HOST_SERVICE_VALIDATION_V1,
    HOST_SERVICE_VALIDATION_V2,
    CIDR_EXPOSURE_SURVEY_V1,
    MAIL_DNS_POSTURE_V1,
]
