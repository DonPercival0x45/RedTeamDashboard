"""Report readiness preflight wire models."""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class ReadinessCheck(BaseModel):
    key: str
    level: Literal["blocker", "warning", "info"]
    count: int
    message: str
    finding_ids: list[UUID] = Field(default_factory=list)
    target_view: str | None = None


class ReportReadiness(BaseModel):
    ready: bool
    generated_at: datetime
    reportable_count: int
    total_findings: int
    checks: list[ReadinessCheck]
