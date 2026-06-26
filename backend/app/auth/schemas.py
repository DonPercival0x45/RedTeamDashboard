from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models import APIKeyScope, ApprovalStatus, RiskLevel


# ---------------------------------------------------------------------------
# api_key schemas
# ---------------------------------------------------------------------------

"""Wire-format models for the api_keys surface.

``APIKeyMintResponse`` includes the plaintext ``key`` — this is the ONE moment
the caller sees it. It's never re-fetchable from any other endpoint; the DB
only stores the SHA-256 hash. Treat it like a one-shot secret.
"""


class APIKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    scope: APIKeyScope


class APIKeyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    scope: APIKeyScope
    created_by: UUID | None
    revoked_at: datetime | None
    last_used_at: datetime | None
    created_at: datetime


class APIKeyMintResponse(APIKeyRead):
    """One-time payload returned by POST /api-keys. ``key`` is the plaintext
    token the caller must save — it cannot be retrieved again."""

    key: str


# ---------------------------------------------------------------------------
# approval schemas
# ---------------------------------------------------------------------------

"""Wire-format models for the approvals endpoints."""


class ApprovalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
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
    # When approving, also grant a standing per-(Project, tool) session
    # authorization so future in-scope calls to this tool auto-run. Ignored on
    # denial.
    remember_for_session: bool = False


# ---------------------------------------------------------------------------
# authorization schemas
# ---------------------------------------------------------------------------

"""Wire-format model for session authorizations (per-tool standing grants)."""


class AuthorizationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    tool_name: str
    granted_by: UUID | None
    note: str | None
    revoked_at: datetime | None
    revoked_by: UUID | None
    created_at: datetime
    updated_at: datetime
