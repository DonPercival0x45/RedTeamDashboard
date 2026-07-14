"""Deterministic engagement completion preflight and hashing.

No provider credential or model call is used here. Completion is an analyst
approval over canonical database facts, never an agent assertion.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    AgentExecution,
    AgentExecutionStatus,
    Approval,
    ApprovalStatus,
    CoverageItem,
    CoverageStatus,
    Engagement,
    EngagementObjective,
    EngagementStrategyRevision,
    ObjectiveStatus,
    StrategyRevisionState,
    StrategySignal,
    StrategySignalStatus,
    Suggestion,
    SuggestionStatus,
    Task,
    TaskStatus,
    WorkItem,
    WorkItemStatus,
)
from app.schemas.completion import (
    AcceptedGapCandidate,
    CompletionCheck,
    CompletionReadiness,
    CompletionRef,
)
from app.services.report_readiness import build_report_readiness


def _refs(kind: str, ids: list[object]) -> list[CompletionRef]:
    return [CompletionRef(type=kind, id=str(value)) for value in sorted(ids, key=str)]  # type: ignore[arg-type]


def _check(
    key: str,
    *,
    count: int,
    message: str,
    refs: list[CompletionRef] | None = None,
    waivable: bool = False,
    severity: str = "blocker",
) -> CompletionCheck:
    return CompletionCheck(
        key=key,
        severity=severity,  # type: ignore[arg-type]
        count=count,
        waivable=waivable,
        refs=refs or [],
        message=message,
    )


def build_completion_readiness(
    session: Session,
    *,
    engagement: Engagement,
) -> CompletionReadiness:
    """Compile stable closure facts and a content hash.

    ``generated_at`` is deliberately excluded from the hash. Rows and checks are
    ordered so equivalent database state yields the same hash across retries.
    """
    remaining_work = list(
        session.execute(
            select(WorkItem.id)
            .where(
                WorkItem.engagement_id == engagement.id,
                WorkItem.status.in_(
                    [
                        WorkItemStatus.ready,
                        WorkItemStatus.in_progress,
                        WorkItemStatus.blocked,
                    ]
                ),
            )
            .order_by(WorkItem.id)
        ).scalars()
    )
    open_objectives = list(
        session.execute(
            select(EngagementObjective.id)
            .where(
                EngagementObjective.engagement_id == engagement.id,
                EngagementObjective.status.in_(
                    [
                        ObjectiveStatus.planned,
                        ObjectiveStatus.active,
                        ObjectiveStatus.blocked,
                    ]
                ),
            )
            .order_by(EngagementObjective.id)
        ).scalars()
    )
    open_suggestions = list(
        session.execute(
            select(Suggestion.id)
            .where(
                Suggestion.engagement_id == engagement.id,
                Suggestion.status == SuggestionStatus.open,
            )
            .order_by(Suggestion.id)
        ).scalars()
    )
    proposed_revisions = list(
        session.execute(
            select(EngagementStrategyRevision.id)
            .where(
                EngagementStrategyRevision.engagement_id == engagement.id,
                EngagementStrategyRevision.state == StrategyRevisionState.proposed,
            )
            .order_by(EngagementStrategyRevision.id)
        ).scalars()
    )
    open_signals = list(
        session.execute(
            select(StrategySignal.id)
            .where(
                StrategySignal.engagement_id == engagement.id,
                StrategySignal.status == StrategySignalStatus.open,
            )
            .order_by(StrategySignal.id)
        ).scalars()
    )
    deferred_work = list(
        session.execute(
            select(WorkItem.id)
            .where(
                WorkItem.engagement_id == engagement.id,
                WorkItem.status == WorkItemStatus.deferred,
            )
            .order_by(WorkItem.id)
        ).scalars()
    )
    active_tasks = list(
        session.execute(
            select(Task.id)
            .where(
                Task.engagement_id == engagement.id,
                Task.status.in_(
                    [
                        TaskStatus.pending,
                        TaskStatus.dispatched,
                        TaskStatus.running,
                    ]
                ),
            )
            .order_by(Task.id)
        ).scalars()
    )
    active_agents = list(
        session.execute(
            select(AgentExecution.id)
            .where(
                AgentExecution.engagement_id == engagement.id,
                AgentExecution.status == AgentExecutionStatus.running,
            )
            .order_by(AgentExecution.id)
        ).scalars()
    )
    pending_approvals = list(
        session.execute(
            select(Approval.id)
            .where(
                Approval.engagement_id == engagement.id,
                Approval.status == ApprovalStatus.pending,
            )
            .order_by(Approval.id)
        ).scalars()
    )
    coverage_gaps = list(
        session.execute(
            select(CoverageItem)
            .where(
                CoverageItem.engagement_id == engagement.id,
                CoverageItem.status.in_(
                    [
                        CoverageStatus.not_started,
                        CoverageStatus.planned,
                        CoverageStatus.active,
                        CoverageStatus.blocked,
                        CoverageStatus.deferred,
                    ]
                ),
            )
            .order_by(CoverageItem.id)
        ).scalars()
    )

    checks: list[CompletionCheck] = [
        _check(
            "remaining_work",
            count=len(remaining_work),
            refs=_refs("work_item", remaining_work),
            message=f"{len(remaining_work)} committed work items remain",
        ),
        _check(
            "open_objectives",
            count=len(open_objectives),
            refs=_refs("objective", open_objectives),
            message=f"{len(open_objectives)} objectives are not terminal",
        ),
        _check(
            "open_suggestions",
            count=len(open_suggestions),
            refs=_refs("suggestion", open_suggestions),
            message=f"{len(open_suggestions)} proposals await an analyst decision",
        ),
        _check(
            "proposed_strategy_revisions",
            count=len(proposed_revisions),
            refs=_refs("strategy_revision", proposed_revisions),
            message=f"{len(proposed_revisions)} strategy revisions await a decision",
        ),
        _check(
            "open_strategy_signals",
            count=len(open_signals),
            refs=_refs("strategy_signal", open_signals),
            message=f"{len(open_signals)} strategy signals await a decision",
        ),
        _check(
            "deferred_work",
            count=len(deferred_work),
            refs=_refs("work_item", deferred_work),
            message=f"{len(deferred_work)} deferred work items require an accepted exception",
            waivable=True,
        ),
        _check(
            "active_execution_tasks",
            count=len(active_tasks),
            refs=_refs("task", active_tasks),
            message=f"{len(active_tasks)} execution tasks are still active",
        ),
        _check(
            "active_agent_runs",
            count=len(active_agents),
            refs=_refs("agent_execution", active_agents),
            message=f"{len(active_agents)} agent runs are still active",
        ),
        _check(
            "pending_approvals",
            count=len(pending_approvals),
            refs=_refs("approval", pending_approvals),
            message=f"{len(pending_approvals)} approvals await a decision",
        ),
        _check(
            "coverage_gaps",
            count=len(coverage_gaps),
            refs=_refs("coverage_item", [row.id for row in coverage_gaps]),
            message=f"{len(coverage_gaps)} coverage items require a gap decision",
            waivable=True,
        ),
    ]

    report = build_report_readiness(session, engagement=engagement)
    for report_check in sorted(report.checks, key=lambda item: item.key):
        if report_check.count <= 0:
            continue
        severity = "blocker" if report_check.level == "blocker" else report_check.level
        refs = _refs("finding", list(report_check.finding_ids))
        if not refs:
            refs = [CompletionRef(type="report_check", id=report_check.key)]
        checks.append(
            _check(
                f"report.{report_check.key}",
                count=report_check.count,
                refs=refs,
                message=report_check.message,
                severity=severity,
            )
        )

    candidates = [
        AcceptedGapCandidate(
            ref=CompletionRef(type="work_item", id=str(item_id)),
            key="deferred_work",
            message="Accept deferred work as an explicit completion exception",
        )
        for item_id in deferred_work
    ]
    candidates.extend(
        AcceptedGapCandidate(
            ref=CompletionRef(type="coverage_item", id=str(row.id)),
            key="coverage_gaps",
            message=(row.reason or f"{row.target_kind} {row.target_key}: {row.activity_category}"),
        )
        for row in coverage_gaps
    )

    facts = {
        "work_state": engagement.work_state.value,
        "work_state_version": engagement.work_state_version,
        "checks": [
            check.model_dump(mode="json") for check in sorted(checks, key=lambda item: item.key)
        ],
        "accepted_gap_candidates": [
            candidate.model_dump(mode="json")
            for candidate in sorted(candidates, key=lambda item: (item.ref.type, item.ref.id))
        ],
    }
    digest = hashlib.sha256(
        json.dumps(facts, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    hard_blocker = any(
        check.severity == "blocker" and check.count > 0 and not check.waivable for check in checks
    )
    waivable_blocker = any(
        check.severity == "blocker" and check.count > 0 and check.waivable for check in checks
    )
    return CompletionReadiness(
        work_state=engagement.work_state.value,
        work_state_version=engagement.work_state_version,
        ready=not hard_blocker and not waivable_blocker,
        readiness_hash=digest,
        checks=checks,
        accepted_gap_candidates=candidates,
        generated_at=datetime.now(tz=UTC),
    )


def validate_completion_exceptions(
    readiness: CompletionReadiness,
    accepted: list[dict[str, object]],
) -> None:
    """Require every current waivable blocker ref and reject foreign/stale refs."""
    candidate_keys = {
        (candidate.ref.type, candidate.ref.id) for candidate in readiness.accepted_gap_candidates
    }
    accepted_keys = {
        (str(item["ref"]["type"]), str(item["ref"]["id"]))  # type: ignore[index]
        for item in accepted
    }
    unknown = accepted_keys - candidate_keys
    missing = candidate_keys - accepted_keys
    if unknown:
        raise ValueError(f"exceptions are not present in current preflight: {sorted(unknown)}")
    if missing:
        raise ValueError(f"waivable blockers require explicit exceptions: {sorted(missing)}")

    hard = [
        check.key
        for check in readiness.checks
        if check.severity == "blocker" and check.count > 0 and not check.waivable
    ]
    if hard:
        raise ValueError(f"completion has unwaivable blockers: {', '.join(hard)}")
