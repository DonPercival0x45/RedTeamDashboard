"""Integration row helpers — single upsert path keyed on ``type``.

Single-tenant by design: one row per ``type`` (Discord today; future
Slack/Teams just add enum values). The masked-config merge logic lives
here too, so the API layer never accidentally overwrites a real
``bot_token`` with the ``"…1234"`` mask string the UI displayed.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Integration, IntegrationType
from app.schemas.integration import _MASKED_KEYS


def get_by_type(
    session: Session, integration_type: IntegrationType
) -> Integration | None:
    return session.execute(
        select(Integration).where(Integration.type == integration_type)
    ).scalar_one_or_none()


def _merge_config(
    existing: dict[str, Any] | None, incoming: dict[str, Any]
) -> dict[str, Any]:
    """Preserve stored values for masked keys when the incoming value
    looks like the mask placeholder we sent down. Anything else replaces
    verbatim."""
    out: dict[str, Any] = dict(existing or {})
    for k, v in incoming.items():
        if (
            k in _MASKED_KEYS
            and isinstance(v, str)
            and v.startswith("…")
        ):
            # Caller round-tripped the masked value — keep what we have.
            continue
        out[k] = v
    return out


def upsert(
    session: Session,
    *,
    integration_type: IntegrationType,
    enabled: bool,
    config: dict[str, Any],
    actor_user_id: uuid.UUID,
) -> Integration:
    """Upsert by type. Caller commits."""
    row = get_by_type(session, integration_type)
    if row is None:
        row = Integration(
            type=integration_type,
            enabled=enabled,
            config=config,
            created_by_user_id=actor_user_id,
        )
        session.add(row)
    else:
        row.enabled = enabled
        row.config = _merge_config(row.config, config)
    session.flush()
    return row


def delete(
    session: Session, integration_type: IntegrationType
) -> bool:
    row = get_by_type(session, integration_type)
    if row is None:
        return False
    session.delete(row)
    return True
