"""Breach exposure lookup backed by imported DeHashed evidence.

Analysts can import DeHashed JSON/CSV exports into the engagement Entity store.
This playbook tool queries those durable records for an exact mailbox or domain,
so exposure triage is useful without transmitting credentials or evidence to a
new third party. A live DeHashed connector can feed the same normalized record
shape later.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Entity
from app.services.playbook.executor import StepResult
from app.services.scope_matcher import normalize_domain, normalize_email

_MAX_RECORDS = 500


def run(scope_context: str, args: dict[str, Any]) -> StepResult:
    """Fallback used outside a worker-bound database session."""
    email = args.get("email")
    domain = None if email else (args.get("domain") or scope_context)
    return StepResult(
        ok=True,
        stub=True,
        data={
            "note": "DeHashed stub fallback requires an engagement-bound worker session",
            "email": email,
            "domain": domain,
        },
    )


def run_from_store(
    session: Session,
    *,
    engagement_id: uuid.UUID,
    scope_context: str,
    args: dict[str, Any],
) -> StepResult:
    """Match imported DeHashed records against the authorized target."""
    target_email = normalize_email(str(args.get("email") or ""))
    target_domain = (
        None
        if target_email is not None
        else normalize_domain(str(args.get("domain") or scope_context))
    )
    rows = list(
        session.execute(
            select(Entity)
            .where(
                Entity.engagement_id == engagement_id,
                Entity.type == "breach_record",
                Entity.suppressed_at.is_(None),
            )
            .order_by(Entity.created_at.desc())
            .limit(_MAX_RECORDS * 4)
        ).scalars()
    )
    records: list[dict[str, Any]] = []
    for row in rows:
        properties = dict(row.properties or {})
        record_email = normalize_email(str(properties.get("email") or ""))
        if target_email is not None:
            matched = record_email == target_email
        else:
            matched = bool(
                record_email
                and target_domain
                and normalize_domain(record_email.rsplit("@", 1)[1]) == target_domain
            )
        if not matched:
            continue
        records.append(
            {
                **properties,
                "entity_id": str(row.id),
                "source_attribution": row.source_attribution,
            }
        )
        if len(records) >= _MAX_RECORDS:
            break

    return StepResult(
        ok=True,
        findings_total=len(records),
        data={
            "provider": "dehashed_import",
            "email": target_email,
            "domain": target_domain,
            "records": records,
            "truncated": len(records) >= _MAX_RECORDS,
        },
    )
