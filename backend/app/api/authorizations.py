"""Session authorizations HTTP surface.

- ``GET  /engagements/{engagement_id}/authorizations?active=true`` — list grants
- ``POST /authorizations/{id}/revoke``                             — revoke one

Grants are *created* implicitly by approving an interrupt with
``remember_for_session`` (see ``app.api.approvals``); this module only lists and
revokes them. Revoking sets ``revoked_at`` (the grant history is preserved) and
takes effect immediately — the worker's authorizer queries live on each call.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.api.deps import CurrentNonGuestUser, DbSession
from app.models import ActorType, AuditLog, Authorization
from app.schemas.authorization import AuthorizationRead

router = APIRouter()


@router.get(
    "/engagements/{engagement_id}/authorizations",
    response_model=list[AuthorizationRead],
)
def list_authorizations(
    engagement_id: UUID,
    session: DbSession,
    active: Annotated[bool | None, Query(description="Filter by active/revoked.")] = None,
) -> list[Authorization]:
    stmt = select(Authorization).where(Authorization.engagement_id == engagement_id)
    if active is True:
        stmt = stmt.where(Authorization.revoked_at.is_(None))
    elif active is False:
        stmt = stmt.where(Authorization.revoked_at.is_not(None))
    stmt = stmt.order_by(Authorization.created_at.desc())
    return list(session.execute(stmt).scalars())


@router.post("/authorizations/{authorization_id}/revoke", response_model=AuthorizationRead)
def revoke_authorization(
    authorization_id: UUID,
    session: DbSession,
    user: CurrentNonGuestUser,
) -> Authorization:
    grant = session.get(Authorization, authorization_id)
    if grant is None:
        raise HTTPException(status_code=404, detail="authorization not found")
    if grant.revoked_at is not None:
        return grant  # idempotent — already revoked

    grant.revoked_at = datetime.now(tz=UTC)
    grant.revoked_by = user.id
    session.add(
        AuditLog(
            engagement_id=grant.engagement_id,
            actor_type=ActorType.user,
            actor_id=str(user.id),
            event_type="authorization.revoked",
            payload={"authorization_id": str(grant.id), "tool": grant.tool_name},
        )
    )
    session.commit()
    session.refresh(grant)
    return grant
