from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models import ScopeKind
from app.services.effective_scope import EffectiveScopeState


class EffectiveScopeDecisionRead(BaseModel):
    """Explainable projection of the canonical backend scope matcher."""

    model_config = ConfigDict(from_attributes=True)

    state: EffectiveScopeState
    allowed: bool
    reason_code: str
    reason: str
    target: str | None = None
    target_kind: ScopeKind | None = None
    matched_include_id: UUID | None = None
    matched_exclusion_id: UUID | None = None
