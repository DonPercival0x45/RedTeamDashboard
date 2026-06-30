"""Integration row helpers (v0.9.0 — multi-row, purpose-routed).

Pre-v0.9 this module assumed one row per type and exposed ``get_by_type``
as the only lookup. v0.9 makes rows multi-instance (two Discord webhooks
for two channels, etc.) and adds ``purpose`` as the routing key.

The masked-config merge logic still lives here so the API layer never
accidentally overwrites a real ``bot_token`` with the ``"…1234"`` mask
string the UI displayed.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Integration, IntegrationPurpose
from app.schemas.integration import _MASKED_KEYS

# ── lookups ──────────────────────────────────────────────────────────────


def get_by_id(session: Session, integration_id: uuid.UUID) -> Integration | None:
    return session.get(Integration, integration_id)


def list_all(session: Session) -> list[Integration]:
    return list(
        session.execute(
            select(Integration).order_by(
                Integration.purpose, Integration.type, Integration.created_at
            )
        ).scalars()
    )


def list_by_purpose(
    session: Session,
    purpose: IntegrationPurpose,
    *,
    enabled_only: bool = True,
) -> list[Integration]:
    """All integrations wired to one event class.

    The send-event callers (``status_notifier``, the feedback Discord
    notifier, the GitHub-push endpoint) call this so a multi-Discord
    setup with separate channels for feedback vs status_alerts works
    out of the box.
    """
    stmt = select(Integration).where(Integration.purpose == purpose)
    if enabled_only:
        stmt = stmt.where(Integration.enabled.is_(True))
    return list(session.execute(stmt).scalars())


def first_by_purpose(
    session: Session,
    purpose: IntegrationPurpose,
    *,
    enabled_only: bool = True,
) -> Integration | None:
    """Shorthand for the common case — one row of a given purpose."""
    rows = list_by_purpose(session, purpose, enabled_only=enabled_only)
    return rows[0] if rows else None


# ── back-compat shim ─────────────────────────────────────────────────────


def get_by_type(session: Session, integration_type: str) -> Integration | None:
    """v0.8 callers used a type-keyed lookup. v0.9 multi-row means this
    returns the FIRST row of that type, prefer-enabled. Kept around so
    legacy code paths don't break during the migration; new callers
    should use ``first_by_purpose`` / ``list_by_purpose`` instead.
    """
    rows = list(
        session.execute(
            select(Integration)
            .where(Integration.type == integration_type)
            .order_by(Integration.enabled.desc(), Integration.created_at)
        ).scalars()
    )
    return rows[0] if rows else None


# ── config merge (mask-aware) ────────────────────────────────────────────


def _merge_config(
    existing: dict[str, Any] | None, incoming: dict[str, Any]
) -> dict[str, Any]:
    """Preserve stored values for masked keys when the incoming value
    looks like the mask placeholder we sent down. Anything else replaces
    verbatim."""
    out: dict[str, Any] = dict(existing or {})
    for k, v in incoming.items():
        if k in _MASKED_KEYS and isinstance(v, str) and v.startswith("…"):
            # Caller round-tripped the masked value — keep what we have.
            continue
        out[k] = v
    return out


# ── create / update / delete ─────────────────────────────────────────────


def create(
    session: Session,
    *,
    integration_type: str,
    purpose: IntegrationPurpose,
    name: str,
    display_name: str | None,
    logo_url: str | None,
    enabled: bool,
    config: dict[str, Any],
    actor_user_id: uuid.UUID,
) -> Integration:
    row = Integration(
        type=integration_type,
        purpose=purpose,
        name=name,
        display_name=display_name,
        logo_url=logo_url,
        enabled=enabled,
        config=config,
        created_by_user_id=actor_user_id,
    )
    session.add(row)
    session.flush()
    return row


def update(
    session: Session,
    row: Integration,
    *,
    purpose: IntegrationPurpose | None,
    name: str | None,
    display_name: str | None,
    logo_url: str | None,
    enabled: bool | None,
    config: dict[str, Any] | None,
) -> Integration:
    if purpose is not None:
        row.purpose = purpose
    if name is not None:
        row.name = name
    if display_name is not None:
        row.display_name = display_name
    if logo_url is not None:
        row.logo_url = logo_url
    if enabled is not None:
        row.enabled = enabled
    if config is not None:
        row.config = _merge_config(row.config, config)
    session.flush()
    return row


def delete(session: Session, integration_id: uuid.UUID) -> bool:
    row = get_by_id(session, integration_id)
    if row is None:
        return False
    session.delete(row)
    return True
