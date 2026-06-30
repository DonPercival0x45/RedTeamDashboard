"""Wire schemas for the v0.9.0 Integrations surface.

The provider catalog at ``/settings/integrations`` is the 3rd-party-app
hub of the dashboard — a generic place for anything that isn't an API
key (Discord webhooks, Teams Adaptive Cards, GitHub-push, custom
JSON-template webhooks, future Slack/Jira/PagerDuty/…). Each row stores:

- A **type** (free-form VARCHAR slug; the provider module on the frontend
  decides what config fields apply).
- A **purpose** enum routing this row to one event class
  (``feedback`` | ``status_alerts`` | ``roadmap_push`` | ``manual``).
- A **name** (analyst label) and optional **display_name**.
- A **config** JSONB whose shape is provider-defined.
- A **logo_url** used only by Custom integrations the admin uploaded
  their own square image for.

Secret-ish config fields are masked on read — only the last four chars
come back over the wire (``bot_token``, ``pat_token``, ``api_key``).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.integration import IntegrationPurpose

# v0.9.0: add ``api_key`` for custom webhooks that need bearer auth on
# top of webhook_url.
_MASKED_KEYS = {"bot_token", "pat_token", "api_key"}


def mask_config(config: dict[str, Any]) -> dict[str, Any]:
    """Mask any fields in ``_MASKED_KEYS`` — show only the last four chars."""
    out: dict[str, Any] = {}
    for k, v in config.items():
        if k in _MASKED_KEYS and isinstance(v, str) and v:
            out[k] = f"…{v[-4:]}" if len(v) >= 4 else "…"
        else:
            out[k] = v
    return out


class IntegrationCreate(BaseModel):
    """POST /integrations — admin creates a fresh row.

    ``type`` is a free-form slug the provider module on the frontend
    decides ("discord", "teams", "github_push", "custom", future
    "slack" / "jira" / etc.). v0.9 ships built-in support for the four
    slugs above; the backend stores any string.

    Empty string in a masked config field means "leave the stored value
    alone" on a follow-up PATCH — but for create the field is just
    whatever the admin pasted.
    """

    type: str = Field(min_length=1, max_length=60)
    purpose: IntegrationPurpose = IntegrationPurpose.manual
    name: str = Field(min_length=1, max_length=120)
    display_name: str | None = Field(default=None, max_length=120)
    logo_url: str | None = Field(default=None, max_length=500)
    enabled: bool = False
    config: dict[str, Any] = Field(default_factory=dict)


class IntegrationUpdate(BaseModel):
    """PATCH /integrations/{id} — only the provided fields change.

    ``config`` is merged: empty-string values in masked fields preserve
    the stored value (so the admin can re-save without re-pasting the
    secret).
    """

    purpose: IntegrationPurpose | None = None
    name: str | None = Field(default=None, min_length=1, max_length=120)
    display_name: str | None = Field(default=None, max_length=120)
    logo_url: str | None = Field(default=None, max_length=500)
    enabled: bool | None = None
    config: dict[str, Any] | None = None


class IntegrationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    type: str
    purpose: IntegrationPurpose
    name: str
    display_name: str | None = None
    logo_url: str | None = None
    enabled: bool
    config: dict[str, Any]
    created_by_user_id: UUID | None
    created_at: datetime
    updated_at: datetime
