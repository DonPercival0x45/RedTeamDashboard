"""Seed trees for the methodology catalog — Track A step A1.

Three starter methodologies. **Minimal** by design (architecture-answers §D):
enough to smoke A2 (baseline-complete + stale sweep) end-to-end, not the full
published specs — playbooks don't exist yet to satisfy 90% of a real PTES /
MITRE tree, so a bigger seed would just show a permanent gap. Real content
lands as playbooks are built out in A3.

Each seed is a plain dict; ``load_seed_catalog`` in ``services/methodology``
upserts them into the DB by ``(slug, version)``. Bumping a seed's ``version``
gets it side-by-side with the old one — engagements pinned to the old version
keep their snapshot; new engagements pick up the new version at selection.

Node ``ttl_days`` is the freshness window A2's stale sweep uses. ``None`` =
never lapses (static metadata like whois usually doesn't). Passive vs active
recon nodes get different TTLs: passive results decay slowly (weeks), active
scans decay fast (a port scan more than a week old is basically new work).
"""
from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# PTES — passive + active recon slice
# ---------------------------------------------------------------------------

PTES_V1: dict[str, Any] = {
    "slug": "ptes",
    "version": 1,
    "name": "PTES — Reconnaissance (starter)",
    "description": (
        "Penetration Testing Execution Standard — reconnaissance slice only, "
        "trimmed to the passive + active recon nodes we currently have "
        "playbook coverage for. Expands as more playbooks land."
    ),
    "source_url": "http://www.pentest-standard.org/",
    "nodes": [
        {
            "node_id": "recon.passive",
            "parent_node_id": None,
            "title": "Passive intelligence gathering",
            "description": "Zero direct interaction with target infra.",
            "tier": "baseline",
            "asset_class": "domain",
            "ttl_days": 30,
            "sort_order": 10,
        },
        {
            "node_id": "recon.passive.whois",
            "parent_node_id": "recon.passive",
            "title": "WHOIS registration data",
            "description": "Registrant / registrar / nameserver metadata.",
            "tier": "baseline",
            "asset_class": "domain",
            "ttl_days": None,
            "sort_order": 20,
        },
        {
            "node_id": "recon.passive.subdomains",
            "parent_node_id": "recon.passive",
            "title": "Subdomain enumeration (passive)",
            "description": "Certificate transparency, passive DNS.",
            "tier": "baseline",
            "asset_class": "domain",
            "ttl_days": 14,
            "sort_order": 30,
        },
        {
            "node_id": "recon.passive.dns",
            "parent_node_id": "recon.passive",
            "title": "DNS record inventory",
            "description": "A / AAAA / MX / TXT / SPF / DMARC.",
            "tier": "baseline",
            "asset_class": "domain",
            "ttl_days": 30,
            "sort_order": 40,
        },
        {
            "node_id": "recon.active",
            "parent_node_id": None,
            "title": "Active reconnaissance",
            "description": "Direct-touch scanning against in-scope hosts.",
            "tier": "baseline",
            "asset_class": "ip",
            "ttl_days": 7,
            "sort_order": 50,
        },
        {
            "node_id": "recon.active.portscan",
            "parent_node_id": "recon.active",
            "title": "TCP/UDP port scan",
            "description": "Enumerate open ports on in-scope IPs.",
            "tier": "baseline",
            "asset_class": "ip",
            "ttl_days": 7,
            "sort_order": 60,
        },
        {
            "node_id": "recon.active.service_id",
            "parent_node_id": "recon.active",
            "title": "Service + version fingerprint",
            "description": "Banner grab + protocol probe on discovered ports.",
            "tier": "baseline",
            "asset_class": "ip",
            "ttl_days": 7,
            "sort_order": 70,
        },
        {
            "node_id": "recon.exploration.rabbit_hole",
            "parent_node_id": None,
            "title": "Exploration follow-ups",
            "description": (
                "Analyst / agent leads off the beaten path. Not a baseline "
                "gate; recorded here so exploration coverage rolls up too."
            ),
            "tier": "exploration",
            "asset_class": "domain",
            "ttl_days": None,
            "sort_order": 100,
        },
    ],
}


# ---------------------------------------------------------------------------
# MITRE ATT&CK — Reconnaissance TA slice
# ---------------------------------------------------------------------------

