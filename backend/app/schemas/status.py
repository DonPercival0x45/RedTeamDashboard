"""Status feed schemas (v0.8.0).

The Status tab on each engagement collapses three storage sources —
AgentExecutions, Tasks, and Approvals — into a single colour-coded
timeline. Each native status enum maps to one of four display colours
(per the v0.8 brief: green=active, blue=pending, red=failed,
purple=completed). The expand view renders ``log`` verbatim as
formatted JSON.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

StatusColor = Literal["active", "pending", "completed", "failed"]
StatusKind = Literal["agent", "task", "approval"]


class StatusTransition(BaseModel):
    """One entry in a box's status timeline.

    ``status`` is the **display** colour the entity reached at ``at``
    (active / pending / completed / failed) — matches the colour the
    tile would have if you saw the entity at that moment. Derived
    server-side from whichever timestamp columns the entity carries
    (started_at + completed_at for agents; created_at + dispatched_at +
    completed_at for tasks; created_at + decided_at for approvals).
    """

    status: StatusColor
    raw_status: str
    at: datetime


class StatusEntity(BaseModel):
    """One box on the Status tab.

    ``log`` is whatever the analyst should see when they hit Expand: for
    agents it's the input + output JSONB and the error message; for
    tasks the payload + run_id + dispatched_at; for approvals the
    tool_name, tool_args, risk, and scope check.

    ``history`` is the entity's status timeline — the Expand modal
    renders it at the top so the analyst can see "this Task went
    pending → dispatched → completed at these timestamps."
    """

    model_config = ConfigDict(from_attributes=False)

    id: UUID
    kind: StatusKind
    title: str
    subtitle: str | None = None
    color: StatusColor
    raw_status: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    retryable: bool = False
    log: dict[str, Any]
    history: list[StatusTransition] = []


class EngagementStatusResponse(BaseModel):
    """Aggregate feed for one engagement. Newest first within each list;
    the frontend interleaves them on a single timeline."""

    agents: list[StatusEntity]
    tasks: list[StatusEntity]
    approvals: list[StatusEntity]
