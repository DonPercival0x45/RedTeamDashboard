"""Admin-only user-management surface.

Powers the /settings/management page. Three endpoints:

    GET   /admin/users                   -> list all users (admin only)
    PATCH /admin/users/{user_id}/role    -> change a user's role
    PATCH /admin/users/{user_id}/active  -> activate / deactivate (roadmap #6)

Admins can't demote or deactivate themselves (avoid accidental lockout)
— promote someone else to admin first, then have them demote/deactivate
you. Deactivation is soft (``is_active=False``): the user can't sign in
or be resolved from headers, but their ``created_by`` rows on findings,
suggestions, attachments, and audit logs stay intact (no FK breakage).
"""
from __future__ import annotations

import uuid
from datetime import datetime

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.api.deps import CurrentAdminUser, DbSession
from app.models import ActorType, AuditLog, User, UserRole

logger = structlog.get_logger(__name__)

router = APIRouter()


class AdminUserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    display_name: str | None
    role: UserRole
    is_active: bool
    created_at: datetime
    updated_at: datetime


class AdminUserRoleUpdate(BaseModel):
    role: UserRole


class AdminUserActiveUpdate(BaseModel):
    # v1.4.15 (roadmap #6): soft activate/deactivate. Soft so created_by
    # FKs on findings / suggestions / attachments / audit logs stay valid.
    is_active: bool


@router.get("/admin/users", response_model=list[AdminUserRead])
def list_users(
    session: DbSession, admin: CurrentAdminUser
) -> list[AdminUserRead]:
    rows = list(
        session.execute(select(User).order_by(User.email)).scalars()
    )
    return [AdminUserRead.model_validate(u) for u in rows]


@router.patch(
    "/admin/users/{user_id}/role", response_model=AdminUserRead
)
def update_user_role(
    user_id: uuid.UUID,
    body: AdminUserRoleUpdate,
    session: DbSession,
    admin: CurrentAdminUser,
) -> AdminUserRead:
    if user_id == admin.id and body.role != UserRole.admin:
        # Don't let an admin accidentally lock themselves out. Promote
        # someone else first, then have them demote you.
        raise HTTPException(
            status_code=400,
            detail=(
                "you can't demote yourself; promote another user to admin "
                "first, then ask them to demote you"
            ),
        )
    target = session.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="user not found")
    if target.role == body.role:
        return AdminUserRead.model_validate(target)

    previous = target.role.value
    target.role = body.role

    session.add(
        AuditLog(
            engagement_id=None,
            actor_type=ActorType.user,
            actor_id=str(admin.id),
            event_type="user.role_changed",
            payload={
                "target_user_id": str(target.id),
                "target_email": target.email,
                "from_role": previous,
                "to_role": body.role.value,
            },
        )
    )
    session.commit()
    session.refresh(target)
    return AdminUserRead.model_validate(target)


@router.patch(
    "/admin/users/{user_id}/active", response_model=AdminUserRead
)
def update_user_active(
    user_id: uuid.UUID,
    body: AdminUserActiveUpdate,
    session: DbSession,
    admin: CurrentAdminUser,
) -> AdminUserRead:
    """Activate or deactivate a user (roadmap #6).

    Soft: flips ``is_active`` only — the row stays so ``created_by`` FKs
    on findings / suggestions / attachments / audit logs don't dangle. An
    admin can't deactivate themselves (same lockout guard as role change).
    """
    if user_id == admin.id and not body.is_active:
        raise HTTPException(
            status_code=400,
            detail=(
                "you can't deactivate yourself; promote another user to "
                "admin first, then ask them to deactivate you"
            ),
        )
    target = session.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="user not found")
    if target.is_active == body.is_active:
        return AdminUserRead.model_validate(target)

    previous = target.is_active
    target.is_active = body.is_active
    session.add(
        AuditLog(
            engagement_id=None,
            actor_type=ActorType.user,
            actor_id=str(admin.id),
            event_type="user.active_changed",
            payload={
                "target_user_id": str(target.id),
                "target_email": target.email,
                "from_active": previous,
                "to_active": body.is_active,
            },
        )
    )
    session.commit()
    session.refresh(target)
    return AdminUserRead.model_validate(target)
