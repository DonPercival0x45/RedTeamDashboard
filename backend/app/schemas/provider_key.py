"""Wire schemas for BYO model + MCP credentials uploaded by the analyst.

Mirrors ``app/models/user_provider_key.py``. Crucial invariant: server-bound
shapes (``ProviderKeyImport``, ``ProviderKeyCreate``, ``ProviderKeyUpdate``)
accept the plaintext ``api_key`` field; the read shape (``ProviderKeyRead``)
NEVER carries it — only ``key_last4`` for UI masking.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.user_provider_key import ProviderKeyKind


class ProviderKeyEntry(BaseModel):
    """One uploaded entry — used both for single-create and inside import."""

    name: str = Field(min_length=1, max_length=120)
    provider: str = Field(min_length=1, max_length=60)
    kind: ProviderKeyKind = ProviderKeyKind.model_provider
    models: list[str] = Field(default_factory=list)
    is_local: bool = False
    endpoint: str | None = Field(default=None, max_length=2000)
    api_key: str | None = Field(default=None, max_length=4096)
    extra: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_required_fields(self) -> ProviderKeyEntry:
        # Non-local providers must carry a key (otherwise we have nothing to
        # call the API with). Local providers may carry one if the analyst
        # uses a custom endpoint that needs auth.
        if not self.is_local and not self.api_key:
            raise ValueError(
                f"provider entry '{self.name}': api_key required for non-local "
                "providers (set is_local=true or supply the key)"
            )
        if self.kind == ProviderKeyKind.mcp_server and not self.endpoint:
            raise ValueError(
                f"provider entry '{self.name}': mcp_server entries require an "
                "endpoint URL"
            )
        return self


class ProviderKeyImport(BaseModel):
    """Bulk upload payload — what the JSON file body parses into."""

    providers: list[ProviderKeyEntry] = Field(min_length=1)


class ProviderKeyUpdate(BaseModel):
    """PATCH body — every field optional. ``api_key`` set means rotate."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    models: list[str] | None = None
    endpoint: str | None = Field(default=None, max_length=2000)
    api_key: str | None = Field(default=None, max_length=4096)
    extra: dict[str, Any] | None = None


class ProviderKeyRead(BaseModel):
    """What we return — no plaintext key, ever."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    kind: ProviderKeyKind
    name: str
    provider: str
    is_local: bool
    models: list[str]
    endpoint: str | None
    key_last4: str | None
    extra: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class ProviderKeyImportErrorRow(BaseModel):
    index: int
    name: str | None
    reason: str


class ProviderKeyImportResult(BaseModel):
    created: list[ProviderKeyRead]
    errors: list[ProviderKeyImportErrorRow]
    duplicates: list[ProviderKeyImportErrorRow]
