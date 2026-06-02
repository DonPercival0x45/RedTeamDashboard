"""Wire-format models for the approvals endpoints."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models import ApprovalStatus, RiskLevel


class ApprovalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    engagement_id: UUID
    thread_id: str
    node: str | None
    tool_name: str
    tool_args: dict[str, Any]
    risk: RiskLevel
    scope_check: dict[str, Any]
    status: ApprovalStatus
    decided_by: UUID | None
    decision_args: dict[str, Any] | None
    authorization_id: UUID | None
    decided_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ApprovalDecision(BaseModel):
    """Body for POST /approvals/{id}/decision.

    Mirrors the LangGraph resume payload exactly — ``approved`` is required,
    ``edited_args`` lets the analyst tweak the tool args before approval, and
    ``reason`` is a free-form string surfaced back to the agent on denial.
    """

    approved: bool
    edited_args: dict[str, Any] | None = None
    reason: str | None = None
    # When approving, also grant a standing per-(engagement, tool) session
    # authorization so future in-scope calls to this tool auto-run. Ignored on
    # denial.
    remember_for_session: bool = False
