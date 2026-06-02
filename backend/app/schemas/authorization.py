"""Wire-format model for session authorizations (per-tool standing grants)."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class AuthorizationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    engagement_id: UUID
    tool_name: str
    granted_by: UUID | None
    note: str | None
    revoked_at: datetime | None
    revoked_by: UUID | None
    created_at: datetime
    updated_at: datetime
