"""Per-mode structured output schemas for the v3 intelligence agent (B4-2).

architecture-answers §B4 + the locked decision: each prompt-mode returns what
it naturally produces (option a — per-mode schemas), not a unified shape with
empty fields. Tighter schemas → more reliable structured-output compliance.

Persistence (``app.agents.intelligence.run_intelligence_analysis``):
  - analysis       → proposed facts/hypotheses as Memory elements
  - ideation       → proposed hypotheses (Memory) + proposed work items (queue)
  - coverage_review → fold actions (``fold_into_decision``) + re-collection notes
  - strategy       → proposed decisions (Memory) + proposed work items (queue)
"""
from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Shared sub-schemas
# ---------------------------------------------------------------------------


class ProposedFact(BaseModel):
    summary: str = Field(min_length=1, max_length=500)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    finding_refs: list[UUID] = Field(default_factory=list, max_length=20)


class ProposedHypothesis(BaseModel):
    summary: str = Field(min_length=1, max_length=500)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    supports_finding_refs: list[UUID] = Field(default_factory=list, max_length=20)
    refutes_finding_refs: list[UUID] = Field(default_factory=list, max_length=20)


class ProposedWorkItem(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    # disposition values match WorkItemDisposition (tool_backed / tool_backed_mcp
    # / manual_local / build / blocked / needs_decision / out_of_scope).
    disposition: str = Field(default="manual_local", max_length=40)
    rationale: str = Field(default="", max_length=1000)


class ProposedDecision(BaseModel):
    summary: str = Field(min_length=1, max_length=500)
    rationale: str = Field(default="", max_length=1000)


class ProposedFold(BaseModel):
    """coverage_review compaction action: close resolved hypotheses into a decision."""
    hypothesis_ids: list[UUID] = Field(min_length=1, max_length=20)
    decision_summary: str = Field(min_length=1, max_length=500)
    rationale: str = Field(default="", max_length=1000)


# ---------------------------------------------------------------------------
# Per-mode outputs
# ---------------------------------------------------------------------------


class AnalysisOutput(BaseModel):
    proposed_facts: list[ProposedFact] = Field(default_factory=list, max_length=20)
    proposed_hypotheses: list[ProposedHypothesis] = Field(default_factory=list, max_length=20)


class IdeationOutput(BaseModel):
    proposed_hypotheses: list[ProposedHypothesis] = Field(default_factory=list, max_length=20)
    proposed_work_items: list[ProposedWorkItem] = Field(default_factory=list, max_length=10)


class CoverageReviewOutput(BaseModel):
    folds: list[ProposedFold] = Field(default_factory=list, max_length=10)
    re_collection_node_ids: list[str] = Field(default_factory=list, max_length=20)


class StrategyOutput(BaseModel):
    situation_summary: str = Field(default="", max_length=5000)
    proposed_decisions: list[ProposedDecision] = Field(default_factory=list, max_length=10)
    proposed_work_items: list[ProposedWorkItem] = Field(default_factory=list, max_length=10)
