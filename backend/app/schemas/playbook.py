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
    """

    playbook_slug: str
    playbook_version: int | None = None
    scope_subset: list[str] = Field(default_factory=list)


class PlaybookRunRead(BaseModel):
    """One playbook run — status + counts + timing."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    engagement_id: uuid.UUID
    playbook_id: uuid.UUID
    playbook_slug: str
    playbook_version: int
    status: str
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
