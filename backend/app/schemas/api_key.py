"""Wire-format models for the api_keys surface.

``APIKeyMintResponse`` includes the plaintext ``key`` — this is the ONE moment
the caller sees it. It's never re-fetchable from any other endpoint; the DB
only stores the SHA-256 hash. Treat it like a one-shot secret.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models import APIKeyScope


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
