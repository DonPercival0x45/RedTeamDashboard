"""Wire schemas for the integrations surface (Discord, …).

Secret-ish config fields (``bot_token``) are masked on read — only the
last four chars come back over the wire so the admin UI can confirm the
right token is configured without exposing it. ``webhook_url`` is
treated as semi-public (it's already a credential by URL, but
re-displaying it lets the admin verify the channel target).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.integration import IntegrationType

_MASKED_KEYS = {"bot_token"}


def mask_config(config: dict[str, Any]) -> dict[str, Any]:
    """Mask any fields in ``_MASKED_KEYS`` — show only the last four chars."""
    out: dict[str, Any] = {}
    for k, v in config.items():
        if k in _MASKED_KEYS and isinstance(v, str) and v:
            out[k] = f"…{v[-4:]}" if len(v) >= 4 else "…"
        else:
            out[k] = v
    return out


class IntegrationUpsert(BaseModel):
    """Create-or-update payload. ``type`` keys a unique row, so PUT
    semantics fit better than separate POST/PATCH. Empty string in a
    masked field means "leave the stored value alone" (don't overwrite
    with the mask string)."""

    type: IntegrationType
    enabled: bool = False
    config: dict[str, Any] = Field(default_factory=dict)


class IntegrationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    type: IntegrationType
    enabled: bool
    config: dict[str, Any]
    created_by_user_id: UUID | None
    created_at: datetime
    updated_at: datetime
