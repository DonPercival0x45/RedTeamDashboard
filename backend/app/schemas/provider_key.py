"""Wire schemas for BYO model + MCP credentials uploaded by the analyst.

Keys are now ephemeral — held in Redis under a per-user hash with a sliding
TTL, never at rest in the DB. Crucial invariant unchanged: server-bound
shapes (``ProviderKeyImport``, ``ProviderKeyCreate``, ``ProviderKeyUpdate``)
accept the plaintext ``api_key`` field; the read shape (``ProviderKeyRead``)
NEVER carries it — only ``key_last4`` for UI masking.
"""
from __future__ import annotations

import enum
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ProviderKeyKind(enum.StrEnum):
    """``model_provider`` — LLM credentials (Anthropic, OpenAI, Azure, …).
    ``mcp_server`` — external MCP server endpoints (GitHub MCP, web search,
    …). ``other`` — catch-all for credentials the analyst wants stored but
    not auto-consumed by the agent stack (third-party APIs, vault tokens,
    etc.). Same storage shape, different consumers."""

    model_provider = "model_provider"
    mcp_server = "mcp_server"
    other = "other"


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


class ProviderKeyProbe(BaseModel):
    """POST body for the unsaved-key test — the /settings/keys form calls
    this before creating a row, so the analyst can verify a key + endpoint
    and pull the live model list without saving first."""

    provider: str = Field(min_length=1, max_length=60)
    api_key: str | None = Field(default=None, max_length=4096)
    endpoint: str | None = Field(default=None, max_length=2000)
    is_local: bool = False


class ProviderKeyProbeResult(BaseModel):
    """What a liveness/model-discovery probe returns. Never carries the
    key. ``ok`` = reachable AND authorized; ``models`` is the discovered
    catalog the UI can offer as a dropdown."""

    ok: bool
    reachable: bool
    supported: bool = True
    status_code: int | None = None
    latency_ms: int | None = None
    models: list[str] = Field(default_factory=list)
    checked_url: str | None = None
    error: str | None = None


class ProviderKeyImportErrorRow(BaseModel):
    index: int
    name: str | None
    reason: str


class ProviderKeyImportResult(BaseModel):
    created: list[ProviderKeyRead]
    errors: list[ProviderKeyImportErrorRow]
    duplicates: list[ProviderKeyImportErrorRow]
