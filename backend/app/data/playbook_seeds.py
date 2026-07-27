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


DOMAIN_WEB_SURFACE_V4: dict[str, Any] = {
    **DOMAIN_WEB_SURFACE_V3,
    "version": 4,
    "active": True,
    "description": (
        "Combines registration and passive discovery, then makes bounded HTTP probes "
        "against authorized names. Every discovered target is rechecked against current "
        "exclusions, and analyst approval is required before direct target contact."
    ),
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


MAIL_DNS_POSTURE_V2: dict[str, Any] = {
    **MAIL_DNS_POSTURE_V1,
    "version": 2,
    "name": "Mail authentication posture",
    "description": (
        "Keyless, bounded review of MX, SPF, DMARC, MTA-STS, and SMTP TLS "
        "reporting. DKIM is reported as not tested because selector discovery "
        "cannot be exhaustive without configured selectors."
    ),
    "steps": [
        {
            "sort_order": 10,
            "tool_slug": "mail-auth-posture",
            "args_template": {"domain": "{{scope_item}}"},
            "satisfies_node_ids": ["osint.domain.dns"],
            "description": "Analyze keyless mail-authentication DNS evidence.",
        }
    ],
}


SCOPE_HYGIENE_REVIEW_V1: dict[str, Any] = {
    "slug": "scope-hygiene-review",
    "version": 1,
    "name": "Scope hygiene review",
    "description": (
        "Reviews client-defined and discovered exact scope entries for provenance, "
        "external dependencies, non-global IPs, role mailboxes, duplicates, and "
        "include/exclusion collisions. Report-only: it never changes authorization."
    ),
    "applies_to_asset_class": "scope",
    "active": False,
    "steps": [
        {
            "sort_order": 10,
            "tool_slug": "scope-hygiene",
            "args_template": {},
            "satisfies_node_ids": [],
            "description": (
                "Produce explainable keep/review/remove recommendations without mutation."
            ),
        }
    ],
}


DNS_OWNERSHIP_BOUNDARY_V1: dict[str, Any] = {
    "slug": "dns-ownership-boundary",
    "version": 1,
    "name": "DNS ownership boundary",
    "description": (
        "Maps authoritative DNS, mail, address, SOA, and CNAME dependencies. "
        "External infrastructure is evidence for review, not an ownership verdict."
    ),
    "applies_to_asset_class": "domain",
    "active": False,
    "steps": [
        {
            "sort_order": 10,
            "tool_slug": "dns-ownership-boundary",
            "args_template": {"domain": "{{scope_item}}"},
            "satisfies_node_ids": ["osint.domain.dns"],
            "description": "Map DNS and mail dependencies across the domain boundary.",
        }
    ],
}


DANGLING_DNS_TRIAGE_V1: dict[str, Any] = {
    "slug": "dangling-dns-triage",
    "version": 1,
    "name": "Dangling DNS triage",
    "description": (
        "Discovers authorized names, follows a bounded CNAME check, and flags "
        "NXDOMAIN-backed dangling candidates. It never claims provider resources "
        "or labels a takeover confirmed."
    ),
    "applies_to_asset_class": "domain",
    "active": False,
    "steps": [
        {
            "sort_order": 10,
            "tool_slug": "mcp_subfinder",
            "args_template": {"domain": "{{scope_item}}"},
            "satisfies_node_ids": ["osint.domain.subdomains"],
            "description": "Discover candidate hostnames passively.",
        },
        {
            "sort_order": 20,
            "tool_slug": "mcp_crt_sh",
            "args_template": {"domain": "{{scope_item}}"},
            "satisfies_node_ids": ["osint.domain.cert"],
            "description": "Add certificate-transparency hostnames.",
        },
        {
            "sort_order": 30,
            "tool_slug": "dangling-dns-triage",
            "args_template": {
                "domain": "{{scope_item}}",
                "__target_source": "discovered_domains",
            },
            "satisfies_node_ids": [],
            "description": "Triage authorized CNAME targets without claiming resources.",
        },
    ],
}


WEB_SECURITY_BASELINE_V1: dict[str, Any] = {
    "slug": "web-security-baseline",
    "version": 1,
    "name": "Web security baseline",
    "description": (
        "Makes one bounded HTTPS request per authorized domain, without redirects "
        "or crawling, and reviews common response headers and cookie flags."
    ),
    "applies_to_asset_class": "domain",
    "active": False,
    "steps": [
        {
            "sort_order": 10,
            "tool_slug": "web-security-baseline",
            "args_template": {"url": "{{scope_item}}"},
            "satisfies_node_ids": [],
            "description": "Collect one response and assess baseline browser protections.",
        }
    ],
}


WEB_SECURITY_BASELINE_V2: dict[str, Any] = {
    **WEB_SECURITY_BASELINE_V1,
    "version": 2,
    "active": True,
    "description": (
        "Makes one bounded HTTPS request per authorized domain, without redirects or "
        "crawling, and reviews common response headers and cookie flags. Analyst approval "
        "is required before direct target contact."
    ),
}


CLOUD_EDGE_BOUNDARY_V1: dict[str, Any] = {
    "slug": "cloud-edge-boundary",
    "version": 1,
    "name": "Cloud/CDN edge boundary",
    "description": (
        "Correlates CNAME, address, and bounded HTTP header signals for common "
        "delivery providers. It does not probe for hidden origins or equate an "
        "edge address with client ownership."
    ),
    "applies_to_asset_class": "domain",
    "active": False,
    "steps": [
        {
            "sort_order": 10,
            "tool_slug": "cloud-edge-boundary",
            "args_template": {"domain": "{{scope_item}}"},
            "satisfies_node_ids": ["osint.domain.dns"],
            "description": "Map explicit edge-provider signals without origin bypass.",
        }
    ],
}


CLOUD_EDGE_BOUNDARY_V2: dict[str, Any] = {
    **CLOUD_EDGE_BOUNDARY_V1,
    "version": 2,
    "active": True,
    "description": (
        "Correlates CNAME, address, and one bounded HTTP header snapshot for common "
        "delivery providers. It does not probe hidden origins or equate an edge address "
        "with client ownership. Analyst approval is required before target contact."
    ),
}


EXTERNAL_ATTACK_SURFACE_BASELINE_V1: dict[str, Any] = {
    "slug": "external-attack-surface-baseline",
    "version": 1,
    "name": "External attack surface baseline",
    "description": (
        "Builds a broad, evidence-backed baseline for an authorized domain: registration, "
        "passive hostname discovery, certificate and DNS inventory, ownership boundaries, "
        "cloud/CDN signals, mail authentication, dangling-DNS candidates, and bounded web "
        "posture. Newly discovered authorized names are revalidated against current scope "
        "and exclusions before the final posture checks."
    ),
    "applies_to_asset_class": "domain",
    "applicable_entity_types": ["domain"],
    "active": True,
    "steps": [
        {
            "sort_order": 10,
            "tool_slug": "whois",
            "args_template": {"domain": "{{scope_item}}"},
            "satisfies_node_ids": ["osint.domain.whois"],
            "description": "Collect registration, registrar, and nameserver context.",
        },
        {
            "sort_order": 20,
            "tool_slug": "mcp_subfinder",
            "args_template": {"domain": "{{scope_item}}"},
            "satisfies_node_ids": ["osint.domain.enum"],
            "description": "Discover hostnames from passive sources.",
        },
        {
            "sort_order": 30,
            "tool_slug": "mcp_crt_sh",
            "args_template": {"domain": "{{scope_item}}"},
            "satisfies_node_ids": ["osint.domain.cert"],
            "description": "Collect certificate-transparency names and evidence.",
        },
        {
            "sort_order": 40,
            "tool_slug": "mcp_dns_lookup",
            "args_template": {"domain": "{{scope_item}}"},
            "satisfies_node_ids": ["osint.domain.dns"],
            "description": "Resolve current address, alias, mail, and TXT evidence.",
        },
        {
            "sort_order": 50,
            "tool_slug": "dns-ownership-boundary",
            "args_template": {"domain": "{{scope_item}}"},
            "satisfies_node_ids": ["osint.domain.dns"],
            "description": "Map authoritative and third-party DNS dependencies.",
        },
        {
            "sort_order": 60,
            "tool_slug": "cloud-edge-boundary",
            "args_template": {"domain": "{{scope_item}}"},
            "satisfies_node_ids": ["osint.domain.dns"],
            "description": "Identify explicit cloud and delivery-edge signals.",
        },
        {
            "sort_order": 70,
            "tool_slug": "mail-auth-posture",
            "args_template": {"domain": "{{scope_item}}"},
            "satisfies_node_ids": ["osint.domain.dns"],
            "description": "Review SPF, DMARC, MTA-STS, and SMTP TLS reporting.",
        },
        {
            "sort_order": 80,
            "tool_slug": "dangling-dns-triage",
            "args_template": {
                "domain": "{{scope_item}}",
                "__target_source": "discovered_domains",
            },
            "satisfies_node_ids": [],
            "description": "Triage authorized discovered names for dangling CNAME evidence.",
        },
        {
            "sort_order": 90,
            "tool_slug": "web-security-baseline",
            "args_template": {
                "url": "{{scope_item}}",
                "__target_source": "discovered_domains",
            },
            "satisfies_node_ids": [],
            "description": "Collect a bounded HTTPS posture snapshot for authorized names.",
        },
    ],
}


IP_EXPOSURE_TRIAGE_V1: dict[str, Any] = {
    "slug": "ip-exposure-triage",
    "version": 1,
    "name": "IP exposure triage",
    "description": (
        "Combines passive IP ownership and network context with an approved bounded port "
        "scan and service fingerprint. Active connections never begin until an analyst "
        "approves the immutable execution plan. Service detection uses the same bounded "
        "port profile rather than dynamically consuming scan output."
    ),
    "applies_to_asset_class": "ip",
    "applicable_entity_types": ["ip"],
    "active": True,
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
            "description": "Collect geolocation and ISP context with the requester's key.",
        },
        {
            "sort_order": 30,
            "tool_slug": "ipinfo",
            "args_template": {"ip": "{{scope_item}}"},
            "satisfies_node_ids": [],
            "description": "Collect ASN, ownership, and hosting/privacy signals.",
        },
        {
            "sort_order": 40,
            "tool_slug": "mcp_port_scan",
            "args_template": {
                "target": "{{scope_item}}",
                "ports": "21,22,25,53,80,110,143,443,445,3389,5432,6379,8080,8443",
                "__on_error": "stop",
            },
            "satisfies_node_ids": [],
            "description": "Scan the approved common-exposure port profile.",
        },
        {
            "sort_order": 50,
            "tool_slug": "mcp_service_detect",
            "args_template": {
                "target": "{{scope_item}}",
                "ports": "21,22,25,53,80,110,143,443,445,3389,5432,6379,8080,8443",
            },
            "satisfies_node_ids": [],
            "description": "Fingerprint services on the approved port profile.",
        },
    ],
}


