"""Wire-format schemas for tool invocations (v0.12.0)."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.models import ToolInvocationStatus


class ToolInvokeRequest(BaseModel):
    """Body for POST /engagements/{slug}/tool-invocations.

    ``args`` is validated against the tool's manifest ``spec.args``
    server-side. ``allow_network`` is a per-invocation escape hatch
    the admin can flip when a tool that declares ``network_egress:
    [none]`` still needs egress for a one-off — captured in the
    invocation row's ``args`` payload for audit.
    """

    tool_id: UUID
    args: dict[str, Any] = Field(default_factory=dict)


class ToolInvocationRead(BaseModel):
    id: UUID
    tool_id: UUID
    tool_version: int
    tool_name: str | None = None  # denormalized on read for the list view
    engagement_id: UUID
    invoker_user_id: UUID | None = None
    args: dict[str, Any]
    runtime_ref: str | None = None
    status: ToolInvocationStatus
    exit_code: int | None = None
    stdout: str | None = None
    stderr: str | None = None
    error: str | None = None
    started_at: datetime
    completed_at: datetime | None = None
