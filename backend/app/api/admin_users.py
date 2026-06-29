"""Admin-only user-management surface.

Powers the /settings/management page. Two endpoints:

    GET   /admin/users                  -> list all users (admin only)
    PATCH /admin/users/{user_id}/role   -> change a user's role

Admins can't demote themselves (avoid accidental lockout) — promote
someone else to admin first, then have them demote you.
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
