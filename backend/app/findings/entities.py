"""Entity correlation — derive entities from an Project's findings.

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

from app.findings.models import Finding, Severity

EntityType = str  # email | ip | cidr | domain | subdomain | url | host

_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_CIDR_RE = re.compile(r"^(?:\d{1,3}\.){3}\d{1,3}/\d{1,2}$")
_CIDR_FIND = re.compile(r"(?:\d{1,3}\.){3}\d{1,3}/\d{1,2}")
_HOST_KEYS = {"subdomains": "subdomain", "domains": "domain", "hosts": "host"}

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
        for k, v in value.items():
            if k in _HOST_KEYS and isinstance(v, list):
                for item in v:
                    if isinstance(item, str) and item.strip():
                        sink.append((_HOST_KEYS[k], item.strip().lower()))
            if k == "host" and isinstance(v, str) and v.strip():
                host = v.strip()
                kind: EntityType = "ip" if _IPV4_RE.fullmatch(host) else "host"
                sink.append((kind, host))
            _walk(v, sink)
        return
    if isinstance(value, list):
        for item in value:
            _walk(item, sink)


def _extract_one(finding: Finding) -> list[tuple[EntityType, str]]:
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
        for etype, value in _extract_one(f):
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
        r["severity"] = r["severity"].value  # serialize enum -> str

    results.sort(
        key=lambda r: (_severity_rank_str(r["severity"]), r["count"]),
        reverse=True,
    )
    return results


def _severity_rank_str(s: str) -> int:
    return _SEVERITY_RANK.get(Severity(s), 0)
