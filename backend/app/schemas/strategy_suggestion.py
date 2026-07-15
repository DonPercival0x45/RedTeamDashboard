"""Work-item to execution-suggestion contracts."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from app.schemas.orchestrator import SuggestionRead


class ExecutionSuggestionCreate(BaseModel):
    tool: str = Field(min_length=1, max_length=120)
    target: str = Field(min_length=1, max_length=500)
    task_kind: str
    title: str = Field(min_length=1, max_length=300)
    expected_work_item_version: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=100)
    finding_id: uuid.UUID | None = None


class ExecutionSuggestionResponse(BaseModel):
    suggestion: SuggestionRead
    scope_reason: str
