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
# v1.2.0: sub-outcome nuance under the four colours. Only set on
# terminal (color in {completed, failed}) entities — running/pending
# rows leave it None. Rules:
#   - success:  completed, produced usable output (findings > 0 OR
#               kind-specific "non-empty" signal)
#   - empty:    completed, produced no output but no error (e.g. an
#               OSINT run with no subdomains — legitimate zero result)
#   - partial:  completed, some sub-step errored but at least one
#               tool produced output (Tactical mixed results)
#   - errored:  status == failed OR error field set
StatusOutcome = Literal["success", "empty", "partial", "errored"]


class StepEntry(BaseModel):
    """One line in the per-run step log rendered inside the Expand
    modal. Derived server-side from audit_log rows (for agent + approval
    entities) and the Redis outbound stream (for task entities). Newest
    last so the client can render top-down."""

    at: datetime
    kind: str  # e.g. "tool.call", "run.started", "approval.pending"
    label: str  # short human-readable line
    detail: dict[str, Any] | None = None


class StepLogResponse(BaseModel):
    steps: list[StepEntry]
    truncated: bool = False


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
    # Typed lineage for navigation. Consumers must not parse generic log JSON.
    finding_id: UUID | None = None
    work_item_id: UUID | None = None
    task_id: UUID | None = None
    log: dict[str, Any]
    history: list[StatusTransition] = []
    # v1.2.0: display-only short slug for cross-portal run tracking.
    # Format ``rt-<4 hex>`` — first four hex chars of the entity UUID.
    # The URL (deep link) uses the full ``id`` — the slug is a human-
    # readable handle for toasts and copy/paste.
    run_slug: str
    # v1.2.0: sub-outcome badge on the card. None for still-running /
    # pending rows; one of success/empty/partial/errored for terminal.
    outcome: StatusOutcome | None = None
    # v1.2.0: one-line plain-language summary — replaces "here's what
    # I tried to do, here's what happened, or here's why I failed".
    # Templated server-side from prompt/status/error/counts; falls back
    # to ``subtitle`` when no signal is available.
    synopsis: str | None = None


class EngagementStatusResponse(BaseModel):
    """Aggregate feed for one engagement. Newest first within each list;
    the frontend interleaves them on a single timeline."""

    agents: list[StatusEntity]
    tasks: list[StatusEntity]
    approvals: list[StatusEntity]
