"""Milestone event contract — the canonical names + payload shapes Track A
emits (A6) and Track B consumes (B3). (architecture-v3-tracker PR 0.)

PR 0 defines the *contract only* — the symbols both tracks import so neither
uses magic strings and both agree on payload fields. No stream wiring lives
here; A6 publishes these to the engagement's outbound stream (reusing
``app.runs.events.encode_event``), B3's milestone runner reads them.

Why typed payloads (not free dicts): the three events are the single trigger
surface between two parallel tracks. Pinning the fields now means B3 can be
written against a stable shape before A6 emits, and A6 can't silently drift a
field name B3 depends on.
"""
from __future__ import annotations

from typing import Any, TypedDict

# ---------------------------------------------------------------------------
# Canonical event names
# ---------------------------------------------------------------------------

COLLECTION_JOB_COMPLETED = "collection.job.completed"
COVERAGE_GAP_OPENED = "coverage.gap.opened"
BASELINE_COMPLETED = "baseline.completed"

MILESTONE_EVENT_TYPES: frozenset[str] = frozenset(
    {COLLECTION_JOB_COMPLETED, COVERAGE_GAP_OPENED, BASELINE_COMPLETED}
)


# ---------------------------------------------------------------------------
# Payload shapes
# ---------------------------------------------------------------------------


class FindingsSummary(TypedDict):
    """Deterministic rollup of what a collection job produced — the same shape
    B2 computes for the strategy projection, so B3 can decide significance
    without re-querying. Counts only; no free text."""

    new: int
    unvalidated: int
    high_severity: int
    total: int


class CollectionJobCompletedPayload(TypedDict):
    """Track A emits this when a playbook run finishes. B3 gathers significant
    findings (``is_new OR not_validated OR high_severity``) and invokes the
    agent in a gather-then-analyze batch (architecture-answers §B3)."""

    engagement_id: str
    playbook_run_id: str
    methodology_id: str | None
    node_ids: list[str]
    asset_class: str
    scope_subset: list[str]
    findings_summary: FindingsSummary


class CoverageGapOpenedPayload(TypedDict):
    """Track A emits this when the coverage computation finds an unsatisfied
    baseline node (or a re-opened one). B3 surfaces it to the agent as a
    recommended collection / work item."""

    engagement_id: str
    node_id: str
    node_tier: str
    asset_class: str
    reason: str


class BaselineCompletedPayload(TypedDict):
    """Track A emits this when all baseline-tier nodes are ``satisfied`` for
    in-scope assets — the hard phase gate. B3 flips the engagement to
    ``exploration`` (stamping ``baseline_completed_at``) and the milestone
    runner unlocks general/open-ended analysis."""

    engagement_id: str
    methodology_id: str
    baseline_completed_at: str


# ---------------------------------------------------------------------------
# Builders — validate + return the logical envelope (caller wraps for the wire)
# ---------------------------------------------------------------------------


def collection_job_completed(
    *,
    engagement_id: str,
    playbook_run_id: str,
    methodology_id: str | None,
    node_ids: list[str],
    asset_class: str,
    scope_subset: list[str],
    findings_summary: FindingsSummary | dict[str, Any],
) -> dict[str, Any]:
    return {
        "type": COLLECTION_JOB_COMPLETED,
        "engagement_id": engagement_id,
        "playbook_run_id": playbook_run_id,
        "methodology_id": methodology_id,
        "node_ids": list(node_ids),
        "asset_class": asset_class,
        "scope_subset": list(scope_subset),
        "findings_summary": dict(findings_summary),
    }


def coverage_gap_opened(
    *,
    engagement_id: str,
    node_id: str,
    node_tier: str,
    asset_class: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "type": COVERAGE_GAP_OPENED,
        "engagement_id": engagement_id,
        "node_id": node_id,
        "node_tier": node_tier,
        "asset_class": asset_class,
        "reason": reason,
    }


def baseline_completed(
    *,
    engagement_id: str,
    methodology_id: str,
    baseline_completed_at: str,
) -> dict[str, Any]:
    return {
        "type": BASELINE_COMPLETED,
        "engagement_id": engagement_id,
        "methodology_id": methodology_id,
        "baseline_completed_at": baseline_completed_at,
    }
