"""Atomic, audited routing for analyst acceptance of typed suggestions."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents import TacticalAgent, TacticalRefusedExploit
from app.models import (
    ActorType,
    AgentTrigger,
    AuditLog,
    Engagement,
    EngagementObjective,
    EngagementStatus,
    EngagementStrategyRevision,
    EngagementWorkState,
    Finding,
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
    title = str(payload.get("title") or suggestion.title).strip()[:300]
    if not title:
        raise HTTPException(status_code=422, detail="work item title is required")
    criteria = payload.get("acceptance_criteria") or []
    if not isinstance(criteria, list) or not all(isinstance(value, str) for value in criteria):
        raise HTTPException(status_code=422, detail="acceptance criteria must be a string array")
    item = WorkItem(
        engagement_id=suggestion.engagement_id,
        objective_id=objective_id,
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

    # Tactical's lease policy and dispatcher commit internally. Stage the
    # suggestion decision, task link, and audit *before* entering Tactical so
    # its first commit cannot leave an open suggestion with a committed Task.
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
        suggestion.payload = {
            **(suggestion.payload or {}),
            "accepted_strategy_revision_id": str(result.strategy_revision.id),
        }
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
