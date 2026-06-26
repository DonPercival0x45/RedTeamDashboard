from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, uuid7


# ---------------------------------------------------------------------------
# api_key
# ---------------------------------------------------------------------------

"""Per-deployment API keys — the production auth surface.

The dev-time ``X-User-Id`` header is a backdoor for local work; in a real
deployment every API call carries an ``X-API-Key`` instead. The kit's installer
mints the first ``admin`` key after migrations; that key can then issue scoped
keys (``cli`` for Project work, ``viewer`` for the central viewer) via
``POST /api-keys``.

Keys are stored hashed (SHA-256 of the random token). The plaintext is only
visible at mint time — see ``APIKeyMintResponse``. SHA-256 is fine here: the
input is a 32-byte random token, not a password, so we need fast deterministic
lookup, not slow-hash protection against guessing.
"""


class APIKeyScope(enum.StrEnum):
    """Coarse-grained authorization tiers, lowest to highest.

    - ``viewer`` — read-only GETs (findings, events, grants list, scope). For
      the central viewer's connection to a tenant's API.
    - ``cli``    — full Project work: start runs, approve, edit scope,
      revoke grants. For the operator's CLI.
    - ``admin``  — everything ``cli`` can do, plus mint/list/revoke API keys.
      For the first key minted by the kit; create others sparingly.
    """

    viewer = "viewer"
    cli = "cli"
    admin = "admin"


# Privilege ladder used by RequireScope: a key with scope at index N satisfies
# any required scope at index <= N.
_SCOPE_RANK = {APIKeyScope.viewer: 0, APIKeyScope.cli: 1, APIKeyScope.admin: 2}


def scope_satisfies(have: APIKeyScope, need: APIKeyScope) -> bool:
    return _SCOPE_RANK[have] >= _SCOPE_RANK[need]


class APIKey(Base, TimestampMixin):
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # SHA-256 hex digest of the raw token. 64-char fixed length; indexed unique.
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    scope: Mapped[APIKeyScope] = mapped_column(
        Enum(APIKeyScope, name="api_key_scope"), nullable=False
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ---------------------------------------------------------------------------
# user
# ---------------------------------------------------------------------------


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    display_name: Mapped[str | None] = mapped_column(String(200))
    entra_oid: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


# ---------------------------------------------------------------------------
# authorization
# ---------------------------------------------------------------------------


class Authorization(Base, TimestampMixin):
    """A standing per-(Project, tool) approval — a "session grant".

    While active (``revoked_at`` is NULL), the gate auto-approves in-scope calls
    to ``tool_name`` for this Project instead of interrupting for a human;
    each such auto-approval is still written to the audit log carrying this
    row's id. Created when an operator approves a pending interrupt with
    "remember for this session", and lives until revoked or the Project is
    flushed (FK cascade).

    A partial unique index keeps at most one *active* grant per (Project,
    tool); revoking sets ``revoked_at`` rather than deleting, so the grant
    history survives.
    """

    __tablename__ = "authorizations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tool_name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    granted_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    note: Mapped[str | None] = mapped_column(String(500))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )


# ---------------------------------------------------------------------------
# approval
# ---------------------------------------------------------------------------


class RiskLevel(enum.StrEnum):
    passive = "passive"
    active = "active"
    destructive = "destructive"


class ApprovalStatus(enum.StrEnum):
    pending = "pending"
    approved = "approved"
    denied = "denied"
    edited = "edited"
    auto = "auto"


class Approval(Base, TimestampMixin):
    __tablename__ = "approvals"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    thread_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    node: Mapped[str | None] = mapped_column(String(120))
    tool_name: Mapped[str] = mapped_column(String(200), nullable=False)
    tool_args: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    risk: Mapped[RiskLevel] = mapped_column(Enum(RiskLevel, name="risk_level"), nullable=False)
    scope_check: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    status: Mapped[ApprovalStatus] = mapped_column(
        Enum(ApprovalStatus, name="approval_status"),
        default=ApprovalStatus.pending,
        nullable=False,
    )
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    decision_args: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    authorization_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ---------------------------------------------------------------------------
# user_provider_key
# ---------------------------------------------------------------------------


class ProviderKeyKind(enum.StrEnum):
    """What sort of remote the key authenticates against. ``model_provider``
    holds LLM API keys; ``mcp_server`` holds keys for third-party MCP servers
    the analyst connects to (GitHub, web search, etc.)."""

    model_provider = "model_provider"
    mcp_server = "mcp_server"


class UserProviderKey(Base, TimestampMixin):
    """One BYO credential entry uploaded by an analyst.

    The plaintext key never lives in this table — only the Fernet ciphertext
    (``encrypted_key``) and a 4-char tail (``key_last4``) we can show in the
    UI without decrypting. Local providers (Ollama on the analyst's box,
    self-hosted Hugging Face) carry no key, just an ``endpoint``.
    """

    __tablename__ = "user_provider_keys"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_user_provider_keys_user_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[ProviderKeyKind] = mapped_column(
        Enum(ProviderKeyKind, name="provider_key_kind"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    provider: Mapped[str] = mapped_column(String(60), nullable=False)
    is_local: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    models: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    endpoint: Mapped[str | None] = mapped_column(Text, nullable=True)
    encrypted_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    key_last4: Mapped[str | None] = mapped_column(String(8), nullable=True)
    extra: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, nullable=False
    )


# ---------------------------------------------------------------------------
# audit_log
# ---------------------------------------------------------------------------


class ActorType(enum.StrEnum):
    user = "user"
    agent = "agent"
    system = "system"


class AuditLog(Base):
    """Append-only log of authorization-relevant events.

    Immutability is enforced at the DB layer via a BEFORE UPDATE/DELETE trigger
    (see 0001_initial migration). The trigger respects a session-local bypass
    flag set only inside the SECURITY DEFINER flush_engagement() helper.
    """

    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
    )
    actor_type: Mapped[ActorType] = mapped_column(
        Enum(ActorType, name="actor_type"), nullable=False
    )
    actor_id: Mapped[str | None] = mapped_column(String(200), index=True)
    event_type: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
