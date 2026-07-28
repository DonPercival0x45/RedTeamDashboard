from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models import Severity
from app.schemas.finding import FindingRead

MAX_FINDING_GROUP_MEMBERS = 200


def _strip_required(value: str, *, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} cannot be blank")
    return normalized


class FindingGroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    rationale: str = Field(min_length=1, max_length=4_000)
    finding_ids: list[UUID] = Field(min_length=2, max_length=MAX_FINDING_GROUP_MEMBERS)
    idempotency_key: UUID

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return _strip_required(value, field_name="name")

    @field_validator("rationale")
    @classmethod
    def normalize_rationale(cls, value: str) -> str:
        return _strip_required(value, field_name="rationale")

    @model_validator(mode="after")
    def finding_ids_are_unique(self) -> FindingGroupCreate:
        if len(set(self.finding_ids)) != len(self.finding_ids):
            raise ValueError("finding_ids must not contain duplicates")
        return self


class FindingGroupUpdate(BaseModel):
    expected_row_version: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=200)
    rationale: str = Field(min_length=1, max_length=4_000)
    finding_ids: list[UUID] = Field(min_length=2, max_length=MAX_FINDING_GROUP_MEMBERS)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return _strip_required(value, field_name="name")

    @field_validator("rationale")
    @classmethod
    def normalize_rationale(cls, value: str) -> str:
        return _strip_required(value, field_name="rationale")

    @model_validator(mode="after")
    def finding_ids_are_unique(self) -> FindingGroupUpdate:
        if len(set(self.finding_ids)) != len(self.finding_ids):
            raise ValueError("finding_ids must not contain duplicates")
        return self


class FindingGroupMemberRead(BaseModel):
    finding_id: UUID
    sort_order: int
    available: bool
    finding: FindingRead


class FindingGroupRollupRead(BaseModel):
    member_count: int
    available_members: int
    unavailable_members: int
    max_severity: Severity
    status_counts: dict[str, int] = Field(default_factory=dict)
    excluded_count: int


class FindingGroupRead(BaseModel):
    id: UUID
    engagement_id: UUID
    name: str
    rationale: str
    created_by_user_id: UUID | None = None
    row_version: int
    created_at: datetime
    updated_at: datetime
    members: list[FindingGroupMemberRead]
    rollup: FindingGroupRollupRead
