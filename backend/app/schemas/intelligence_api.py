"""HTTP contracts for per-engagement v3 activation and manual intelligence."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.models import (
    AgentExecutionStatus,
    AgentPromptMode,
    EngagementArchitecture,
    EngagementPhase,
)


class IntelligenceConversionRequest(BaseModel):
    methodology_slug: str = Field(min_length=1, max_length=120)
    methodology_version: int | None = Field(default=None, ge=1)
    reason: str = Field(min_length=1, max_length=2000)


class IntelligenceConversionResponse(BaseModel):
    engagement_id: UUID
    intelligence_architecture: EngagementArchitecture
    converted_to_v3_at: datetime | None
    methodology_id: UUID | None
    phase: EngagementPhase
    seeded_memory_element_ids: list[UUID] = Field(default_factory=list)
    already_converted: bool = False


class IntelligenceRunRequest(BaseModel):
    mode: AgentPromptMode


class IntelligenceRunResponse(BaseModel):
    execution_id: UUID
    mode: AgentPromptMode
    status: AgentExecutionStatus
    parsed: dict[str, Any] | None = None
    error: str | None = None
