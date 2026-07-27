"""Entity correlation — derive entities from an engagement's findings.

CHARTER Idea 4: surface the emails, hosts, IPs, domains, etc. disclosed across
findings, correlated so each value appears once with all the findings that
mentioned it. Derived on the fly (no separate store yet) — analyst tagging /
persistence is a later enhancement.

The extractor is deliberately conservative: it pulls from each finding's
``target`` plus high-signal patterns (emails, IPv4s) and a few known structured
keys (subdomains/domains/hosts). It does not guess domains from arbitrary text,
to avoid noise.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from app.models import Finding, Severity
from app.services import scope_matcher
from app.services.effective_scope import (
    EffectiveScopeDecision,
    EffectiveScopeState,
    exact_scope_rule_ids_for_entity,
    project_entity,
)
from app.services.entity_identity import normalize_entity_type, normalize_entity_value

EntityType = str  # email | ip | cidr | domain | subdomain | url | host


def classify_entity_scope_status(
    entity_type: str,
    entity_value: str,
    current_scope_items: Iterable[scope_matcher.ScopeItemLike],
    retired_scope_values: set[str],
) -> str:
    """Return "live" | "excluded" | "legacy" | "oos" for one entity.

    - live: value matches at least one CURRENT ScopeItem via ScopeMatcher.
    - legacy: not currently in scope, but a scope.item.deleted audit event
      recorded this exact value (case-insensitive) at some point.
    - oos: neither — discovered from a finding but never a scope target.

    Unknown entity types short-circuit to "oos". Email uses conservative
    exact-mailbox matching; a scoped domain never authorizes every mailbox.
    """
    decision = project_entity(entity_type, entity_value, current_scope_items)
    if decision.state is EffectiveScopeState.included:
        return "live"
    if decision.state is EffectiveScopeState.excluded:
        return "excluded"
    if entity_value.strip().lower() in retired_scope_values:
        return "legacy"
    return "oos"


def include_scope_entities(
    entities: list[dict[str, Any]],
    scope_items: Iterable[scope_matcher.ScopeItemLike],
) -> list[dict[str, Any]]:
    """Merge current in-scope targets into the derived entity inventory.

    Scope is already canonical operator input; requiring a finding before it
    appears in Entities forces duplicate entry and makes a fresh engagement
    look empty. Existing finding-derived rows win and retain provenance/counts.
    """
    result = [dict(entity) for entity in entities]
    seen = {
        (
            normalize_entity_type(entity.get("type")),
            normalize_entity_value(entity.get("type"), entity.get("value")),
        )
        for entity in result
    }
    for item in scope_items:
        if bool(getattr(item, "is_exclusion", False)):
            continue
        kind = getattr(item, "kind", "")
        entity_type = normalize_entity_type(getattr(kind, "value", kind))
        value = normalize_entity_value(entity_type, getattr(item, "value", ""))
        if not entity_type or not value or (entity_type, value) in seen:
            continue
        created_at = getattr(item, "created_at", None)
        updated_at = getattr(item, "updated_at", None) or created_at
        result.append(
            {
                "type": entity_type,
                "value": value,
                "count": 0,
                "severity": Severity.info.value,
                "first_seen": created_at,
                "last_seen": updated_at,
                "findings": [],
            }
        )
        seen.add((entity_type, value))
    return result


_VENDOR_ROLE_MAILBOXES = {
    "abuse",
    "domains",
    "hostmaster",
    "noc",
    "privacy",
    "registrar",
    "whois",
}


def classify_entity_relevance(
    entity_type: str,
    entity_value: str,
    scope_status: str,
) -> tuple[str, str | None]:
    """Conservatively sort likely third-party chaff without deleting evidence.

    Only a narrow, explainable pattern is auto-classified. Everything else
    outside scope remains in the review bucket because CDN, SaaS, registrar,
    and supplier infrastructure may be explicitly authorized in some tests.
    """
    if scope_status == "live":
        return "in_scope", None
    if scope_status == "excluded":
        return "excluded", "Explicitly excluded by engagement scope"
    if entity_type == "email" and "@" in entity_value:
        local_part = entity_value.rsplit("@", 1)[0].lower()
        if local_part in _VENDOR_ROLE_MAILBOXES:
            return (
                "likely_third_party",
                "Role mailbox on a domain outside current scope",
            )
    return "review", "Outside current scope; retain for analyst review"


def annotate_scope_status(
    entities: list[dict[str, Any]],
    *,
    current_scope_items: Iterable[scope_matcher.ScopeItemLike],
    retired_scope_values: set[str],
) -> list[dict[str, Any]]:
    """Attach ``scope_status`` to each entity dict in place and return the list."""
    items = list(current_scope_items)
    for entity in entities:
        entity_type = str(entity.get("type") or "")
        entity_value = str(entity.get("value") or "")
        decision: EffectiveScopeDecision = project_entity(
            entity_type,
            entity_value,
            items,
        )
        scope_status = (
            "live"
            if decision.state is EffectiveScopeState.included
            else "excluded"
            if decision.state is EffectiveScopeState.excluded
            else "legacy"
            if entity_value.strip().lower() in retired_scope_values
            else "oos"
        )
        relevance, reason = classify_entity_relevance(
            entity_type,
            entity_value,
            scope_status,
        )
        exact_include_ids, exact_exclusion_ids = exact_scope_rule_ids_for_entity(
            entity_type,
            entity_value,
            items,
        )
        entity["scope_status"] = scope_status
        entity["effective_scope"] = decision
        entity["exact_scope_include_ids"] = exact_include_ids
        entity["exact_scope_exclusion_ids"] = exact_exclusion_ids
        entity["relevance"] = relevance
        entity["relevance_reason"] = reason
    return entities


_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_CIDR_RE = re.compile(r"^(?:\d{1,3}\.){3}\d{1,3}/\d{1,2}$")
_CIDR_FIND = re.compile(r"(?:\d{1,3}\.){3}\d{1,3}/\d{1,2}")
_DOMAIN_FIND = re.compile(r"\b(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}\b")
# Plural keys → typed list-of-strings. Singular keys → one entry per dict.
# Singular forms exist so finding.details['items'][*].subdomain etc. from
# subfinder/httpx/nmap flow into Discovered Context as promotable indicators.
_HOST_KEYS_PLURAL = {
    "subdomains": "subdomain",
    "domains": "domain",
    "hosts": "host",
    "name_servers": "domain",
    "nameservers": "domain",
    "cname": "domain",
    "ns": "domain",
}
_HOST_KEYS_SINGULAR = {
    "subdomain": "subdomain",
    "domain": "domain",
    "hostname": "host",
    "fqdn": "domain",
    "url": "url",
}

_SEVERITY_RANK: dict[Severity, int] = {
    Severity.critical: 4,
    Severity.high: 3,
    Severity.medium: 2,
    Severity.low: 1,
    Severity.info: 0,
}


def _classify_target(target: str) -> tuple[EntityType, str] | None:
    t = target.strip()
    if not t:
        return None
    if _EMAIL_RE.fullmatch(t):
        return ("email", t.lower())
    if t.startswith(("http://", "https://")):
        return ("url", t)
    if _CIDR_RE.match(t):
        return ("cidr", t)
    host = t.split(":", 1)[0]  # strip :port
    if _IPV4_RE.fullmatch(host):
        return ("ip", host)
    if "." in host and any(c.isalpha() for c in host):
        return ("domain", host.lower())
    return ("host", host)


def _walk(value: Any, sink: list[tuple[EntityType, str]]) -> None:
    if isinstance(value, str):
        for m in _EMAIL_RE.findall(value):
            sink.append(("email", m.lower()))
        for cidr in _CIDR_FIND.findall(value):
            sink.append(("cidr", cidr))
        for ip in _IPV4_RE.findall(value):
            # Skip the network base when it's written as part of a CIDR (e.g.
            # the "172.18.0.0" in "172.18.0.0/28") — that's the cidr, not a host.
            if f"{ip}/" in value:
                continue
            sink.append(("ip", ip))
        return
    if isinstance(value, dict):
        # Canonical DNS grouping stores one record per {type, value} item.
        # Preserve that structure as entities instead of relying on regex over
        # an otherwise context-free string (which intentionally ignores most
        # arbitrary domain-looking prose).
        record_type = str(value.get("type") or "").upper()
        record_value = value.get("value")
        if isinstance(record_value, str) and record_value.strip():
            cleaned = record_value.strip().rstrip(".")
            if record_type in {"A", "AAAA"}:
                sink.append(("ip", cleaned))
            elif record_type in {"CNAME", "NS", "PTR"}:
                sink.append(("domain", cleaned.lower()))
            elif record_type == "MX":
                mx_host = cleaned.split()[-1].rstrip(".")
                if mx_host:
                    sink.append(("domain", mx_host.lower()))
        for k, v in value.items():
            if k in _HOST_KEYS_PLURAL and isinstance(v, list):
                for item in v:
                    if isinstance(item, str) and item.strip():
                        entity_type = _HOST_KEYS_PLURAL[k]
                        normalized = item.strip().lower()
                        if entity_type in {"domain", "subdomain", "host"}:
                            normalized = normalized.rstrip(".")
                        sink.append((entity_type, normalized))
            if k in _HOST_KEYS_SINGULAR and isinstance(v, str) and v.strip():
                entity_type = _HOST_KEYS_SINGULAR[k]
                normalized = v.strip().lower()
                if entity_type in {"domain", "subdomain", "host"}:
                    normalized = normalized.rstrip(".")
                sink.append((entity_type, normalized))
            if k == "host" and isinstance(v, str) and v.strip():
                host = v.strip()
                kind: EntityType = "ip" if _IPV4_RE.fullmatch(host) else "host"
                sink.append((kind, host))
            _walk(v, sink)
        return
    if isinstance(value, list):
        for item in value:
            _walk(item, sink)


def extract_finding_entities(
    finding: Finding,
) -> list[tuple[EntityType, str]]:
    found: list[tuple[EntityType, str]] = []
    if finding.target:
        hit = _classify_target(finding.target)
        if hit:
            found.append(hit)
    details = dict(finding.details or {})
    details.pop("thread_id", None)
    details.pop("args", None)
    _walk(details, found)
    # Dedupe within a single finding.
    return list(dict.fromkeys(found))


def extract_finding_context(finding: Finding) -> list[tuple[EntityType, str]]:
    """Return analyst-reviewable entity candidates from one finding.

    The engagement-wide derived view stays deliberately conservative. This
    finding-scoped path also inspects title/summary and domain-like strings
    because every candidate is shown to an analyst before it is persisted.
    """
    found = extract_finding_entities(finding)
    narrative = f"{finding.title}\n{finding.summary or ''}"
    _walk(narrative, found)
    for domain in _DOMAIN_FIND.findall(narrative):
        found.append(("domain", domain.lower()))
    return list(dict.fromkeys(found))[:100]


def extract_entities(
    findings: Iterable[Finding],
    *,
    type_filter: str | None = None,
    query: str | None = None,
) -> list[dict[str, Any]]:
    """Aggregate entities across findings. Returns one record per
    (type, value), each carrying the findings that disclosed it, the max
    severity, and first/last-seen timestamps. Sorted by severity then count."""
    agg: dict[tuple[str, str], dict[str, Any]] = {}

    for f in findings:
        for etype, value in extract_finding_entities(f):
            key = (etype, value)
            rec = agg.get(key)
            ref = {
                "id": str(f.id),
                "title": f.title,
                "tool": f.source_tool,
                "severity": f.severity.value,
                "phase": f.phase.value,
            }
            if rec is None:
                agg[key] = {
                    "type": etype,
                    "value": value,
                    "severity": f.severity,
                    "first_seen": f.created_at,
                    "last_seen": f.created_at,
                    "findings": [ref],
                }
            else:
                if _SEVERITY_RANK[f.severity] > _SEVERITY_RANK[rec["severity"]]:
                    rec["severity"] = f.severity
                rec["first_seen"] = min(rec["first_seen"], f.created_at)
                rec["last_seen"] = max(rec["last_seen"], f.created_at)
                rec["findings"].append(ref)

    results = list(agg.values())

    if type_filter:
        results = [r for r in results if r["type"] == type_filter]
    if query:
        q = query.lower()
        results = [r for r in results if q in r["value"].lower()]

    for r in results:
        r["count"] = len(r["findings"])
        r["severity"] = r["severity"].value  # serialize enum → str

    results.sort(
        key=lambda r: (_severity_rank_str(r["severity"]), r["count"]),
        reverse=True,
    )
    return results


def _severity_rank_str(s: str) -> int:
    return _SEVERITY_RANK.get(Severity(s), 0)
