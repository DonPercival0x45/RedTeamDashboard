"""``GET /me`` — the acting user's own profile.

Browser viewers call this once on mount to learn their ``is_admin`` flag so
they can show or hide admin-only surfaces (the suggestion approve/reject
buttons today). Distinct from ``/api-keys/me`` which reports the API key's
scope; this one reports the User row.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import CurrentUser, DbSession

router = APIRouter()


class MeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    display_name: str | None
    is_admin: bool
    role: str
    # v1.4.11: per-analyst default model (roadmap #3 / #12).
    default_llm_provider: str | None = None
    default_llm_model: str | None = None


@router.get("/me", response_model=MeRead)
def get_me(user: CurrentUser) -> MeRead:
    return MeRead(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        is_admin=user.is_admin,
        role=user.role.value,
        default_llm_provider=user.default_llm_provider,
        default_llm_model=user.default_llm_model,
    )


class MePreferencesUpdate(BaseModel):
    """PATCH /me/preferences — set the analyst's default model. Either
    field may be null to clear; both nullable so a caller can update one
    without touching the other."""

    default_llm_provider: str | None = Field(default=None, max_length=60)
    default_llm_model: str | None = Field(default=None, max_length=128)


@router.patch("/me/preferences", response_model=MeRead)
def update_my_preferences(
    body: MePreferencesUpdate,
    session: DbSession,
    user: CurrentUser,
) -> MeRead:
    if "default_llm_provider" in body.model_fields_set:
        user.default_llm_provider = body.default_llm_provider
    if "default_llm_model" in body.model_fields_set:
        user.default_llm_model = body.default_llm_model
    session.commit()
    session.refresh(user)
    return get_me(user)
