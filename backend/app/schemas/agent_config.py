"""Schemas for Settings > Configurations — per-analyst per-engagement
routing of agent models. Engagement strategy adds a fourth configurable
engagement-scoped agent; ``planner``/``triage``/``tool_review``/``finding_chat`` routing
is intentionally deferred (see MEMORY note).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# The three roles this bundle exposes. Storage column is the wider
# ``agent_name`` enum so we can add ``planner``/``triage``/etc later
# without a migration.
ConfigurableAgentRole = Literal["strategic", "engagement_strategist", "tactical", "correlate"]

_ACCEPTED_ROLES = {"strategic", "engagement_strategist", "tactical", "correlate"}


class AgentConfigRolePayload(BaseModel):
    """One agent-role -> model pinning inside a per-engagement config."""

    strategic: str | None = Field(
        default=None, description="Model for Strategic on this engagement"
    )
    engagement_strategist: str | None = Field(
        default=None, description="Model for Engagement Strategist on this engagement"
    )
    tactical: str | None = Field(default=None, description="Model for Tactical on this engagement")
    correlate: str | None = Field(
        default=None, description="Model for Correlate on this engagement"
    )


class AgentConfigRead(BaseModel):
    """One engagement's config from the current analyst's perspective."""

    model_config = ConfigDict(from_attributes=True)

    engagement_id: uuid.UUID
    engagement_slug: str
    strategic: str | None = None
    engagement_strategist: str | None = None
    tactical: str | None = None
    correlate: str | None = None
    updated_at: datetime | None = None


class AgentConfigListResponse(BaseModel):
    configurations: list[AgentConfigRead]


class AgentConfigPut(BaseModel):
    """Upsert body for ``PUT /agent-configurations/{slug}``. Missing keys
    are left unchanged; ``null`` clears a specific role."""

    strategic: str | None = None
    engagement_strategist: str | None = None
    tactical: str | None = None
    correlate: str | None = None

    # Pydantic v2: allow ``strategic: null`` to explicitly clear. We keep
    # the field optional so a caller can PUT ``{tactical: "x"}`` without
    # touching strategic/correlate. Nothing to enforce here beyond types.


class AgentConfigExport(BaseModel):
    """Downloadable export payload. Analyst uploads this later to restore.

    Uses engagement slugs (stable, human-readable) rather than uuids so
    the file is portable across environments where uuids don't match
    but slugs do.
    """

    version: int = 1
    exported_at: datetime
    exported_by_user_id: uuid.UUID
    configurations: dict[str, AgentConfigRolePayload]


class AgentConfigImportResult(BaseModel):
    applied_slugs: list[str]
    skipped_unknown_slugs: list[str]

    @field_validator("applied_slugs", "skipped_unknown_slugs")
    @classmethod
    def _sort(cls, v: list[str]) -> list[str]:
        return sorted(v)


def is_configurable_role(role: str) -> bool:
    """API-layer gate: only the three engagement-scoped roles are
    accepted this bundle. Widen this set when we ship planner/triage
    routing."""
    return role in _ACCEPTED_ROLES
