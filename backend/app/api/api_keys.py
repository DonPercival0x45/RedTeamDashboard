"""API key management surface.

- ``POST   /api-keys``        — mint a new scoped key (admin only)
- ``GET    /api-keys``        — list keys (admin only); ``?active=true`` filters
- ``POST   /api-keys/{id}/revoke`` — revoke (idempotent; admin only)

Plaintext keys are returned once, by ``POST /api-keys`` only — the DB holds the
SHA-256 hash, so there is no way to recover a lost key (mint a replacement and
revoke the old one). Every mint and revoke writes an entry to ``audit_log``.
"""
from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession, RequireScope, hash_api_key
from app.models import ActorType, APIKey, APIKeyScope, AuditLog
from app.schemas.api_key import APIKeyCreate, APIKeyMintResponse, APIKeyRead

router = APIRouter()

# Prefix the random portion with ``rtd_`` so a leaked key is easy to spot in
# logs and the user knows what they're looking at when they paste one in.
_KEY_PREFIX = "rtd_"


def _generate_key() -> str:
    """Return a fresh 32-byte URL-safe random token, prefixed for identification."""
    return _KEY_PREFIX + secrets.token_urlsafe(32)


@router.post(
    "/api-keys",
    response_model=APIKeyMintResponse,
    status_code=201,
)
def mint_api_key(
    body: APIKeyCreate,
    session: DbSession,
    user: CurrentUser,
    _admin: Annotated[APIKey, Depends(RequireScope(APIKeyScope.admin))],
) -> APIKeyMintResponse:
    raw = _generate_key()
    row = APIKey(
        name=body.name,
        key_hash=hash_api_key(raw),
        scope=body.scope,
        created_by=user.id,
    )
    session.add(row)
    # Flush so ``row.id`` is materialized for the audit payload without
    # paying for a separate commit.
    session.flush()
    session.add(
        AuditLog(
            engagement_id=None,
            actor_type=ActorType.user,
            actor_id=str(user.id),
            event_type="api_key.created",
            payload={
                "api_key_id": str(row.id),
                "name": body.name,
                "scope": body.scope.value,
            },
        )
    )
    session.commit()
    session.refresh(row)

    return APIKeyMintResponse(
        id=row.id,
        name=row.name,
        scope=row.scope,
        created_by=row.created_by,
        revoked_at=row.revoked_at,
        last_used_at=row.last_used_at,
        created_at=row.created_at,
        key=raw,
    )


@router.get(
    "/api-keys",
    response_model=list[APIKeyRead],
)
def list_api_keys(
    session: DbSession,
    _admin: Annotated[APIKey, Depends(RequireScope(APIKeyScope.admin))],
    active: Annotated[bool | None, Query()] = None,
) -> list[APIKey]:
    stmt = select(APIKey)
    if active is True:
        stmt = stmt.where(APIKey.revoked_at.is_(None))
    elif active is False:
        stmt = stmt.where(APIKey.revoked_at.is_not(None))
    stmt = stmt.order_by(APIKey.created_at.desc())
    return list(session.execute(stmt).scalars())


@router.post(
    "/api-keys/{api_key_id}/revoke",
    response_model=APIKeyRead,
)
def revoke_api_key(
    api_key_id: UUID,
    session: DbSession,
    user: CurrentUser,
    _admin: Annotated[APIKey, Depends(RequireScope(APIKeyScope.admin))],
) -> APIKey:
    row = session.get(APIKey, api_key_id)
    if row is None:
        raise HTTPException(status_code=404, detail="API key not found")
    if row.revoked_at is not None:
        return row  # idempotent

    row.revoked_at = datetime.now(tz=UTC)
    row.revoked_by = user.id
    session.add(
        AuditLog(
            engagement_id=None,
            actor_type=ActorType.user,
            actor_id=str(user.id),
            event_type="api_key.revoked",
            payload={
                "api_key_id": str(row.id),
                "name": row.name,
                "scope": row.scope.value,
            },
        )
    )
    session.commit()
    session.refresh(row)
    return row
