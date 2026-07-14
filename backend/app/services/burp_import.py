"""Burp Suite Pro Issue Export XML import — v0.7.0.

Parses the XML you get from Burp's *Export issues* dialog (Site Map or
Issue Activity → right-click → Report selected issues → XML) into a list
of finding-shaped rows that flow through the same import-persistence path
as the Nessus and JSON importers. Each ``<issue>`` element becomes one
``Finding`` with ``phase=vuln_scan``, ``source_tool="burp_import"``, and
the Burp metadata stashed under ``details`` JSONB for slide-over rendering.

Dedup key is ``<serialNumber>`` — a stable per-issue identifier in Burp's
export. The persistence helper compares against
``(engagement_id, burp_serial_number)`` and skips rows that already exist,
so re-importing the same XML after a re-scan is safe.

Charter posture: same as Nessus — analyst runs Burp Pro on their own
infra and uploads the result here. We don't shell out to Burp or talk
to a Burp REST API. Findings land ``status=pending_validation`` (Burp's
phase is ``vuln_scan``) and need analyst sign-off before the report
includes them.

XML safety: ``defusedxml.ElementTree`` only — Burp exports are
externally-sourced and the stdlib parser is vulnerable to
billion-laughs / XXE.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any

from defusedxml import ElementTree

from app.models import FindingPhase, ScopeItem, Severity
from app.services.scope_matcher import evaluate_scope_candidates, infer_scope_kind

# Burp Pro severity strings → our Severity enum. "False Positive" rarely
# appears in exports (analyst-marked) but we map it to info just in case.
_BURP_SEVERITY: dict[str, Severity] = {
    "high": Severity.high,
    "medium": Severity.medium,
    "low": Severity.low,
    "information": Severity.info,
    "info": Severity.info,
    "false positive": Severity.info,
}


@dataclass
class ParsedItem:
    """One Burp <issue> reduced to a finding-shaped row.

    Duck-typed against ``FindingImport`` (in ``app.api.engagements``) so
    the shared persistence helper accepts either shape.
    """

    title: str
    severity: Severity
    phase: FindingPhase
    summary: str | None
    target: str
    source_tool: str
    details: dict[str, Any] = field(default_factory=dict)
    observed_at: datetime | None = None
    burp_serial_number: str | None = None
    # v1.4.0: Burp issues of the same type fold under one finding row —
    # every affected URL/path becomes an item[]. Stamped as
    # ``burp:{issue-type-or-name}``. Prefer the numeric ``type`` (stable
    # across Burp releases) with the human-readable ``name`` as fallback.
    group_key: str | None = None


@dataclass
class ParseResult:
    """What ``parse_burp_xml`` returns. Skipped counts are surfaced on
    the response so the analyst can verify the filter dropped what they
    expected (info-rows excluded by default, out-of-scope hosts, etc.).
    """

    items: list[ParsedItem]
    skipped_info: int
    skipped_out_of_scope: int
    total_items: int
    export_time: datetime | None  # from <issues exportTime="...">


def _child_text(elem: Any, tag: str) -> str | None:
    """Find a direct child by tag, return stripped text (or None)."""
    child = elem.find(tag)
    if child is None or child.text is None:
        return None
    text = child.text.strip()
    return text or None


def _build_target(host_elem: Any, path_text: str | None) -> str:
    """Combine ``<host>`` + ``<path>`` into a single target string.

    Burp's ``<host>`` carries the full URL as text (``https://acme.com``)
    and an ``ip`` attribute. ``<path>`` is the issue-specific path.
    Together they uniquely identify where the issue was observed.
    """
    host_text = (host_elem.text or "").strip() if host_elem is not None else ""
    if path_text and host_text:
        return f"{host_text}{path_text}"
    return host_text or path_text or ""


def _host_addresses(host_elem: Any) -> set[str]:
    """All distinct host strings the scope check should consider."""
    addrs: set[str] = set()
    if host_elem is None:
        return addrs
    host_text = (host_elem.text or "").strip()
    if host_text:
        addrs.add(host_text)
        # Strip scheme so 'https://acme.com' also matches scope 'acme.com'.
        stripped = (
            host_text.replace("https://", "")
            .replace("http://", "")
            .split("/", 1)[0]
        )
        if stripped:
            addrs.add(stripped)
            # Strip :port so 'acme.com:8443' also matches scope 'acme.com'.
            if ":" in stripped:
                addrs.add(stripped.split(":", 1)[0])
    ip = host_elem.attrib.get("ip", "").strip()
    if ip:
        addrs.add(ip)
    return addrs


def _host_in_scope(
    host_elem: Any,
    scope_items: list[ScopeItem],
) -> bool:
    """Evaluate every Burp host representation through one scope policy."""
    addresses = _host_addresses(host_elem)
    return evaluate_scope_candidates(
        [(value, infer_scope_kind(value)) for value in addresses],
        scope_items,
        empty_scope_allowed=True,
    ).allowed


def _parse_export_time(root: Any) -> datetime | None:
    """Best-effort parse of the ``exportTime`` attribute on <issues>.

    Burp writes RFC-2822 style (``Mon, 30 Jun 2026 14:22:01 GMT``).
    Returns None if the attribute is absent or unparseable — we don't
    want a malformed timestamp to abort the whole import.
    """
    raw = root.attrib.get("exportTime", "").strip()
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None


def parse_burp_xml(
    xml_bytes: bytes,
    *,
    include_info: bool = False,
    scope_items: list[ScopeItem] | None = None,
) -> ParseResult:
    """Parse a Burp Pro Issue Export XML payload.

    ``include_info``: when False (default), Burp severity=Information
    issues are skipped. Most analysts don't want a flood of "Strict
    transport security not enforced" infos in the report.

    ``scope_items``: when non-empty, hosts not matching any in-scope
    address (or matching an exclude) are dropped silently and counted on
    the response.

    Raises ``ValueError`` on malformed XML or unexpected root element.
    """
    try:
        root = ElementTree.fromstring(xml_bytes)
    except ElementTree.ParseError as exc:
        raise ValueError(f"invalid Burp XML: {exc}") from exc

    if root.tag != "issues":
        raise ValueError(
            f"expected <issues> root element, got {root.tag!r} "
            "— this importer only accepts Burp Pro Issue Export XML"
        )

    export_time = _parse_export_time(root)
    items: list[ParsedItem] = []
    skipped_info = 0
    skipped_out_of_scope = 0
    total = 0

    for issue in root.findall("issue"):
        total += 1
        severity_raw = (_child_text(issue, "severity") or "").lower()
        severity = _BURP_SEVERITY.get(severity_raw, Severity.info)
        if severity is Severity.info and not include_info:
            skipped_info += 1
            continue

        host_elem = issue.find("host")
        if not _host_in_scope(host_elem, scope_items or []):
            skipped_out_of_scope += 1
            continue

        name = _child_text(issue, "name") or "(unnamed issue)"
        path_text = _child_text(issue, "path")
        target = _build_target(host_elem, path_text)

        details: dict[str, Any] = {
            "type": _child_text(issue, "type"),
            "confidence": _child_text(issue, "confidence"),
            "location": _child_text(issue, "location"),
            "host_ip": host_elem.attrib.get("ip") if host_elem is not None else None,
            "issue_background": _child_text(issue, "issueBackground"),
            "remediation_background": _child_text(issue, "remediationBackground"),
            "issue_detail": _child_text(issue, "issueDetail"),
            "remediation_detail": _child_text(issue, "remediationDetail"),
            "references": _child_text(issue, "references"),
            "vulnerability_classifications": _child_text(
                issue, "vulnerabilityClassifications"
            ),
        }
        # Strip None values so the slide-over doesn't render empty rows.
        details = {k: v for k, v in details.items() if v is not None}

        # v1.4.0: fold every affected URL under the same issue TYPE
        # (Burp assigns numeric type IDs that are stable across scans).
        # Falls back to issue name when type is absent.
        issue_type = _child_text(issue, "type")
        group_key_slug = issue_type or name
        group_key = f"burp:{group_key_slug}" if group_key_slug else None
        items.append(
            ParsedItem(
                title=name,
                severity=severity,
                phase=FindingPhase.vuln_scan,
                summary=_child_text(issue, "issueBackground"),
                target=target,
                source_tool="burp_import",
                details=details,
                observed_at=export_time,
                burp_serial_number=_child_text(issue, "serialNumber"),
                group_key=group_key,
            )
        )

    return ParseResult(
        items=items,
        skipped_info=skipped_info,
        skipped_out_of_scope=skipped_out_of_scope,
        total_items=total,
        export_time=export_time,
    )
