"""Inert execution proposals linked to committed WorkItems."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.api.deps import CurrentNonGuestUser, DbSession
from app.models import (
    ActorType,
    AgentName,
    AuditLog,
    Engagement,
    EngagementStatus,
    EngagementWorkState,
    Finding,
    ScopeItem,
    Suggestion,
    SuggestionKind,
    SuggestionStatus,
    TaskKind,
    WorkItem,
    WorkItemStatus,
)
from app.orchestrator.tools import get_tool
from app.schemas.orchestrator import SuggestionRead
from app.schemas.strategy_suggestion import (
    ExecutionSuggestionCreate,
    ExecutionSuggestionResponse,
)
from app.services.scope_matcher import evaluate_scope, infer_scope_kind

router = APIRouter()


@router.post(
    "/work-items/{work_item_id}/execution-suggestions",
    response_model=ExecutionSuggestionResponse,
    status_code=201,
)
def create_execution_suggestion(
    work_item_id: uuid.UUID,
    body: ExecutionSuggestionCreate,
    session: DbSession,
    user: CurrentNonGuestUser,
) -> ExecutionSuggestionResponse:
    work_item = session.execute(
        select(WorkItem).where(WorkItem.id == work_item_id).with_for_update()
    ).scalar_one_or_none()
    if work_item is None:
        raise HTTPException(status_code=404, detail="work item not found")
    engagement = session.get(Engagement, work_item.engagement_id)
    if engagement is None or engagement.status == EngagementStatus.flushed:
        raise HTTPException(status_code=404, detail="engagement not found")
    if engagement.status == EngagementStatus.archived:
        raise HTTPException(status_code=409, detail="archived engagement is read-only")
    if engagement.work_state == EngagementWorkState.completed:
        raise HTTPException(status_code=409, detail="completed engagement is read-only")
    if work_item.status in {WorkItemStatus.completed, WorkItemStatus.cancelled}:
        raise HTTPException(status_code=409, detail="terminal work item cannot launch execution")
    if work_item.row_version != body.expected_work_item_version:
        raise HTTPException(
            status_code=409, detail="work item changed since the action was composed"
        )
    existing = session.execute(
        select(Suggestion).where(
            Suggestion.engagement_id == work_item.engagement_id,
            Suggestion.kind == SuggestionKind.task,
            Suggestion.work_item_id == work_item.id,
            Suggestion.proposal_key == body.idempotency_key,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return ExecutionSuggestionResponse(
            suggestion=SuggestionRead.model_validate(existing),
            scope_reason=str((existing.payload or {}).get("scope_reason") or "already validated"),
        )
    if body.task_kind not in {TaskKind.scan.value, TaskKind.enum.value}:
        raise HTTPException(status_code=422, detail="only scan or enum tasks may be proposed")
    tool = get_tool(body.tool)
    if tool is None:
        raise HTTPException(status_code=422, detail="unknown execution tool")
    finding_id = body.finding_id
    if finding_id is not None:
        finding = session.get(Finding, finding_id)
        if (
            finding is None
            or finding.engagement_id != work_item.engagement_id
            or finding.deleted_at is not None
        ):
            raise HTTPException(status_code=422, detail="finding is not in this engagement")
    scope_items = list(
        session.execute(
            select(ScopeItem).where(ScopeItem.engagement_id == work_item.engagement_id)
        ).scalars()
    )
    match = evaluate_scope(body.target, infer_scope_kind(body.target), scope_items)
    if not match.allowed:
        raise HTTPException(
            status_code=422, detail=f"target is outside current scope: {match.reason}"
        )
    suggestion = Suggestion(
        engagement_id=work_item.engagement_id,
        finding_id=finding_id,
        work_item_id=work_item.id,
        title=body.title,
        body=f"Proposed execution via {body.tool} for committed work item {work_item.title}",
        kind=SuggestionKind.task,
        payload={
            "schema_version": 1,
            "source": "work_item_execution_suggestion",
            "tool": body.tool,
            "target": body.target,
            "task_kind": body.task_kind,
            "owner_eligibility": "agent",
            "expected_work_item_version": body.expected_work_item_version,
            "idempotency_key": body.idempotency_key,
            "scope_reason": match.reason,
        },
        status=SuggestionStatus.open,
        created_by_agent=AgentName.engagement_strategist,
        proposal_key=body.idempotency_key,
    )
    session.add(suggestion)
    session.flush()
    session.add(
        AuditLog(
            engagement_id=work_item.engagement_id,
            actor_type=ActorType.user,
            actor_id=str(user.id),
            event_type="work_item.execution_suggestion_created",
            payload={
                "work_item_id": str(work_item.id),
                "suggestion_id": str(suggestion.id),
                "tool": body.tool,
                "target": body.target,
                "scope_reason": match.reason,
                "created_at": datetime.now(tz=UTC).isoformat(),
            },
        )
    )
    session.commit()
    session.refresh(suggestion)
    return ExecutionSuggestionResponse(
        suggestion=SuggestionRead.model_validate(suggestion),
        scope_reason=match.reason,
    )