DOMAIN_DECOMMISSION_RISK_REVIEW_V1: dict[str, Any] = {
    "slug": "domain-decommission-risk-review",
    "version": 1,
    "name": "Domain decommission risk review",
    "description": (
        "Reviews an authorized domain for certificate-transparency names, external DNS "
        "and delivery dependencies, and dangling CNAME candidates. Results are evidence "
        "for analyst review and never claim or modify external resources."
    ),
    "applies_to_asset_class": "domain",
    "applicable_entity_types": ["domain"],
    "active": True,
    "steps": [
        {
            "sort_order": 10,
            "tool_slug": "mcp_dns_lookup",
            "args_template": {"domain": "{{scope_item}}"},
            "satisfies_node_ids": ["osint.domain.dns"],
            "description": "Collect current DNS evidence for the selected domain.",
        },
        {
            "sort_order": 20,
            "tool_slug": "mcp_crt_sh",
            "args_template": {"domain": "{{scope_item}}"},
            "satisfies_node_ids": ["osint.domain.cert"],
            "description": "Collect certificate-transparency names that may outlive services.",
        },
        {
            "sort_order": 30,
            "tool_slug": "dns-ownership-boundary",
            "args_template": {"domain": "{{scope_item}}"},
            "satisfies_node_ids": ["osint.domain.dns"],
            "description": "Map authoritative, mail, alias, and address dependencies.",
        },
        {
            "sort_order": 40,
            "tool_slug": "cloud-edge-boundary",
            "args_template": {"domain": "{{scope_item}}"},
            "satisfies_node_ids": ["osint.domain.dns"],
            "description": "Identify cloud and CDN dependencies that require ownership review.",
        },
        {
            "sort_order": 50,
            "tool_slug": "dangling-dns-triage",
            "args_template": {
                "domain": "{{scope_item}}",
                "__target_source": "discovered_domains",
            },
            "satisfies_node_ids": [],
            "description": "Triage authorized certificate names for dangling CNAME evidence.",
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
    DOMAIN_WEB_SURFACE_V4,
    HOST_SERVICE_VALIDATION_V1,
    HOST_SERVICE_VALIDATION_V2,
    CIDR_EXPOSURE_SURVEY_V1,
    MAIL_DNS_POSTURE_V1,
    MAIL_DNS_POSTURE_V2,
    SCOPE_HYGIENE_REVIEW_V1,
    DNS_OWNERSHIP_BOUNDARY_V1,
    DANGLING_DNS_TRIAGE_V1,
    WEB_SECURITY_BASELINE_V1,
    WEB_SECURITY_BASELINE_V2,
    CLOUD_EDGE_BOUNDARY_V1,
    CLOUD_EDGE_BOUNDARY_V2,
    EXTERNAL_ATTACK_SURFACE_BASELINE_V1,
    IP_EXPOSURE_TRIAGE_V1,
    DOMAIN_DECOMMISSION_RISK_REVIEW_V1,
]
