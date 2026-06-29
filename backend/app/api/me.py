"""``GET /me`` — the acting user's own profile.

Browser viewers call this once on mount to learn their ``is_admin`` flag so
they can show or hide admin-only surfaces (the suggestion approve/reject
buttons today). Distinct from ``/api-keys/me`` which reports the API key's
scope; this one reports the User row.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from app.api.deps import CurrentUser

router = APIRouter()


class MeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    display_name: str | None
    is_admin: bool


@router.get("/me", response_model=MeRead)
def get_me(user: CurrentUser) -> MeRead:
    return MeRead.model_validate(user)
