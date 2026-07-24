"""Persist playbook step output into the engagement Findings table.

Operator complaint 4a: a playbook run used to show "N findings" in its detail
modal while the engagement's Findings tab stayed empty — the internal DNS /
WHOIS tools counted answers as findings but never persisted a ``Finding`` row,
and ``engagement_rollup``'s post-run analysis (which gathers by
``FindingOrigin.thread_id == run.id``) found nothing to analyze.

This module closes that loop with a **playbook-specific** persistence path that
deliberately does NOT touch the legacy grouping engine
(``services.finding_grouping``), which is keyed to legacy tool slugs
(``subfinder`` / ``crt_sh`` / ``dns_lookup``) and is too battle-tested to
extend safely here. Instead we:

* group by ``playbook:{tool_slug}:{scope_item}`` — one ``Finding`` per
  (tool, target), with the tool's per-item records folded into
  ``details['items']`` and deduped across re-runs;
* stamp ``FindingOrigin.thread_id = run.id`` so the post-run
  gather-then-analyze milestone sees exactly this run's findings;
* stage the finding feedback so the auto-assess watcher reacts;
* return created/updated counts so the run's ``findings_*`` counters describe
  *persisted* outcomes, not raw tool answers.

Auto-validation mirrors the worker path: OSINT/recon phases land as
``validated`` (factual recon — a DNS record either exists or it doesn't).
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Finding, FindingPhase, FindingStatus, Severity
from app.models.finding import record_finding_origins
from app.services.finding_feedback import publish_feedback_entries, stage_finding_feedback

logger = structlog.get_logger(__name__)

# Playbook tool slugs are OSINT/recon — factual, auto-validated like the worker.
_PHASE = FindingPhase.osint

# How each tool's ``data`` blob projects into per-item records. Each projector
# returns ``[{"kind": str, "label": str, "data": {...}}, ...]``; ``label`` is
# the dedup identity inside the (tool, target) group.


def _dns_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    """dns-inventory: flatten the records dict {rtype: [answers]} into items."""
    records = data.get("records")
    if not isinstance(records, dict):
        return []
    items: list[dict[str, Any]] = []
    for rtype, answers in records.items():
        if not isinstance(answers, list):
            continue
        for answer in answers:
            if not isinstance(answer, str) or not answer.strip():
                continue
            items.append(
                {
                    "kind": "dns_record",
                    "label": f"{rtype}={answer.strip()}",
                    "data": {"type": rtype, "value": answer.strip()},
                }
            )
    return items


def _whois_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    """whois: one item per populated registration field."""
    record = data.get("record")
    if not isinstance(record, dict):
        return []
    items: list[dict[str, Any]] = []
    for key, value in record.items():
        if value in (None, "", []):
            continue
        items.append(
            {
                "kind": "whois_field",
                "label": str(key),
                "data": {"field": key, "value": value},
            }
        )
    return items


def _subdomain_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    """subfinder / crtsh real impls: one item per discovered subdomain."""
    subs = data.get("subdomains")
    if not isinstance(subs, list):
        return []
    return [
        {"kind": "subdomain", "label": s.strip().lower(), "data": {"subdomain": s.strip()}}
        for s in subs
        if isinstance(s, str) and s.strip()
    ]


_PROJECTORS = {
    "dns-inventory": _dns_items,
    "whois": _whois_items,
    "subfinder": _subdomain_items,
    "crtsh": _subdomain_items,
}

# A conservative cap so a pathological tool can't blow up a single run's
# item count (mirrors the import caps elsewhere).
_MAX_ITEMS_PER_STEP = 5000


def _group_key(tool_slug: str, scope_item: str) -> str:
    return f"playbook:{tool_slug}:{scope_item.strip().lower()}"


def _group_title(tool_slug: str, scope_item: str, count: int) -> str:
    tool_label = tool_slug.replace("-", " ")
    return f"{tool_label}: {scope_item} ({count} item{'s' if count != 1 else ''})"


def persist_step_findings(
    session: Session,
    *,
    engagement_id: uuid.UUID,
    run_id: uuid.UUID,
    tool_slug: str,
    scope_item: str,
    args: dict[str, Any],
    data: dict[str, Any],
    acting_user_id: uuid.UUID | None,
) -> dict[str, int]:
    """Fold one successful step's output into a canonical Finding.

    Idempotent across re-runs: items dedup against the existing group row.
    Returns ``{"new": <items newly added>, "total": <total items in group>,
    "created": <1 if a new Finding row was created else 0>}`` so the runner can
    accumulate persisted-outcome counters. No-op for stub steps or tools with
    no projector (they produce zero candidate findings by design).
    """
    projector = _PROJECTORS.get(tool_slug)
    if projector is None:
        return {"new": 0, "total": 0, "created": 0}
    items = projector(data or {})[:_MAX_ITEMS_PER_STEP]
    if not items:
        return {"new": 0, "total": 0, "created": 0}

    group_key = _group_key(tool_slug, scope_item)
    now = datetime.now(tz=UTC).isoformat()

    row = session.execute(
        select(Finding).where(
            Finding.engagement_id == engagement_id,
            Finding.group_key == group_key,
            Finding.deleted_at.is_(None),
        )
    ).scalar_one_or_none()

    created = False
    if row is None:
        row = Finding(
            engagement_id=engagement_id,
            title=_group_title(tool_slug, scope_item, 0),
            severity=Severity.info,
            summary=None,
            details={
                "group_key": group_key,
                "source_tool": tool_slug,
                "first_seen_at": now,
                "last_seen_at": now,
                "items": [],
            },
            source_tool=tool_slug,
            target=scope_item,
            phase=_PHASE,
            status=default_status(),
            validated_at=(datetime.now(tz=UTC)),
            group_key=group_key,
        )
        session.add(row)
        session.flush()
        created = True

    existing_items = (row.details or {}).get("items") or []
    seen = {
        str(item.get("label", "")).strip().lower()
        for item in existing_items
        if isinstance(item, dict)
    }
    new_items = [
        {"label": i["label"], "kind": i["kind"], "data": i["data"], "first_seen_at": now}
        for i in items
        if i["label"].strip().lower() not in seen
    ]

    if new_items:
        details = dict(row.details or {})
        details["items"] = existing_items + new_items
        details["last_seen_at"] = now
        row.details = details
        row.title = _group_title(tool_slug, scope_item, len(details["items"]))
        # Flag the JSONB column as mutated so SQLAlchemy emits the UPDATE.
        from sqlalchemy.orm.attributes import flag_modified

        flag_modified(row, "details")
        flag_modified(row, "title")

    total = len(existing_items) + len(new_items)

    record_finding_origins(
        session,
        finding_ids=[row.id],
        thread_id=run_id,
        source_tool=tool_slug,
    )

    # Stage feedback so the auto-assess watcher reacts to the canonical row.
    # event_type mirrors the worker: created → finding.created, else updated.
    if acting_user_id is not None and (created or new_items):
        entry = stage_finding_feedback(
            session,
            finding=row,
            acting_user_id=acting_user_id,
            operation_id=f"playbook-run:{run_id}",
            source="playbook",
            event_type="finding.created" if created else "finding.updated",
            thread_id=run_id,
            tool=tool_slug,
            args=args,
            data={"chunk_finding_count": len(new_items)},
        )
        publish_feedback_entries(session, None, [entry])

    return {"new": len(new_items), "total": total, "created": 1 if created else 0}


def default_status() -> FindingStatus:
    """OSINT/recon findings auto-validate (factual), matching the worker."""
    from app.models.finding import default_status_for_phase

    return default_status_for_phase(_PHASE)
