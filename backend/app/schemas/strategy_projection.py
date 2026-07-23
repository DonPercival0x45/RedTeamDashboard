"""Strategy projection — the analyst-facing view of Engagement Memory (v3 B1).

The Memory *hot set* is what the agent reads; the *strategy projection* is
what the analyst sees on the strategy screen — the same elements grouped by
kind (decisions first), with a token-budget-aware fetch cap so a burst between
compactions can't push an unbounded hot set into a prompt (architecture-v3
tracker B1; review item #3 on #198).

This is a read path over the Memory persistence layer (PR #198) + the shared
contract (#199). It coexists with the legacy ``/strategy`` revision endpoint;
that retires at Convergence once the projection is the strategy surface.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models import ActorType, MemoryKind, MemoryStatus


class MemoryElementProjection(BaseModel):
    """One hot-set element rendered for the strategy screen."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    kind: MemoryKind
    status: MemoryStatus
    summary: str
    confidence: float | None
    author_type: ActorType
    author_id: str
    token_estimate: int
    created_at: datetime


class StrategyProjectionRead(BaseModel):
    """The hot Memory set grouped for the strategy screen + budget accounting.

    ``capped`` is True when the fetch hit the token budget before exhausting
    the hot set — the remainder stays in the DB (not loaded into the prompt).
    ``token_total`` reflects only what's included here.
    """

    decisions: list[MemoryElementProjection]
    facts: list[MemoryElementProjection]
    hypotheses: list[MemoryElementProjection]
    open_questions: list[MemoryElementProjection]
    threads: list[MemoryElementProjection]
    token_total: int
    token_budget: int
    capped: bool
    hot_count_remaining: int
