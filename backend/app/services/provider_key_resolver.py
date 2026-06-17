"""Resolve the acting user's stored API key for a given provider at run time.

This is the bridge between the BYO key store (``user_provider_keys`` table)
and the LLM-instantiation path (``app.orchestrator.llm.make_llm``).

Policy (locked in 2026-06-17): no silent fallback to ``settings.{provider}_api_key``.
If the acting user has no row for the requested provider, the call raises
``NoProviderKeyError`` and the HTTP/worker layer surfaces a 4xx pointing
the analyst at ``/settings/keys``. Matches the original BYO ask
("complete control over their keys").

Selection rule when the user has multiple rows for the same provider:
pick the most recently updated (rotation wins). A future "default" flag
on UserProviderKey would replace this; for now MRU is the rule.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ProviderKeyKind, UserProviderKey
from app.services.secret_box import decrypt


class NoProviderKeyError(Exception):
    """The acting user has no ``UserProviderKey`` for the requested provider."""

    def __init__(self, *, user_id: uuid.UUID, provider: str) -> None:
        self.user_id = user_id
        self.provider = provider
        super().__init__(
            f"no provider key configured for '{provider}' on user {user_id}; "
            f"upload one at /settings/keys"
        )


@dataclass(frozen=True, slots=True)
class ResolvedProviderKey:
    """Outcome of a successful resolution. ``api_key`` is decrypted plaintext
    (or ``None`` for local providers); ``endpoint`` is the provider-specific
    base URL (or ``None`` to use the SDK default)."""

    row_id: uuid.UUID
    name: str
    provider: str
    is_local: bool
    api_key: str | None
    endpoint: str | None


def resolve_for_user(
    session: Session, *, user_id: uuid.UUID, provider: str
) -> ResolvedProviderKey:
    """Return the user's most-recently-updated key for ``provider``.

    Limited to ``kind=model_provider`` rows — MCP-server entries live in the
    same table but never satisfy an LLM call.
    """
    provider_norm = provider.strip().lower()
    row = session.execute(
        select(UserProviderKey)
        .where(UserProviderKey.user_id == user_id)
        .where(UserProviderKey.kind == ProviderKeyKind.model_provider)
        .where(UserProviderKey.provider == provider_norm)
        .order_by(UserProviderKey.updated_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if row is None:
        raise NoProviderKeyError(user_id=user_id, provider=provider_norm)

    plaintext: str | None = None
    if row.encrypted_key:
        plaintext = decrypt(row.encrypted_key)
    elif not row.is_local:
        # Non-local row with no ciphertext shouldn't happen (the schema-level
        # validator catches it on upload) but defend anyway.
        raise NoProviderKeyError(user_id=user_id, provider=provider_norm)

    return ResolvedProviderKey(
        row_id=row.id,
        name=row.name,
        provider=row.provider,
        is_local=row.is_local,
        api_key=plaintext,
        endpoint=row.endpoint,
    )
