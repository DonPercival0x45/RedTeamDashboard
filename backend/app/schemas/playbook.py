"""API schemas for the playbook runner (A3b)."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PlaybookStepRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    sort_order: int
    tool_slug: str
    args_template: dict
    satisfies_node_ids: list[str]
    description: str | None = None


class PlaybookRead(BaseModel):
    """Catalog entry — list view."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    version: int
    name: str
    description: str | None = None
    applies_to_asset_class: str
    active: bool
    step_count: int = 0


class PlaybookDetail(PlaybookRead):
    """Catalog entry — full steps."""

    steps: list[PlaybookStepRead] = Field(default_factory=list)


class PlaybookRunPayload(BaseModel):
    """Request body for POST /engagements/{slug}/playbook-runs.

    ``playbook_version`` is optional — omit to pin to latest at start.
    ``scope_subset`` = analyst-declared scope_item_ids the run targets.
    ``executor`` picks which executor drives the run (A4). Defaults to
    ``internal`` — the in-process implementation A3b landed. ``mcp``
    dispatches to the MCP server via ``MCPExecutor``.
    """

    playbook_slug: str
    playbook_version: int | None = None
    scope_subset: list[str] = Field(default_factory=list)
    executor: str = "internal"


class PlaybookRunRead(BaseModel):
    """One playbook run — status + counts + timing."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    engagement_id: uuid.UUID
    playbook_id: uuid.UUID
    playbook_slug: str
    playbook_version: int
    status: str
    executor: str = "internal"
    scope_subset: list = Field(default_factory=list)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    steps_total: int = 0
    steps_succeeded: int = 0
    steps_failed: int = 0
    findings_new: int = 0
    findings_unvalidated: int = 0
    findings_high_severity: int = 0
    findings_total: int = 0
    last_error: str | None = None
    # Request identity is durable even though execution happens in a worker.
    requested_by: uuid.UUID | None = None
    # A5 approval attribution — populated when the run passed through the
    # awaiting_approval gate.
    approved_by: uuid.UUID | None = None
    approved_at: datetime | None = None
    approval_reason: str | None = None
    rejected_by: uuid.UUID | None = None
    rejected_at: datetime | None = None
    rejection_reason: str | None = None


class PlaybookApprovalPayload(BaseModel):
    """Request body for approve/reject endpoints.

    ``reason`` is optional on approve (audit context), required on reject
    (analyst needs to tell the requestor why).
    """

    reason: str | None = None


class PlaybookCreatePayload(BaseModel):
    """Request body for POST /playbooks — analyst-authored catalog entry."""

    slug: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=200)
    applies_to_asset_class: str = Field(min_length=1, max_length=80)
    description: str | None = None
    active: bool = False


class PlaybookPatchPayload(BaseModel):
    """Request body for PATCH /playbooks/{slug}. All fields optional so
    partial updates carry only what changed."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    applies_to_asset_class: str | None = Field(default=None, min_length=1, max_length=80)
    active: bool | None = None


class PlaybookStepCreatePayload(BaseModel):
    """Request body for POST /playbooks/{slug}/steps."""

    tool_slug: str = Field(min_length=1, max_length=120)
    args_template: dict = Field(default_factory=dict)
    satisfies_node_ids: list[str] = Field(default_factory=list)
    sort_order: int | None = None
    description: str | None = None


class PlaybookStepPatchPayload(BaseModel):
    """Request body for PATCH /playbooks/{slug}/steps/{step_id}."""

    tool_slug: str | None = Field(default=None, min_length=1, max_length=120)
    args_template: dict | None = None
    satisfies_node_ids: list[str] | None = None
    sort_order: int | None = None
    description: str | None = None
