"""Wire-format models for engagements, scope items, and run kickoff."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models import EngagementStatus, ScopeKind


class EngagementCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    slug: str | None = Field(
        default=None,
        max_length=200,
        description="Optional. Auto-generated from `name` if omitted.",
    )


class EngagementUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    status: EngagementStatus | None = Field(
        default=None,
        description=(
            "Only `active` or `archived` are accepted via PATCH. Use "
            "POST /engagements/{slug}/flush for irreversible deletion."
        ),
    )


class EngagementRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    slug: str
    status: EngagementStatus
    created_by: UUID | None
    archived_at: datetime | None
    flushed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ScopeItemCreate(BaseModel):
    kind: ScopeKind
    value: str = Field(min_length=1, max_length=500)
    is_exclusion: bool = False
    note: str | None = Field(default=None, max_length=500)


class ScopeItemUpdate(BaseModel):
    value: str | None = Field(default=None, min_length=1, max_length=500)
    is_exclusion: bool | None = None
    note: str | None = Field(default=None, max_length=500)


class ScopeItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    engagement_id: UUID
    kind: ScopeKind
    value: str
    is_exclusion: bool
    note: str | None
    created_at: datetime
    updated_at: datetime


class RunStart(BaseModel):
    prompt: str = Field(min_length=1)


class RunStartResponse(BaseModel):
    engagement_id: UUID
    thread_id: UUID
    events_stream: str
