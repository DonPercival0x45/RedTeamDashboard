"""Canonical bridge from v3 tool output to engagement Findings.

Both full playbook runs and one-shot v3 tool actions use this adapter. It
normalizes playbook tool slugs/output into the existing finding-grouping
vocabulary so every ingestion path shares group keys and item deduplication.
It also records run lineage and stages (but does not publish/commit) finding
feedback in the caller's transaction.
"""
from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import Finding, FindingPhase, Severity
from app.models.finding import default_status_for_phase, record_finding_origins
from app.services.finding_feedback import stage_finding_feedback
from app.services.finding_grouping import compute_group_key, upsert_grouped_finding
from app.services.playbook.executor import substitute_scope

logger = structlog.get_logger(__name__)

# Accept both the Python module spelling used by direct-tool actions and the
# hyphenated catalog spelling used by seeded PlaybookStep rows.
TOOL_ALIASES: dict[str, str] = {
    "whois": "whois_lookup",
    "dns_inventory": "dns_lookup",
    "dns-inventory": "dns_lookup",
    "subfinder": "subfinder",
    "crtsh": "crt_sh",
}

_MAX_ITEMS_PER_STEP = 5000


@dataclass(frozen=True)
class FindingBridgeResult:
    finding_id: uuid.UUID
    items_added: int
    items_total: int
    created: bool


def _bounded_strings(values: Any, remaining: int) -> list[str]:
    if remaining <= 0 or not isinstance(values, list):
        return []
    return [
        value.strip()
        for value in values
        if isinstance(value, str) and value.strip()
    ][:_MAX_ITEMS_PER_STEP if remaining > _MAX_ITEMS_PER_STEP else remaining]


def _translate(
    playbook_tool: str,
    args: Mapping[str, Any],
    data: Mapping[str, Any] | None,
) -> tuple[str | None, dict[str, Any] | None]:
    """Return canonical grouping tool/data, or ``(None, None)`` for no output."""
    data = dict(data or {})
    domain = str(args.get("domain") or data.get("domain") or "").strip()
    normalized = playbook_tool.replace("-", "_")

    if normalized == "whois":
        record = data.get("record") or {}
        if not isinstance(record, dict) or not record or not domain:
            return None, None
        return "whois_lookup", {"domain": domain, **record}

    if normalized == "dns_inventory":
        records = data.get("records") or {}
        if not isinstance(records, dict) or not domain:
            return None, None
        remaining = _MAX_ITEMS_PER_STEP
        projected: dict[str, list[str]] = {}
        for source_key, target_key in (
            ("A", "a"),
            ("AAAA", "aaaa"),
            ("CNAME", "cname"),
            ("MX", "mx"),
            ("TXT", "txt"),
            ("NS", "ns"),
        ):
            values = _bounded_strings(records.get(source_key), remaining)
            projected[target_key] = values
            remaining -= len(values)
        if not any(projected.values()):
            return None, None
        return "dns_lookup", {"domain": domain, **projected}

    if normalized in {"subfinder", "crtsh"}:
        subdomains = _bounded_strings(data.get("subdomains"), _MAX_ITEMS_PER_STEP)
        if not subdomains or not domain:
            return None, None
        grouping_tool = "subfinder" if normalized == "subfinder" else "crt_sh"
        return grouping_tool, {"domain": domain, "subdomains": subdomains}

    return None, None


def bridge_step_to_finding(
    session: Session,
    *,
    engagement_id: uuid.UUID,
    playbook_tool: str,
    scope_item: str,
    args_template: Mapping[str, Any] | None,
    data: Mapping[str, Any] | None,
    thread_id: uuid.UUID | str | None,
    acting_user_id: uuid.UUID | None = None,
    operation_id: uuid.UUID | str | None = None,
    source: str = "playbook",
    phase: FindingPhase = FindingPhase.osint,
) -> FindingBridgeResult | None:
    """Persist one successful real tool result through canonical grouping.

    The caller owns commit/publish. Returning ``None`` means the tool is not
    bridgeable, emitted nothing, or persistence failed. No exception from this
    best-effort downstream seam is allowed to fail the collection step.
    """
    if not data:
        return None

    # Resolve the actual target rather than grouping on the literal
    # ``{{scope_item}}`` template. These bridgeable tools are domain-shaped;
    # scope_item is authoritative even if a caller supplied a conflicting arg.
    resolved_args = substitute_scope(args_template or {}, scope_item)
    resolved_args["domain"] = scope_item
    grouping_tool, reshaped = _translate(playbook_tool, resolved_args, data)
    if grouping_tool is None or reshaped is None:
        return None

    group_key = compute_group_key(grouping_tool, resolved_args, reshaped)
    if group_key is None:
        return None

    lineage_thread: uuid.UUID | None = None
    if isinstance(thread_id, uuid.UUID):
        lineage_thread = thread_id
    elif thread_id is not None:
        try:
            lineage_thread = uuid.UUID(str(thread_id))
        except ValueError:
            logger.warning(
                "playbook.finding_bridge.invalid_thread_id",
                thread_id=str(thread_id),
            )

    try:
        # A SAVEPOINT makes fail-soft real: a constraint/database failure rolls
        # back only bridge work instead of poisoning the playbook transaction.
        with session.begin_nested():
            # Serialize first writes for one canonical group. Without this,
            # concurrent direct/playbook calls can both observe no row and the
            # unique-index loser silently drops otherwise distinct output.
            session.execute(
                text(
                    "SELECT pg_advisory_xact_lock("
                    "hashtextextended(:lock_key, 0))"
                ),
                {"lock_key": f"{engagement_id}:{group_key}"},
            )
            existing_id = session.execute(
                select(Finding.id).where(
                    Finding.engagement_id == engagement_id,
                    Finding.group_key == group_key,
                    Finding.deleted_at.is_(None),
                )
            ).scalar_one_or_none()
            status = default_status_for_phase(phase)
            row, added = upsert_grouped_finding(
                session,
                engagement_id=engagement_id,
                group_key=group_key,
                tool=grouping_tool,
                thread_id=str(lineage_thread) if lineage_thread is not None else None,
                args=resolved_args,
                data=reshaped,
                incoming_severity=Severity.info,
                default_title=None,
                phase=phase,
                status=status,
            )
            session.flush()

            if lineage_thread is not None:
                record_finding_origins(
                    session,
                    finding_ids=[row.id],
                    thread_id=lineage_thread,
                    source_tool=playbook_tool,
                )

            created = existing_id is None
            if (
                acting_user_id is not None
                and operation_id is not None
                and (created or added > 0)
            ):
                stage_finding_feedback(
                    session,
                    finding=row,
                    acting_user_id=acting_user_id,
                    operation_id=operation_id,
                    source=source,
                    event_type="finding.created" if created else "finding.updated",
                    thread_id=lineage_thread,
                    tool=grouping_tool,
                    args=resolved_args,
                    data={"chunk_finding_count": added},
                )

            total = len((row.details or {}).get("items") or [])
    except Exception as exc:  # noqa: BLE001 - downstream persistence is fail-soft
        logger.exception(
            "playbook.finding_bridge.failed",
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
        added=added,
        total=total,
    )
    return FindingBridgeResult(
        finding_id=row.id,
        items_added=added,
        items_total=total,
        created=created,
    )
