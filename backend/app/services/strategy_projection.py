"""Strategy projection service (v3 B1).

Builds the analyst-facing view of Engagement Memory: the hot set grouped by
kind (decisions first), with a token-budget-aware fetch ceiling. This is the
read half of architecture step 1 — the agent reads the hot set directly via
``app.services.memory.get_hot_set``; analysts read *this* projection.

The cap is the belt-and-suspenders guard from review item #3 on #198: the raw
``get_hot_set`` query is unbounded by design (the prompt assembler owns the
ceiling), so a burst between compactions can't push an unbounded hot set into
a prompt. We accumulate hot elements in their already-decisions-first order
until the cumulative ``token_estimate`` crosses the budget, then stop — the
remainder stays in the DB for a later, cap-driven compaction.
"""
from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import MemoryElement, MemoryKind
from app.services.memory import get_hot_set


def build_strategy_projection(
    session: Session,
    *,
    engagement_id: uuid.UUID,
    token_budget: int | None = None,
) -> dict:
    """Return the hot set grouped for the strategy screen + budget accounting.

    ``token_budget`` defaults to ``settings.hot_memory_token_budget``. The
    returned dict is shaped for ``StrategyProjectionRead`` — decisions first
    within the include order, then facts/hypotheses/open_questions/threads.
    ``capped`` indicates the budget stopped the include before the hot set was
    exhausted; ``hot_count_remaining`` is how many hot elements were left out.
    """
    budget = settings.hot_memory_token_budget if token_budget is None else token_budget
    hot = get_hot_set(session, engagement_id)  # already decisions-first, then newest

    included: list[MemoryElement] = []
    running = 0
    capped = False
    for el in hot:
        # A single oversized element still includes if we're under budget
        # (>=1 element guarantee) — otherwise the cap could return nothing.
        if included and running + el.token_estimate > budget:
            capped = True
            break
        included.append(el)
        running += el.token_estimate

    grouped: dict[MemoryKind, list[MemoryElement]] = {
        kind: [] for kind in MemoryKind
    }
    for el in included:
        grouped[el.kind].append(el)

    hot_count_remaining = len(hot) - len(included)
    return {
        "decisions": grouped[MemoryKind.decision],
        "facts": grouped[MemoryKind.fact],
        "hypotheses": grouped[MemoryKind.hypothesis],
        "open_questions": grouped[MemoryKind.open_question],
        "threads": grouped[MemoryKind.thread],
        "token_total": running,
        "token_budget": budget,
        "capped": capped,
        "hot_count_remaining": hot_count_remaining,
    }
