"""Canonical bridge from v3 tool output to engagement Findings.

Both full playbook runs and one-shot v3 tool actions use this adapter. It
normalizes playbook tool slugs/output into the existing finding-grouping
vocabulary so every ingestion path shares group keys and item deduplication.
It also records run lineage and stages (but does not publish/commit) finding
feedback in the caller's transaction.
"""
from __future__ import annotations

import copy
import ipaddress
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import Finding, FindingPhase, Severity
from app.models.finding import default_status_for_phase, record_finding_origins
from app.services.finding_feedback import stage_finding_feedback
from app.services.finding_grouping import compute_group_key, upsert_grouped_finding
from app.services.playbook.executor import resolve_step_args

logger = structlog.get_logger(__name__)

# Accept both the Python module spelling used by direct-tool actions and the
# hyphenated catalog spelling used by seeded PlaybookStep rows.
TOOL_ALIASES: dict[str, str] = {
    "whois": "whois_lookup",
    "dns_inventory": "dns_lookup",
    "dns-inventory": "dns_lookup",
    "subfinder": "subfinder",
    "crtsh": "crt_sh",
    "freeipapi": "freeipapi",
    "ipinfo": "ipinfo",
}

_MAX_ITEMS_PER_STEP = 5000


class FindingBridgePersistenceError(RuntimeError):
    """Canonical persistence failed after a tool returned usable output."""


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
        return "whois_lookup", {**record, "domain": domain}

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
            if source_key in {"A", "AAAA"}:
                normalized_values: list[str] = []
                for value in values:
                    try:
                        normalized_values.append(
                            ipaddress.ip_address(value).compressed
                        )
                    except ValueError:
                        normalized_values.append(value)
                values = normalized_values
            elif source_key in {"CNAME", "NS"}:
                values = [value.lower().rstrip(".") for value in values]
            elif source_key == "MX":
                values = [
                    " ".join(
                        [*value.split()[:-1], value.split()[-1].lower().rstrip(".")]
                    )
                    if value.split()
                    else value
                    for value in values
                ]
            projected[target_key] = values
            remaining -= len(values)
        if not any(projected.values()):
            return None, None
        return "dns_lookup", {**projected, "domain": domain}

    if normalized in {"subfinder", "crtsh"}:
        subdomains = _bounded_strings(data.get("subdomains"), _MAX_ITEMS_PER_STEP)
        if not subdomains or not domain:
            return None, None
        grouping_tool = "subfinder" if normalized == "subfinder" else "crt_sh"
        return grouping_tool, {
            "subdomains": [value.lower().rstrip(".") for value in subdomains],
            "domain": domain,
        }

    if normalized in {"freeipapi", "ipinfo"}:
        ip = str(args.get("ip") or data.get("ip") or "").strip()
        if not ip or not data:
            return None, None
        return normalized, {**data, "ip": ip}

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
    resolved_args = resolve_step_args(
        playbook_tool, args_template or {}, scope_item
    )
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

            # Older playbook persistence grouped on the literal template even
            # though the executor had already queried the real target. Such a
            # row cannot be trusted (multiple targets may have overwritten one
            # item), so the next successful resolved run retires it and writes
            # a fresh canonical group instead of leaving contradictory rows.
            placeholder_args = dict(resolved_args)
            placeholder_args["domain"] = "{{scope_item}}"
            placeholder_data = dict(reshaped)
            placeholder_data["domain"] = "{{scope_item}}"
            placeholder_group_key = compute_group_key(
                grouping_tool, placeholder_args, placeholder_data
            )
            if placeholder_group_key and placeholder_group_key != group_key:
                legacy_rows = list(
                    session.execute(
                        select(Finding)
                        .where(
                            Finding.engagement_id == engagement_id,
                            Finding.group_key == placeholder_group_key,
                            Finding.deleted_at.is_(None),
                        )
                        .with_for_update()
                    ).scalars()
                )
                for legacy in legacy_rows:
                    legacy.deleted_at = datetime.now(tz=UTC)
                    legacy.details = {
                        **(legacy.details or {}),
                        "retired_reason": "literal playbook scope template",
                        "replaced_by_group_key": group_key,
                    }
                if legacy_rows:
                    session.flush()
                    logger.warning(
                        "playbook.finding_bridge.retired_literal_group",
                        engagement_id=str(engagement_id),
                        legacy_group_key=placeholder_group_key,
                        replacement_group_key=group_key,
                        retired=len(legacy_rows),
                    )

            existing_row = session.execute(
                select(Finding).where(
                    Finding.engagement_id == engagement_id,
                    Finding.group_key == group_key,
                    Finding.deleted_at.is_(None),
                )
            ).scalar_one_or_none()
            existing_id = existing_row.id if existing_row is not None else None
            previous_details = (
                copy.deepcopy(existing_row.details)
                if existing_row is not None
                else None
            )
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
            enriched = (
                existing_row is not None
                and previous_details != row.details
            )
            if (
                acting_user_id is not None
                and operation_id is not None
                and (created or added > 0 or enriched)
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
                    data={
                        "chunk_finding_count": added,
                        "enriched_existing_item": enriched,
                    },
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
        raise FindingBridgePersistenceError(
            f"could not persist canonical {grouping_tool} finding"
        ) from exc

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
