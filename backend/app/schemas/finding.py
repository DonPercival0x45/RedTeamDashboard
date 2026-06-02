"""Wire-format model for persisted findings.

The shape mirrors the SSE ``finding.created`` event (``tool``/``args``/``data``)
so the frontend can hydrate the findings table from the DB on load and append
live events without two code paths. The worker stores findings with
``details = {thread_id, args, **tool_data}``; the API unpacks that back out (see
``_finding_to_read`` in ``app.api.engagements``).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.models import Severity


class FindingRead(BaseModel):
    id: UUID
    thread_id: str | None = None
    tool: str | None = None
    target: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    data: dict[str, Any] = Field(default_factory=dict)
    severity: Severity
    title: str
    created_at: datetime