MITRE_ATTACK_V1: dict[str, Any] = {
    "slug": "mitre-attack",
    "version": 1,
    "name": "MITRE ATT&CK — Reconnaissance (TA0043 starter)",
    "description": (
        "MITRE ATT&CK Enterprise — reconnaissance tactic only, mapped to the "
        "techniques we can currently cover. Broader TAs (Initial Access, "
        "Execution, …) land in follow-up methodology versions."
    ),
    "source_url": "https://attack.mitre.org/tactics/TA0043/",
    "nodes": [
        {
            "node_id": "T1595",
            "parent_node_id": None,
            "title": "Active Scanning",
            "description": "T1595 — direct-network reconnaissance of targets.",
            "tier": "baseline",
            "asset_class": "ip",
            "ttl_days": 7,
            "sort_order": 10,
        },
        {
            "node_id": "T1595.001",
            "parent_node_id": "T1595",
            "title": "Scanning IP Blocks",
            "description": "Sweep an in-scope CIDR range.",
            "tier": "baseline",
            "asset_class": "cidr",
            "ttl_days": 7,
            "sort_order": 20,
        },
        {
            "node_id": "T1595.002",
            "parent_node_id": "T1595",
            "title": "Vulnerability Scanning",
            "description": "Enumerate known-vulnerable services.",
            "tier": "baseline",
            "asset_class": "ip",
            "ttl_days": 7,
            "sort_order": 30,
        },
        {
            "node_id": "T1590",
            "parent_node_id": None,
            "title": "Gather Victim Network Information",
            "description": "T1590 — DNS, network topology, IP ranges.",
            "tier": "baseline",
            "asset_class": "domain",
            "ttl_days": 30,
            "sort_order": 40,
        },
        {
            "node_id": "T1590.002",
            "parent_node_id": "T1590",
            "title": "DNS",
            "description": "Enumerate DNS records for the target.",
            "tier": "baseline",
            "asset_class": "domain",
            "ttl_days": 30,
            "sort_order": 50,
        },
        {
            "node_id": "T1596",
            "parent_node_id": None,
            "title": "Search Open Technical Databases",
            "description": "T1596 — WHOIS, cert transparency, code repos.",
            "tier": "baseline",
            "asset_class": "domain",
            "ttl_days": 30,
            "sort_order": 60,
        },
    ],
}


# ---------------------------------------------------------------------------
# OSINT — minimal domain-recon starter
# ---------------------------------------------------------------------------

OSINT_MINIMAL_V1: dict[str, Any] = {
    "slug": "osint-minimal",
    "version": 1,
    "name": "OSINT — Passive Domain Recon (starter)",
    "description": (
        "Minimal passive-only tree for OSINT-only engagements. Everything "
        "here can be satisfied without touching target infrastructure — the "
        "engagement type that doesn't need scope gate approvals per node."
    ),
    "source_url": None,
    "nodes": [
        {
            "node_id": "osint.domain.enum",
            "parent_node_id": None,
            "title": "Subdomain enumeration",
            "description": "crt.sh / passive DNS / brute list.",
            "tier": "baseline",
            "asset_class": "domain",
            "ttl_days": 14,
            "sort_order": 10,
        },
        {
            "node_id": "osint.domain.dns",
            "parent_node_id": None,
            "title": "DNS record inventory",
            "description": "A / AAAA / MX / TXT — passive DNS lookup.",
            "tier": "baseline",
            "asset_class": "domain",
            "ttl_days": 30,
            "sort_order": 20,
        },
        {
            "node_id": "osint.domain.cert",
            "parent_node_id": None,
            "title": "Certificate transparency",
            "description": "crt.sh scan for issued certs.",
            "tier": "baseline",
            "asset_class": "domain",
            "ttl_days": 30,
            "sort_order": 30,
        },
        {
            "node_id": "osint.domain.whois",
            "parent_node_id": None,
            "title": "WHOIS metadata",
            "description": "Registrant + registrar records.",
            "tier": "baseline",
            "asset_class": "domain",
            "ttl_days": None,
            "sort_order": 40,
        },
        {
            "node_id": "osint.domain.breach",
            "parent_node_id": None,
            "title": "Breach corpus lookup",
            "description": "Check target domain against known credential leaks.",
            "tier": "baseline",
            "asset_class": "domain",
            "ttl_days": 30,
            "sort_order": 50,
        },
    ],
}


SEED_METHODOLOGIES: list[dict[str, Any]] = [PTES_V1, MITRE_ATTACK_V1, OSINT_MINIMAL_V1]
