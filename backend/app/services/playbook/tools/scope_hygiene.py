"""Database-backed, report-only review of exact scope entries."""

from __future__ import annotations

import ipaddress
import uuid
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ScopeItem
from app.services.playbook.executor import StepResult

_ROLE_MAILBOXES = {"abuse", "domains", "hostmaster", "noc", "privacy", "registrar", "whois"}


def _host_for(kind: str, value: str) -> str | None:
    if kind == "domain":
        return value.lower().rstrip(".")
    if kind == "email" and "@" in value:
        return value.rsplit("@", 1)[1].lower().rstrip(".")
    if kind == "url":
        return (urlsplit(value).hostname or "").lower().rstrip(".") or None
    return None


def run_scope_hygiene(
    session: Session,
    *,
    engagement_id: uuid.UUID,
    scope_context: str,
) -> StepResult:
    """Classify one selected include without changing authorization state."""
    rows = list(
        session.execute(select(ScopeItem).where(ScopeItem.engagement_id == engagement_id)).scalars()
    )
    matches = [row for row in rows if not row.is_exclusion and row.value == scope_context]
    if not matches:
        return StepResult(ok=False, error="selected scope entry no longer exists")
    row = matches[0]
    kind = row.kind.value
    value = row.value
    defined_hosts = {
        host
        for item in rows
        if not item.is_exclusion and item.source == "defined"
        for host in [_host_for(item.kind.value, item.value)]
        if host
    }
    recommendation = "keep"
    confidence = "high"
    rationale = "Client-defined scope entry"
    code = "client_defined_scope"
    severity = "info"

    if row.source != "defined":
        recommendation = "review"
        confidence = "medium"
        rationale = "Discovered scope entry requires ownership confirmation"
        code = "discovered_scope_review"
        if kind == "ip":
            try:
                address = ipaddress.ip_address(value)
                if not address.is_global:
                    recommendation = "remove_or_document"
                    confidence = "high"
                    rationale = (
                        "Discovered address is loopback, private, reserved, or otherwise non-global"
                    )
                    code = "non_global_discovered_ip"
                    severity = "low"
            except ValueError:
                recommendation = "remove_or_correct"
                confidence = "high"
                rationale = "Stored IP value is malformed"
                code = "malformed_ip_scope"
                severity = "low"
        else:
            host = _host_for(kind, value)
            related = bool(
                host
                and any(host == anchor or host.endswith(f".{anchor}") for anchor in defined_hosts)
            )
            if related:
                recommendation = "keep_if_expected"
                confidence = "high"
                rationale = "Discovered target is under a client-defined domain boundary"
                code = "related_discovered_scope"
            elif kind == "email" and "@" in value:
                local_part = value.rsplit("@", 1)[0].lower()
                if local_part in _ROLE_MAILBOXES:
                    recommendation = "remove_unless_explicitly_authorized"
                    confidence = "high"
                    rationale = "Role mailbox belongs to a domain outside client-defined scope"
                    code = "likely_vendor_role_mailbox"
                    severity = "low"
            elif host:
                recommendation = "verify_third_party_authorization"
                confidence = "medium"
                rationale = "Target is outside client-defined domain boundaries"
                code = "external_dependency_scope"

    exact_exclusions = [
        item for item in rows if item.is_exclusion and item.kind == row.kind and item.value == value
    ]
    issues = [
        {
            "code": code,
            "target": value,
            "kind": kind,
            "scope_item_id": str(row.id),
            "source": row.source,
            "recommendation": recommendation,
            "confidence": confidence,
            "severity": severity,
            "message": rationale,
        }
    ]
    if len(matches) > 1:
        issues.append(
            {
                "code": "duplicate_exact_include",
                "target": value,
                "kind": kind,
                "scope_item_ids": [str(item.id) for item in matches],
                "recommendation": "deduplicate",
                "confidence": "high",
                "severity": "low",
                "message": "Multiple exact include rows represent the same selected value",
            }
        )
    if exact_exclusions:
        issues.append(
            {
                "code": "exact_include_exclusion_collision",
                "target": value,
                "kind": kind,
                "scope_item_id": str(row.id),
                "exclusion_ids": [str(item.id) for item in exact_exclusions],
                "recommendation": "resolve_conflict",
                "confidence": "high",
                "severity": "low",
                "message": (
                    "Exact include and exclusion rows coexist; exclusion remains authoritative"
                ),
            }
        )
    return StepResult(
        ok=True,
        data={
            "check": "scope_hygiene",
            "domain": "scope",
            "issues": issues,
            "observations": [],
        },
        findings_total=len(issues),
    )
