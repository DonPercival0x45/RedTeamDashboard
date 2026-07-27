from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.finding_followup import (
    FindingRemediationStatus,
    FindingRetestOutcome,
)

MAX_FOLLOWUP_NOTE_CHARS = 10_000


def _normalize_note(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


class FindingRemediationUpdateCreate(BaseModel):
    status: FindingRemediationStatus
    note: str | None = Field(default=None, max_length=MAX_FOLLOWUP_NOTE_CHARS)
    reported_at: datetime | None = None

    @field_validator("note")
    @classmethod
    def normalize_note(cls, value: str | None) -> str | None:
        return _normalize_note(value)


class FindingRemediationUpdateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    finding_id: UUID
    status: FindingRemediationStatus
    note: str | None
    reported_at: datetime
    recorded_by_user_id: UUID | None
    created_at: datetime


class FindingRetestCreate(BaseModel):
    outcome: FindingRetestOutcome
    note: str | None = Field(default=None, max_length=MAX_FOLLOWUP_NOTE_CHARS)
    tested_at: datetime | None = None

    @field_validator("note")
    @classmethod
    def normalize_note(cls, value: str | None) -> str | None:
        return _normalize_note(value)


class FindingRetestRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    finding_id: UUID
    outcome: FindingRetestOutcome
    note: str | None
    tested_at: datetime
    performed_by_user_id: UUID | None
    created_at: datetime


class FindingFollowUpRead(BaseModel):
    latest_remediation: FindingRemediationUpdateRead | None
    latest_retest: FindingRetestRead | None
    remediation_updates: list[FindingRemediationUpdateRead]
    retests: list[FindingRetestRead]
