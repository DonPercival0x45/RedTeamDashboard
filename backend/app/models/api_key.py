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
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, uuid7


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
# any required scope at index ≤ N.
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
