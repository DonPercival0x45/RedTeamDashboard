"""v3.0.3 — turn playbook step outputs into Finding rows.

The v3 collection plane was landing playbook results into ``PlaybookRun``
counters (findings_new / findings_total) but never writing them to the
``findings`` table. So the Findings tab stayed empty even after a
successful ptes-passive-recon run reported "35 findings." This module
closes that seam by reusing the existing grouping helper.

Design constraints:

* **Reuse, don't duplicate.** ``finding_grouping.upsert_grouped_finding``
  already handles apex-domain grouping, item dedup, severity lifting,
  and the ON CONFLICT DO UPDATE. This module is a thin adapter that
  translates each playbook tool's output shape into the shape the
  grouping helper already understands.
* **Fail soft.** A bridge failure must not kill the playbook run —
  Kendall's runner already treats step failures as recoverable, and
  finding persistence is downstream of that. We log + swallow.
* **Stubs skipped.** Kendall's stub-coverage fix (migration 0064) means
  stubs return ``ok=True`` but don't satisfy baseline. They also don't
  produce real findings, so we skip them.

Playbook tool names differ from the grouping vocab (historical: grouping
was written when the tool names had ``_lookup`` suffixes). ``TOOL_ALIASES``
maps between them.
"""
from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

import structlog
from sqlalchemy.orm import Session

from app.models import FindingPhase, FindingStatus, Severity
from app.services.finding_grouping import (
    compute_group_key,
    upsert_grouped_finding,
)

logger = structlog.get_logger(__name__)


# Playbook tool slug → grouping vocab tool slug. Grouping's vocab is the
# source of truth for group_key / extract_items / dedup — we translate
# the playbook side so we don't fork the vocab.
TOOL_ALIASES: dict[str, str] = {
    "whois": "whois_lookup",
    "dns_inventory": "dns_lookup",
    "subfinder": "subfinder",
    "crtsh": "crt_sh",
    # breach_lookup has no grouping vocab entry yet — bridge returns None.
}


def _translate(
    playbook_tool: str,
    args: Mapping[str, Any] | None,
    data: Mapping[str, Any] | None,
) -> tuple[str | None, dict[str, Any] | None]:
    """Reshape a playbook tool's data into what grouping's extract_items
    expects. Returns ``(grouping_tool, reshaped_data)`` or ``(None, None)``
    if the tool isn't bridgeable or emitted nothing worth persisting."""
    args = dict(args or {})
    data = dict(data or {})
    domain = str(args.get("domain") or data.get("domain") or "").strip()

    if playbook_tool == "whois":
        record = data.get("record") or {}
        if not record or not domain:
            return None, None
        return "whois_lookup", {"domain": domain, **record}

    if playbook_tool == "dns_inventory":
        # Playbook shape: {"records": {"A": [...], "AAAA": [...], "MX": [...],
        # "TXT": [...], "NS": [...]}}. Grouping shape: lowercase a/aaaa/cname
        # + mx/txt/ns passthrough at the top level.
        records = data.get("records") or {}
        if not isinstance(records, dict):
            return None, None
        # Grouping's dns_lookup vocab keys everything off "domain" + records.
        # If nothing came back at all, don't create an empty finding.
        total_records = sum(
            len(v) if isinstance(v, list) else 0 for v in records.values()
        )
        if total_records == 0 or not domain:
            return None, None
        return "dns_lookup", {
            "domain": domain,
            "a": records.get("A") or [],
            "aaaa": records.get("AAAA") or [],
            "cname": records.get("CNAME") or [],
            "mx": records.get("MX") or [],
            "txt": records.get("TXT") or [],
            "ns": records.get("NS") or [],
        }

    if playbook_tool == "subfinder":
        subs = [s for s in (data.get("subdomains") or []) if isinstance(s, str) and s.strip()]
        if not subs or not domain:
            return None, None
        return "subfinder", {"domain": domain, "subdomains": subs}

    if playbook_tool == "crtsh":
        subs = [s for s in (data.get("subdomains") or []) if isinstance(s, str) and s.strip()]
        if not subs or not domain:
            return None, None
        return "crt_sh", {"domain": domain, "subdomains": subs}

    return None, None


def bridge_step_to_finding(
    session: Session,
    *,
    engagement_id: uuid.UUID,
    playbook_tool: str,
    scope_item: str,
    args_template: Mapping[str, Any] | None,
    data: Mapping[str, Any] | None,
    thread_id: str | None,
    phase: FindingPhase = FindingPhase.osint,
) -> uuid.UUID | None:
    """Upsert a Finding row for one playbook step's output.

    Returns the ``Finding.id`` on success (whether newly-created or an
    existing row that got items merged in), or ``None`` when the tool
    isn't bridgeable / produced nothing / grouping refused to compute a
    group_key.

    Caller must commit; this only flushes via ``upsert_grouped_finding``.
    Exceptions are logged and swallowed — playbook run correctness beats
    finding persistence.
    """
    if not data:
        return None

    # Playbook tools resolve ``{{scope_item}}`` inside args. We don't
    # have the resolved args here, so mint a resolved copy: if the
    # template's "domain" is falsy, use scope_item; grouping only cares
    # about domain for the tools we bridge.
    template = dict(args_template or {})
    if not template.get("domain") and scope_item:
        template["domain"] = scope_item

    grouping_tool, reshaped = _translate(playbook_tool, template, data)
    if grouping_tool is None or reshaped is None:
        return None

    group_key = compute_group_key(grouping_tool, template, reshaped)
    if group_key is None:
        return None

    try:
        row, _added = upsert_grouped_finding(
            session,
            engagement_id=engagement_id,
            group_key=group_key,
            tool=grouping_tool,
            thread_id=thread_id,
            args=template,
            data=reshaped,
            incoming_severity=Severity.info,
            default_title=None,
            phase=phase,
            status=FindingStatus.pending_validation,
        )
    except Exception as exc:  # noqa: BLE001 - bridge failure must not kill the run
        logger.warning(
            "playbook.finding_bridge.upsert_failed",
            playbook_tool=playbook_tool,
            grouping_tool=grouping_tool,
            group_key=group_key,
            error=str(exc),
        )
        return None

    logger.info(
        "playbook.finding_bridge.upserted",
        playbook_tool=playbook_tool,
        grouping_tool=grouping_tool,
        group_key=group_key,
        finding_id=str(row.id),
    )
    return row.id
