"""API schemas for the methodology catalog (A1)."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class MethodologyNodeRead(BaseModel):
    """One node of a methodology's coverage tree."""

    model_config = ConfigDict(from_attributes=True)

    node_id: str
    parent_node_id: str | None = None
    title: str
    description: str | None = None
    tier: str
    asset_class: str
    ttl_days: int | None = None
    sort_order: int = 0


class MethodologyRead(BaseModel):
    """Catalog list entry — no full node tree, just metadata + counts."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    version: int
    name: str
    description: str | None = None
    source_url: str | None = None
    node_count: int = 0


class MethodologyDetail(BaseModel):
    """Catalog detail — full node tree."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    version: int
    name: str
    description: str | None = None
    source_url: str | None = None
    nodes: list[MethodologyNodeRead] = Field(default_factory=list)


class MethodologySelectPayload(BaseModel):
    """Request body for POST /engagements/{slug}/methodology.

    ``version`` is optional — omit to pin the engagement to the newest catalog
    version at selection time (still frozen into the snapshot immediately).
    """

    slug: str
    version: int | None = None


class EngagementMethodologyRead(BaseModel):
    """Frozen methodology as it lives on an engagement post-selection."""

    methodology_id: uuid.UUID | None = None
    slug: str | None = None
    version: int | None = None
    selected_at: datetime | None = None
    snapshot: dict | None = None
