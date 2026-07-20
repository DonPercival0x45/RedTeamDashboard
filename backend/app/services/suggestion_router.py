"""Atomic, audited routing for analyst acceptance of typed suggestions."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents import TacticalAgent, TacticalAlreadyScanned, TacticalRefusedExploit
from app.models import (
    ActorType,
    AgentTrigger,
    AuditLog,
    CoverageCategory,
    CoverageItem,
    CoverageStatus,
    Engagement,
    EngagementObjective,
    EngagementStatus,
    EngagementStrategyRevision,
    EngagementWorkState,
    Entity,
    Finding,
    ObjectivePriority,
    ObjectiveStatus,
    OwnerEligibility,
    ScopeItem,
    StrategyRevisionState,
    Suggestion,
    SuggestionKind,
    SuggestionStatus,
    Task,
    TaskKind,
    TaskStatus,
    WorkItem,
    WorkItemExecutor,
    WorkItemFinding,
    WorkItemFindingRelationship,
    WorkItemPriority,
    WorkItemStatus,
)
from app.orchestrator.tools import get_tool
from app.services.scope_matcher import evaluate_scope, infer_scope_kind


@dataclass(slots=True)
class SuggestionAcceptance:
    suggestion: Suggestion
    task: Task | None = None
    work_item: WorkItem | None = None
    strategy_revision: EngagementStrategyRevision | None = None
    dispatched: bool = False


def _mutable_engagement(session: Session, engagement_id: uuid.UUID) -> Engagement:
    engagement = session.execute(
        select(Engagement).where(Engagement.id == engagement_id).with_for_update()
    ).scalar_one_or_none()
    if engagement is None or engagement.status == EngagementStatus.flushed:
        raise HTTPException(status_code=404, detail="engagement not found")
    if engagement.status == EngagementStatus.archived:
        raise HTTPException(status_code=409, detail="archived engagement is read-only")
    if engagement.work_state == EngagementWorkState.completed:
        raise HTTPException(status_code=409, detail="completed engagement is read-only")
    return engagement


def _find_existing_work_item(
    session: Session,
    engagement_id: uuid.UUID,
    *,
    title: str,
    scope_item_id: uuid.UUID | None,
    entity_id: uuid.UUID | None,
    executor_type: WorkItemExecutor,
) -> WorkItem | None:
    """Return a work item with the same identity, if any.

    Keeps suggestion-accept and bootstrap seeding from stacking duplicate work
    items (same title + scope + entity + executor). The strategist dedups at
    proposal time; this is the materialization backstop.
    """
    return session.execute(
        select(WorkItem)
        .where(
            WorkItem.engagement_id == engagement_id,
            WorkItem.title == title,
            WorkItem.executor_type == executor_type,
            WorkItem.scope_item_id == scope_item_id
            if scope_item_id
            else WorkItem.scope_item_id.is_(None),
            WorkItem.entity_id == entity_id if entity_id else WorkItem.entity_id.is_(None),
        )
        .limit(1)
    ).scalar_one_or_none()


def _accept_work_item(
    session: Session,
    suggestion: Suggestion,
    *,
    user_id: uuid.UUID,
) -> WorkItem:
    envelope = suggestion.payload if isinstance(suggestion.payload, dict) else {}
    payload = envelope.get("work_item")
    if not isinstance(payload, dict) or envelope.get("schema_version") != 1:
        raise HTTPException(status_code=422, detail="invalid work-item suggestion payload")
    objective_id = payload.get("objective_id") or suggestion.objective_id
    if objective_id:
        try:
            objective_id = uuid.UUID(str(objective_id))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="invalid objective id") from exc
        objective = session.get(EngagementObjective, objective_id)
        if objective is None or objective.engagement_id != suggestion.engagement_id:
            raise HTTPException(status_code=422, detail="objective is not in this engagement")
    links = payload.get("finding_links") or []
    if not isinstance(links, list) or len(links) > 50:
        raise HTTPException(status_code=422, detail="finding links must contain at most 50 items")
    normalized: list[tuple[uuid.UUID, WorkItemFindingRelationship]] = []
    for raw in links:
        if not isinstance(raw, dict):
            raise HTTPException(status_code=422, detail="invalid finding link")
        try:
            finding_id = uuid.UUID(str(raw.get("finding_id")))
            relationship = WorkItemFindingRelationship(str(raw.get("relationship") or "related"))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="invalid finding link") from exc
        normalized.append((finding_id, relationship))
    if len(normalized) != len(set(normalized)):
        raise HTTPException(status_code=422, detail="duplicate finding relationship")
    if normalized:
        valid = set(
            session.execute(
                select(Finding.id).where(
                    Finding.engagement_id == suggestion.engagement_id,
                    Finding.deleted_at.is_(None),
                    Finding.id.in_([item[0] for item in normalized]),
                )
            ).scalars()
        )
        if valid != {item[0] for item in normalized}:
            raise HTTPException(
                status_code=422, detail="finding link crosses engagement or is unavailable"
            )
    try:
        priority = WorkItemPriority(str(payload.get("priority") or "medium"))
        executor = WorkItemExecutor(str(payload.get("executor_type") or "unassigned"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    scope_item_id = None
    raw_scope = payload.get("scope_item_id")
    if raw_scope:
        try:
            scope_item_id = uuid.UUID(str(raw_scope))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="invalid scope item id") from exc
        scope_item = session.get(ScopeItem, scope_item_id)
        if scope_item is None or scope_item.engagement_id != suggestion.engagement_id:
            raise HTTPException(status_code=422, detail="scope item is not in this engagement")
    entity_id = None
    raw_entity = payload.get("entity_id")
    if raw_entity:
        try:
            entity_id = uuid.UUID(str(raw_entity))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="invalid entity id") from exc
        linked_entity = session.get(Entity, entity_id)
        if linked_entity is None or linked_entity.engagement_id != suggestion.engagement_id:
            raise HTTPException(status_code=422, detail="entity is not in this engagement")
    title = str(payload.get("title") or suggestion.title).strip()[:300]
    if not title:
        raise HTTPException(status_code=422, detail="work item title is required")
    criteria = payload.get("acceptance_criteria") or []
    if not isinstance(criteria, list) or not all(isinstance(value, str) for value in criteria):
        raise HTTPException(status_code=422, detail="acceptance criteria must be a string array")
    existing = _find_existing_work_item(
        session,
        suggestion.engagement_id,
        title=title,
        scope_item_id=scope_item_id,
        entity_id=entity_id,
        executor_type=executor,
    )
    if existing is not None:
        # Don't stack a duplicate — link this suggestion to the existing work
        # item. (The strategist dedups at proposal time, but bootstrap-seeded
        # or pre-dedup items can still match an accepted suggestion.)
        suggestion.work_item_id = existing.id
        return existing
    item = WorkItem(
        engagement_id=suggestion.engagement_id,
        objective_id=objective_id,
        scope_item_id=scope_item_id,
        entity_id=entity_id,
        title=title,
        description=str(payload.get("description") or "")[:4000] or None,
        rationale=str(payload.get("rationale") or "")[:4000] or None,
        acceptance_criteria=criteria[:50],
        status=WorkItemStatus.ready,
        priority=priority,
        executor_type=executor,
        created_by_user_id=user_id,
    )
    session.add(item)
    session.flush()
    for finding_id, relationship in normalized:
        session.add(
            WorkItemFinding(
                work_item_id=item.id,
                finding_id=finding_id,
                relationship=relationship,
            )
        )
    suggestion.work_item_id = item.id
    return item


def _bootstrap_workspace_from_initial_strategy(
    session: Session,
    suggestion: Suggestion,
    *,
    user_id: uuid.UUID,
) -> dict[str, int]:
    """Populate first-pass objectives/work/coverage after accepting strategy.

    This runs only for the initial-strategy bootstrap path. The analyst has just
    accepted the strategy proposal, so these are committed starter records rather
    than hidden agent actions.
    """
    if session.execute(
        select(func.count(EngagementObjective.id)).where(
            EngagementObjective.engagement_id == suggestion.engagement_id
        )
    ).scalar_one():
        return {"objectives": 0, "work_items": 0, "coverage_items": 0}

    objectives = [
        EngagementObjective(
            engagement_id=suggestion.engagement_id,
            title="Validate highest-risk findings",
            description=(
                "Review the highest-severity findings for accuracy, scope, "
                "evidence, and reportability."
            ),
            success_criteria=(
                "High and critical findings have validation status, evidence "
                "notes, and reportability decisions."
            ),
            status=ObjectiveStatus.active,
            priority=ObjectivePriority.critical,
            display_order=10,
            created_by_user_id=user_id,
            is_bootstrap=True,
        ),
        EngagementObjective(
            engagement_id=suggestion.engagement_id,
            title="Confirm scope and coverage",
            description=(
                "Confirm declared scope, scanner/import coverage, and any "
                "accepted gaps before downstream work expands."
            ),
            success_criteria=(
                "Scope targets have coverage rows and documented status or "
                "accepted-gap rationale."
            ),
            status=ObjectiveStatus.active,
            priority=ObjectivePriority.high,
            display_order=20,
            created_by_user_id=user_id,
            is_bootstrap=True,
        ),
        EngagementObjective(
            engagement_id=suggestion.engagement_id,
            title="Prepare report-ready evidence",
            description=(
                "Organize evidence, validation outcomes, and client-safe "
                "reporting decisions."
            ),
            success_criteria=(
                "Reportable findings have evidence and unresolved gaps are "
                "documented before completion review."
            ),
            status=ObjectiveStatus.planned,
            priority=ObjectivePriority.medium,
            display_order=30,
            created_by_user_id=user_id,
            is_bootstrap=True,
        ),
    ]
    session.add_all(objectives)
    session.flush()
    validation_obj, coverage_obj, report_obj = objectives

    severity_rank = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}
    priority_by_severity = {
        "critical": WorkItemPriority.critical,
        "high": WorkItemPriority.high,
        "medium": WorkItemPriority.medium,
        "low": WorkItemPriority.low,
        "info": WorkItemPriority.low,
    }
    findings = list(
        session.execute(
            select(Finding)
            .where(Finding.engagement_id == suggestion.engagement_id, Finding.deleted_at.is_(None))
            .order_by(Finding.updated_at.desc())
        ).scalars()
    )
    findings = sorted(
        findings,
        key=lambda row: (severity_rank.get(row.severity.value, 0), row.updated_at),
        reverse=True,
    )[:5]
    work_count = 0
    for finding in findings:
        item = WorkItem(
            engagement_id=suggestion.engagement_id,
            objective_id=validation_obj.id,
            title=f"Review and validate: {finding.title}"[:300],
            description=(
                "Confirm finding validity, affected target, supporting evidence, "
                "and reportability."
            ),
            rationale=(
                "Seeded from the accepted initial strategy so high-value findings "
                "are reviewed first."
            ),
            acceptance_criteria=[
                "Validation status is updated.",
                "Evidence and analyst notes are sufficient for reporting decisions.",
                "Scope or exclusion rationale is documented if applicable.",
            ],
            status=WorkItemStatus.ready,
            priority=priority_by_severity.get(finding.severity.value, WorkItemPriority.medium),
            executor_type=WorkItemExecutor.analyst,
            created_by_user_id=user_id,
            is_bootstrap=True,
        )
        session.add(item)
        session.flush()
        session.add(
            WorkItemFinding(
                work_item_id=item.id,
                finding_id=finding.id,
                relationship=WorkItemFindingRelationship.primary,
            )
        )
        work_count += 1

    for objective, title, description, priority in [
        (
            coverage_obj,
            "Confirm coverage and accepted gaps",
            "Review scope, imported scanner coverage, and any missing coverage rows.",
            WorkItemPriority.high,
        ),
        (
            report_obj,
            "Prepare report readiness decisions",
            "Collect evidence and document decisions needed before completion review.",
            WorkItemPriority.medium,
        ),
    ]:
        session.add(
            WorkItem(
                engagement_id=suggestion.engagement_id,
                objective_id=objective.id,
                title=title,
                description=description,
                rationale="Seeded from the accepted initial strategy.",
                acceptance_criteria=[],
                status=WorkItemStatus.ready,
                priority=priority,
                executor_type=WorkItemExecutor.analyst,
                created_by_user_id=user_id,
                is_bootstrap=True,
            )
        )
        work_count += 1

    coverage_count = 0
    scope_items = list(
        session.execute(
            select(ScopeItem).where(ScopeItem.engagement_id == suggestion.engagement_id).limit(50)
        ).scalars()
    )
    # Sparse-state discovery: if no findings were seeded for review, seed work
    # that targets declared scope to *generate* findings, so a fresh engagement
    # has an actionable queue. Each item points at a concrete scope_item and is
    # executor_type=finding_agent so it can be dispatched to an agent run.
    if not findings:
        seen_scope_values: set[str] = set()
        for scope in scope_items[:5]:
            if scope.value in seen_scope_values:
                continue
            seen_scope_values.add(scope.value)
            enumerate_title = f"Enumerate and triage {scope.value}"[:300]
            # Skip if an identical enumerate item already exists (robust against
            # a re-bootstrap or duplicate scope entries).
            if (
                _find_existing_work_item(
                    session,
                    suggestion.engagement_id,
                    title=enumerate_title,
                    scope_item_id=scope.id,
                    entity_id=None,
                    executor_type=WorkItemExecutor.finding_agent,
                )
                is not None
            ):
                continue
            session.add(
                WorkItem(
                    engagement_id=suggestion.engagement_id,
                    objective_id=validation_obj.id,
                    scope_item_id=scope.id,
                    title=enumerate_title,
                    description=(
                        "No findings exist yet for this in-scope target. Run "
                        "reconnaissance to discover surfaces and generate initial "
                        "findings for analyst validation. No exploitation."
                    ),
                    rationale=(
                        "Seeded from the accepted initial strategy because no "
                        "findings exist yet; prioritize generating findings against "
                        "declared scope."
                    ),
                    acceptance_criteria=[
                        "At least one finding or observation is recorded for this target."
                    ],
                    status=WorkItemStatus.ready,
                    priority=WorkItemPriority.high,
                    executor_type=WorkItemExecutor.finding_agent,
                    created_by_user_id=user_id,
                    is_bootstrap=True,
                )
            )
            work_count += 1
    for scope in scope_items:
        for category in (CoverageCategory.scope_review, CoverageCategory.finding_review):
            session.add(
                CoverageItem(
                    engagement_id=suggestion.engagement_id,
                    objective_id=coverage_obj.id,
                    scope_item_id=scope.id,
                    target_kind=scope.kind.value,
                    target_key=scope.value,
                    activity_category=category,
                    status=CoverageStatus.planned,
                    supporting_refs=[],
                    reason="Seeded from accepted initial strategy.",
                    is_bootstrap=True,
                )
            )
            coverage_count += 1

    return {
        "objectives": len(objectives),
        "work_items": work_count,
        "coverage_items": coverage_count,
    }


def _accept_strategy_revision(
    session: Session,
    suggestion: Suggestion,
    *,
    user_id: uuid.UUID,
) -> EngagementStrategyRevision:
    envelope = suggestion.payload if isinstance(suggestion.payload, dict) else {}
    payload = envelope.get("strategy_revision")
    if not isinstance(payload, dict) or envelope.get("schema_version") != 1:
        raise HTTPException(status_code=422, detail="invalid strategy-revision suggestion payload")
    current = session.execute(
        select(EngagementStrategyRevision)
        .where(
            EngagementStrategyRevision.engagement_id == suggestion.engagement_id,
            EngagementStrategyRevision.state == StrategyRevisionState.current,
        )
        .with_for_update()
    ).scalar_one_or_none()
    based_on = payload.get("based_on_revision_id")
    try:
        based_on_id = uuid.UUID(str(based_on)) if based_on else None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="invalid based_on_revision_id") from exc
    if (current.id if current else None) != based_on_id:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "strategy changed since proposal",
                "current_revision_id": str(current.id) if current else None,
            },
        )
    version = (
        int(
            session.execute(
                select(func.coalesce(func.max(EngagementStrategyRevision.version), 0)).where(
                    EngagementStrategyRevision.engagement_id == suggestion.engagement_id
                )
            ).scalar_one()
        )
        + 1
    )
    if current is not None:
        current.state = StrategyRevisionState.superseded
        current.decided_by_user_id = user_id
        current.decided_at = datetime.now(tz=UTC)
    revision = EngagementStrategyRevision(
        engagement_id=suggestion.engagement_id,
        version=version,
        state=StrategyRevisionState.current,
        based_on_revision_id=based_on_id,
        summary=str(payload.get("summary") or suggestion.title)[:300] or None,
        body=str(payload.get("body") or ""),
        structured=payload.get("structured") if isinstance(payload.get("structured"), dict) else {},
        proposal_reason=str(payload.get("reason") or suggestion.body or "") or None,
        created_by_user_id=user_id,
        decided_by_user_id=user_id,
        decided_at=datetime.now(tz=UTC),
    )
    if not revision.body.strip():
        raise HTTPException(status_code=422, detail="strategy body is required")
    session.add(revision)
    session.flush()
    return revision


def _accept_execution_task(
    session: Session,
    redis_client: Any,
    suggestion: Suggestion,
    *,
    user_id: uuid.UUID,
) -> tuple[Task, bool]:
    payload = dict(suggestion.payload or {})
    linked: WorkItem | None = None
    if suggestion.work_item_id is not None:
        linked = session.execute(
            select(WorkItem).where(WorkItem.id == suggestion.work_item_id).with_for_update()
        ).scalar_one_or_none()
        if linked is None or linked.engagement_id != suggestion.engagement_id:
            raise HTTPException(status_code=422, detail="linked work item is invalid")
        if linked.status in {WorkItemStatus.completed, WorkItemStatus.cancelled}:
            raise HTTPException(status_code=409, detail="linked work item is terminal")
        expected = payload.get("expected_work_item_version")
        if expected is not None and linked.row_version != int(expected):
            raise HTTPException(status_code=409, detail="linked work item changed since proposal")
    try:
        kind = TaskKind(str(payload.get("task_kind") or "enum"))
        owner = OwnerEligibility(str(payload.get("owner_eligibility") or "agent"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    tool = str(payload.get("tool") or "").strip()
    target = str(payload.get("target") or "").strip()
    if kind == TaskKind.exploit:
        raise HTTPException(
            status_code=422, detail="analyst-only execution cannot be dispatched by suggestion"
        )
    if not tool or get_tool(tool) is None:
        raise HTTPException(status_code=422, detail="unknown execution tool")
    if not target:
        raise HTTPException(status_code=422, detail="execution target is required")
    scope = list(
        session.execute(
            select(ScopeItem).where(ScopeItem.engagement_id == suggestion.engagement_id)
        ).scalars()
    )
    match = evaluate_scope(target, infer_scope_kind(target), scope)
    if not match.allowed:
        raise HTTPException(
            status_code=422, detail=f"target is outside current scope: {match.reason}"
        )
    task = Task(
        engagement_id=suggestion.engagement_id,
        finding_id=suggestion.finding_id,
        work_item_id=linked.id if linked else None,
        title=suggestion.title,
        kind=kind,
        owner_eligibility=owner,
        status=TaskStatus.pending,
        payload=payload,
    )
    session.add(task)
    session.flush()
    suggestion.task_id = task.id
    should_dispatch = owner in {OwnerEligibility.agent, OwnerEligibility.either}

    # Tactical is caller-transaction-owned: acceptance, Task, policy metadata,
    # lease, and outbox are staged together and committed only by this route.
    suggestion.status = SuggestionStatus.accepted
    suggestion.decided_by = user_id
    suggestion.decided_at = datetime.now(tz=UTC)
    session.add(
        AuditLog(
            engagement_id=suggestion.engagement_id,
            actor_type=ActorType.user,
            actor_id=str(user_id),
            event_type="suggestion.accepted",
            payload={
                "suggestion_id": str(suggestion.id),
                "kind": suggestion.kind.value,
                "task_id": str(task.id),
                "work_item_id": str(linked.id) if linked else None,
                "strategy_revision_id": None,
                "dispatched": should_dispatch,
            },
        )
    )
    dispatched = False
    if should_dispatch:
        try:
            TacticalAgent(redis_client).dispatch(
                session,
                task=task,
                trigger=AgentTrigger.manual,
                acting_user_id=user_id,
            )
            dispatched = True
        except TacticalAlreadyScanned as dedup:
            # Already scanned recently — mark the task done against the prior
            # run instead of re-dispatching (the duplicate-runs guardrail).
            task.status = TaskStatus.completed
            task.completed_at = datetime.now(tz=UTC)
            task.run_id = dedup.prior_execution_id
            dispatched = False
        except TacticalRefusedExploit:
            dispatched = False
    return task, dispatched


def accept_suggestion(
    session: Session,
    redis_client: Any,
    *,
    suggestion_id: uuid.UUID,
    user_id: uuid.UUID,
    commit: bool = True,
) -> SuggestionAcceptance:
    initial = session.get(Suggestion, suggestion_id)
    if initial is None:
        raise HTTPException(status_code=404, detail="suggestion not found")
    locked_engagement = _mutable_engagement(session, initial.engagement_id)
    suggestion = session.execute(
        select(Suggestion)
        .where(Suggestion.id == suggestion_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if suggestion is None or suggestion.engagement_id != locked_engagement.id:
        raise HTTPException(status_code=404, detail="suggestion not found")
    if suggestion.status == SuggestionStatus.accepted:
        accepted_revision_id = (suggestion.payload or {}).get("accepted_strategy_revision_id")
        try:
            accepted_revision_uuid = (
                uuid.UUID(str(accepted_revision_id)) if accepted_revision_id else None
            )
        except ValueError:
            accepted_revision_uuid = None
        accepted_task = session.get(Task, suggestion.task_id) if suggestion.task_id else None
        return SuggestionAcceptance(
            suggestion=suggestion,
            task=accepted_task,
            work_item=session.get(WorkItem, suggestion.work_item_id)
            if suggestion.work_item_id
            else None,
            strategy_revision=(
                session.get(EngagementStrategyRevision, accepted_revision_uuid)
                if accepted_revision_uuid
                else None
            ),
            dispatched=(
                accepted_task is not None
                and accepted_task.status
                in {TaskStatus.dispatched, TaskStatus.running, TaskStatus.completed}
            ),
        )
    if suggestion.status != SuggestionStatus.open:
        raise HTTPException(status_code=409, detail=f"suggestion is {suggestion.status.value}")
    result = SuggestionAcceptance(suggestion=suggestion)
    task_route = suggestion.kind == SuggestionKind.task
    if suggestion.kind == SuggestionKind.work_item:
        result.work_item = _accept_work_item(session, suggestion, user_id=user_id)
    elif suggestion.kind == SuggestionKind.strategy_revision:
        result.strategy_revision = _accept_strategy_revision(session, suggestion, user_id=user_id)
        payload = dict(suggestion.payload or {})
        payload["accepted_strategy_revision_id"] = str(result.strategy_revision.id)
        revision_payload = payload.get("strategy_revision")
        structured = (
            revision_payload.get("structured", {}) if isinstance(revision_payload, dict) else {}
        )
        if isinstance(structured, dict) and structured.get("source") in {
            "sectioned_ai_generation",
            "deterministic_fallback",
        }:
            payload["workspace_bootstrap"] = _bootstrap_workspace_from_initial_strategy(
                session, suggestion, user_id=user_id
            )
        suggestion.payload = payload
    elif suggestion.kind == SuggestionKind.task:
        result.task, result.dispatched = _accept_execution_task(
            session, redis_client, suggestion, user_id=user_id
        )
    if not task_route:
        suggestion.status = SuggestionStatus.accepted
        suggestion.decided_by = user_id
        suggestion.decided_at = datetime.now(tz=UTC)
        session.add(
            AuditLog(
                engagement_id=suggestion.engagement_id,
                actor_type=ActorType.user,
                actor_id=str(user_id),
                event_type="suggestion.accepted",
                payload={
                    "suggestion_id": str(suggestion.id),
                    "kind": suggestion.kind.value,
                    "task_id": None,
                    "work_item_id": (str(result.work_item.id) if result.work_item else None),
                    "strategy_revision_id": (
                        str(result.strategy_revision.id) if result.strategy_revision else None
                    ),
                    "dispatched": False,
                },
            )
        )
    if commit:
        session.commit()
        session.refresh(suggestion)
    return result
