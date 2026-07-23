"""Seed playbooks — Track A step A3a.

Two starter playbooks that map to the seeded methodology nodes from A1. Both
target the ``domain`` asset class since the seeded trees emphasize passive
domain recon.

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


SEED_PLAYBOOKS: list[dict[str, Any]] = [
    OSINT_PASSIVE_DOMAIN_V1,
    PTES_PASSIVE_RECON_V1,
    OSINT_ENRICHMENT_V1,
]
