"""Wire-format schemas for the Tools tab (v0.11.0)."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.models import ToolKind, ToolLane, ToolStatus, ToolTaskKind


class ToolRead(BaseModel):
    """One row in the catalog. ``artifact_ref`` is masked for the
    frontend beyond "yes there is source stored" so the admin has to
    approve to inspect it."""

    id: UUID
    name: str
    description: str | None = None
    kind: ToolKind
    lane: ToolLane
    risk_level: str
    task_kind: ToolTaskKind
    status: ToolStatus
    manifest: dict[str, Any]
    validation: dict[str, Any]
    has_artifact: bool
    version: int
    created_by_user_id: UUID | None = None
    approved_by_user_id: UUID | None = None
    approved_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ToolUploadResponse(BaseModel):
    """Response for POST /tools. Always returns the created row plus a
    validation summary the frontend renders as a checklist.

    The tool lands in status=``draft`` regardless of validation outcome —
    admin explicitly approves via POST /tools/{id}/approve. This means
    a failing validation still creates a row so the admin can see WHY
    something got rejected without a re-upload dance.
    """

    tool: ToolRead
    validation_ok: bool
    validation_errors: list[str] = Field(default_factory=list)


class ToolApproveRequest(BaseModel):
    override_validation: bool = False
    note: str | None = None
