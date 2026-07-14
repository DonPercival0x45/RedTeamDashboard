"""Manual, auditable Engagement Strategy and work-ledger API.

This module deliberately contains no LLM or Tactical dispatch path. Agents may
later propose records, but only authenticated analysts mutate authoritative
strategy/work state through these endpoints.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import CurrentNonGuestUser, CurrentUser, DbSession
from app.models import (
    ActorType,
    AgentExecution,
    AuditLog,
    CoverageItem,
    CoverageStatus,
    Engagement,
    EngagementCheckpoint,
    EngagementObjective,
    EngagementStatus,
    EngagementStrategyRevision,
    EngagementWorkState,
    Finding,
    ObjectiveStatus,
    StrategyRevisionState,
    StrategySignal,
    StrategySignalStatus,
    Suggestion,
    SuggestionKind,
    SuggestionStatus,
    WorkItem,
    WorkItemExecutor,
    WorkItemFinding,
    WorkItemPriority,
    WorkItemResult,
    WorkItemResultState,
    WorkItemStatus,
)
from app.schemas.strategy import (
    CheckpointCreate,
    CheckpointRead,
    ObjectiveCreate,
    ObjectiveRead,
    ObjectiveUpdate,
    ResultDecisionResponse,
    ResumeResponse,
    SignalCreate,
    SignalDecision,
    StrategyRevisionCreate,
    StrategyRevisionDecision,
    StrategyRevisionRead,
    StrategySignalRead,
    VersionedReason,
    WorkItemBlock,
    WorkItemCreate,
    WorkItemFindingAdd,
    WorkItemFindingRead,
    WorkItemRead,
    WorkItemResolve,
    WorkItemResultAccept,
    WorkItemResultCreate,
    WorkItemResultRead,
    WorkItemResultReject,
    WorkItemRollup,
    WorkItemRollupBucket,
    WorkItemUpdate,
)
from app.services.report_readiness import build_report_readiness

router = APIRouter(tags=["strategy"])

_TERMINAL_WORK = {WorkItemStatus.completed, WorkItemStatus.cancelled}
_REMAINING_WORK = {
    WorkItemStatus.ready,
    WorkItemStatus.in_progress,
    WorkItemStatus.blocked,
}
_TERMINAL_OBJECTIVES = {ObjectiveStatus.completed, ObjectiveStatus.cancelled}


def _engagement(session: Session, slug: str) -> Engagement:
    row = session.execute(select(Engagement).where(Engagement.slug == slug)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="engagement not found")
    return row


def _ensure_mutable(engagement: Engagement) -> None:
    if engagement.status in (EngagementStatus.archived, EngagementStatus.flushed):
        raise HTTPException(
            status_code=409,
            detail=f"engagement is {engagement.status.value}; strategy is read-only",
        )
    if engagement.work_state == EngagementWorkState.completed:
        raise HTTPException(
            status_code=409,
            detail="engagement work is completed; reopen it before changing strategy",
        )


def _audit(
    session: Session,
    engagement_id: uuid.UUID,
    user_id: uuid.UUID,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    session.add(
        AuditLog(
            engagement_id=engagement_id,
            actor_type=ActorType.user,
            actor_id=str(user_id),
            event_type=event_type,
            payload=payload,
        )
    )


def _current_revision(
    session: Session, engagement_id: uuid.UUID
) -> EngagementStrategyRevision | None:
    return session.execute(
        select(EngagementStrategyRevision).where(
            EngagementStrategyRevision.engagement_id == engagement_id,
            EngagementStrategyRevision.state == StrategyRevisionState.current,
        )
    ).scalar_one_or_none()


def _check_revision_base(
    current: EngagementStrategyRevision | None, expected: uuid.UUID | None
) -> None:
    actual = current.id if current else None
    if actual != expected:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "stale_strategy_revision",
                "message": "strategy changed; refresh before deciding",
                "current_revision_id": str(actual) if actual else None,
            },
        )


def _next_revision_version(session: Session, engagement_id: uuid.UUID) -> int:
    latest = session.execute(
        select(func.max(EngagementStrategyRevision.version)).where(
            EngagementStrategyRevision.engagement_id == engagement_id
        )
    ).scalar_one()
    return int(latest or 0) + 1


def _objective_for_engagement(
    session: Session, engagement_id: uuid.UUID, objective_id: uuid.UUID | None
) -> EngagementObjective | None:
    if objective_id is None:
        return None
    row = session.get(EngagementObjective, objective_id)
    if row is None or row.engagement_id != engagement_id:
        raise HTTPException(status_code=400, detail="objective does not belong to engagement")
    return row


def _finding_for_engagement(
    session: Session, engagement_id: uuid.UUID, finding_id: uuid.UUID
) -> Finding:
    row = session.get(Finding, finding_id)
    if row is None or row.engagement_id != engagement_id or row.deleted_at is not None:
        raise HTTPException(status_code=400, detail="finding does not belong to engagement")
    return row


def _work_item_locked(session: Session, work_item_id: uuid.UUID) -> WorkItem:
    row = session.execute(
        select(WorkItem).where(WorkItem.id == work_item_id).with_for_update()
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="work item not found")
    return row


def _check_version(row: Any, expected: int) -> None:
    if row.row_version != expected:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "stale_row_version",
                "message": "record changed; refresh and retry",
                "current_row_version": row.row_version,
            },
        )


def _links(session: Session, work_item_id: uuid.UUID) -> list[WorkItemFinding]:
    return list(
        session.execute(
            select(WorkItemFinding)
            .where(WorkItemFinding.work_item_id == work_item_id)
            .order_by(WorkItemFinding.created_at)
        ).scalars()
    )


def _work_read(session: Session, row: WorkItem) -> WorkItemRead:
    base = WorkItemRead.model_validate(row)
    base.finding_links = [
        WorkItemFindingRead.model_validate(link) for link in _links(session, row.id)
    ]
    return base


def _work_engagement(session: Session, row: WorkItem) -> Engagement:
    eng = session.get(Engagement, row.engagement_id)
    if eng is None:
        raise HTTPException(status_code=404, detail="engagement not found")
    return eng


def _rollup(session: Session, engagement_id: uuid.UUID) -> WorkItemRollup:
    work = list(
        session.execute(select(WorkItem).where(WorkItem.engagement_id == engagement_id)).scalars()
    )
    links = list(
        session.execute(
            select(WorkItemFinding)
            .join(WorkItem, WorkItem.id == WorkItemFinding.work_item_id)
            .where(WorkItem.engagement_id == engagement_id)
        ).scalars()
    )
    proposal_rows = list(
        session.execute(
            select(Suggestion).where(
                Suggestion.engagement_id == engagement_id,
                Suggestion.status == SuggestionStatus.open,
                Suggestion.kind == SuggestionKind.work_item,
            )
        ).scalars()
    )

    def bucket(rows: list[WorkItem], proposals: int = 0) -> WorkItemRollupBucket:
        return WorkItemRollupBucket(
            remaining=sum(row.status in _REMAINING_WORK for row in rows),
            blocked=sum(row.status == WorkItemStatus.blocked for row in rows),
            deferred=sum(row.status == WorkItemStatus.deferred for row in rows),
            proposals=proposals,
        )

    by_id = {row.id: row for row in work}
    by_finding_work_ids: dict[uuid.UUID, set[uuid.UUID]] = {}
    for link in links:
        if link.work_item_id in by_id:
            by_finding_work_ids.setdefault(link.finding_id, set()).add(link.work_item_id)
    by_finding_rows = {
        finding_id: [by_id[work_id] for work_id in work_ids]
        for finding_id, work_ids in by_finding_work_ids.items()
    }
    proposal_counts: dict[uuid.UUID, int] = {}
    for suggestion in proposal_rows:
        finding_ids: set[uuid.UUID] = set()
        payload = suggestion.payload or {}
        work_payload = payload.get("work_item") if isinstance(payload, dict) else None
        raw_links = work_payload.get("finding_links", []) if isinstance(work_payload, dict) else []
        for raw_link in raw_links:
            if not isinstance(raw_link, dict):
                continue
            try:
                finding_ids.add(uuid.UUID(str(raw_link.get("finding_id"))))
            except (TypeError, ValueError):
                continue
        if suggestion.finding_id:
            finding_ids.add(suggestion.finding_id)
        for finding_id in finding_ids:
            proposal_counts[finding_id] = proposal_counts.get(finding_id, 0) + 1
    finding_ids = set(by_finding_rows) | set(proposal_counts)
    return WorkItemRollup(
        by_finding={
            str(fid): bucket(by_finding_rows.get(fid, []), proposal_counts.get(fid, 0))
            for fid in finding_ids
        },
        engagement=bucket(work, len(proposal_rows)),
    )


# Strategy revisions ---------------------------------------------------------


@router.get("/engagements/{slug}/strategy", response_model=StrategyRevisionRead | None)
def get_current_strategy(slug: str, session: DbSession, _user: CurrentUser):
    return _current_revision(session, _engagement(session, slug).id)


@router.get("/engagements/{slug}/strategy/revisions", response_model=list[StrategyRevisionRead])
def list_strategy_revisions(slug: str, session: DbSession, _user: CurrentUser):
    eng = _engagement(session, slug)
    return list(
        session.execute(
            select(EngagementStrategyRevision)
            .where(EngagementStrategyRevision.engagement_id == eng.id)
            .order_by(EngagementStrategyRevision.version.desc())
        ).scalars()
    )


@router.post(
    "/engagements/{slug}/strategy/revisions",
    response_model=StrategyRevisionRead,
    status_code=status.HTTP_201_CREATED,
)
def create_strategy_revision(
    slug: str, body: StrategyRevisionCreate, session: DbSession, user: CurrentNonGuestUser
):
    eng = _engagement(session, slug)
    _ensure_mutable(eng)
    if body.state not in (
        StrategyRevisionState.draft,
        StrategyRevisionState.proposed,
        StrategyRevisionState.current,
    ):
        raise HTTPException(
            status_code=400,
            detail="new revisions must be draft, proposed, or current",
        )
    # Direct analyst edits may create the next current revision in one audited
    # transaction. Lock history so two analysts cannot silently win.
    revisions = list(
        session.execute(
            select(EngagementStrategyRevision)
            .where(EngagementStrategyRevision.engagement_id == eng.id)
            .order_by(EngagementStrategyRevision.version)
            .with_for_update()
        ).scalars()
    )
    current = next(
        (row for row in revisions if row.state == StrategyRevisionState.current),
        None,
    )
    _check_revision_base(current, body.based_on_revision_id)
    now = datetime.now(tz=UTC)
    row = EngagementStrategyRevision(
        engagement_id=eng.id,
        version=max((item.version for item in revisions), default=0) + 1,
        state=body.state,
        based_on_revision_id=body.based_on_revision_id,
        summary=body.summary,
        body=body.body,
        structured=body.structured,
        created_by_user_id=user.id,
        proposal_reason=body.proposal_reason,
        decided_by_user_id=user.id if body.state == StrategyRevisionState.current else None,
        decided_at=now if body.state == StrategyRevisionState.current else None,
    )
    if body.state == StrategyRevisionState.current and current:
        current.state = StrategyRevisionState.superseded
        current.decided_by_user_id = user.id
        current.decided_at = now
        session.flush()
    session.add(row)
    session.flush()
    event_type = (
        "strategy.revision_proposed"
        if body.state == StrategyRevisionState.proposed
        else "strategy.revision_accepted"
        if body.state == StrategyRevisionState.current and current is not None
        else "strategy.created"
    )
    _audit(
        session,
        eng.id,
        user.id,
        event_type,
        {
            "revision_id": str(row.id),
            "version": row.version,
            "state": row.state.value,
            "direct_edit": body.state == StrategyRevisionState.current,
            "superseded_id": str(current.id)
            if body.state == StrategyRevisionState.current and current
            else None,
        },
    )
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=409, detail="strategy revision was created concurrently"
        ) from exc
    session.refresh(row)
    return row


def _revision_decision_context(
    session: Session, slug: str, revision_id: uuid.UUID, expected: uuid.UUID | None
) -> tuple[Engagement, EngagementStrategyRevision, EngagementStrategyRevision | None]:
    eng = _engagement(session, slug)
    _ensure_mutable(eng)
    # Lock all revisions for the engagement in version order. This serializes
    # current-revision decisions even when there is not a current row yet.
    revisions = list(
        session.execute(
            select(EngagementStrategyRevision)
            .where(EngagementStrategyRevision.engagement_id == eng.id)
            .order_by(EngagementStrategyRevision.version)
            .with_for_update()
        ).scalars()
    )
    target = next((row for row in revisions if row.id == revision_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail="strategy revision not found")
    current = next((row for row in revisions if row.state == StrategyRevisionState.current), None)
    _check_revision_base(current, expected)
    return eng, target, current


@router.post(
    "/engagements/{slug}/strategy/revisions/{revision_id}/accept",
    response_model=StrategyRevisionRead,
)
def accept_strategy_revision(
    slug: str,
    revision_id: uuid.UUID,
    body: StrategyRevisionDecision,
    session: DbSession,
    user: CurrentNonGuestUser,
):
    eng, target, current = _revision_decision_context(
        session, slug, revision_id, body.based_on_revision_id
    )
    if target.state == StrategyRevisionState.current:
        return target
    if target.state not in (StrategyRevisionState.draft, StrategyRevisionState.proposed):
        raise HTTPException(status_code=409, detail=f"revision is {target.state.value}")
    if target.based_on_revision_id != (current.id if current else None):
        raise HTTPException(status_code=409, detail="revision was proposed from a stale strategy")
    now = datetime.now(tz=UTC)
    if current:
        current.state = StrategyRevisionState.superseded
        current.decided_at = now
        current.decided_by_user_id = user.id
    target.state = StrategyRevisionState.current
    target.decided_at = now
    target.decided_by_user_id = user.id
    _audit(
        session,
        eng.id,
        user.id,
        "strategy.revision_accepted",
        {
            "revision_id": str(target.id),
            "version": target.version,
            "superseded_id": str(current.id) if current else None,
        },
    )
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="strategy changed concurrently") from exc
    session.refresh(target)
    return target


@router.post(
    "/engagements/{slug}/strategy/revisions/{revision_id}/reject",
    response_model=StrategyRevisionRead,
)
def reject_strategy_revision(
    slug: str,
    revision_id: uuid.UUID,
    body: StrategyRevisionDecision,
    session: DbSession,
    user: CurrentNonGuestUser,
):
    eng, target, _current = _revision_decision_context(
        session, slug, revision_id, body.based_on_revision_id
    )
    if target.state == StrategyRevisionState.rejected:
        return target
    if target.state not in (StrategyRevisionState.draft, StrategyRevisionState.proposed):
        raise HTTPException(status_code=409, detail=f"revision is {target.state.value}")
    target.state = StrategyRevisionState.rejected
    target.decided_at = datetime.now(tz=UTC)
    target.decided_by_user_id = user.id
    if body.reason:
        target.proposal_reason = body.reason
    _audit(
        session,
        eng.id,
        user.id,
        "strategy.revision_rejected",
        {"revision_id": str(target.id), "reason": body.reason},
    )
    session.commit()
    session.refresh(target)
    return target


@router.post(
    "/engagements/{slug}/strategy/revisions/{revision_id}/restore",
    response_model=StrategyRevisionRead,
    status_code=status.HTTP_201_CREATED,
)
def restore_strategy_revision(
    slug: str,
    revision_id: uuid.UUID,
    body: StrategyRevisionDecision,
    session: DbSession,
    user: CurrentNonGuestUser,
):
    eng, source, current = _revision_decision_context(
        session, slug, revision_id, body.based_on_revision_id
    )
    if source.state == StrategyRevisionState.current:
        return source
    now = datetime.now(tz=UTC)
    restored = EngagementStrategyRevision(
        engagement_id=eng.id,
        version=_next_revision_version(session, eng.id),
        state=StrategyRevisionState.current,
        based_on_revision_id=current.id if current else None,
        summary=source.summary,
        body=source.body,
        structured=dict(source.structured or {}),
        created_by_user_id=user.id,
        proposal_reason=body.reason or f"Restored revision {source.version}",
        decided_by_user_id=user.id,
        decided_at=now,
    )
    if current:
        current.state = StrategyRevisionState.superseded
        current.decided_by_user_id = user.id
        current.decided_at = now
    session.add(restored)
    session.flush()
    _audit(
        session,
        eng.id,
        user.id,
        "strategy.revision_restored",
        {
            "revision_id": str(restored.id),
            "restored_from_id": str(source.id),
            "superseded_id": str(current.id) if current else None,
        },
    )
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="strategy changed concurrently") from exc
    session.refresh(restored)
    return restored


# Objectives ----------------------------------------------------------------


@router.get("/engagements/{slug}/objectives", response_model=list[ObjectiveRead])
def list_objectives(slug: str, session: DbSession, _user: CurrentUser):
    eng = _engagement(session, slug)
    return list(
        session.execute(
            select(EngagementObjective)
            .where(EngagementObjective.engagement_id == eng.id)
            .order_by(EngagementObjective.display_order, EngagementObjective.created_at)
        ).scalars()
    )


@router.post("/engagements/{slug}/objectives", response_model=ObjectiveRead, status_code=201)
def create_objective(
    slug: str, body: ObjectiveCreate, session: DbSession, user: CurrentNonGuestUser
):
    eng = _engagement(session, slug)
    _ensure_mutable(eng)
    if body.status in _TERMINAL_OBJECTIVES:
        raise HTTPException(status_code=400, detail="new objectives cannot be terminal")
    row = EngagementObjective(engagement_id=eng.id, created_by_user_id=user.id, **body.model_dump())
    session.add(row)
    session.flush()
    _audit(
        session,
        eng.id,
        user.id,
        "objective.created",
        {"objective_id": str(row.id), "title": row.title},
    )
    session.commit()
    session.refresh(row)
    return row


def _objective_locked(
    session: Session, slug: str, objective_id: uuid.UUID
) -> tuple[Engagement, EngagementObjective]:
    eng = _engagement(session, slug)
    _ensure_mutable(eng)
    row = session.execute(
        select(EngagementObjective)
        .where(EngagementObjective.id == objective_id, EngagementObjective.engagement_id == eng.id)
        .with_for_update()
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="objective not found")
    return eng, row


@router.patch("/engagements/{slug}/objectives/{objective_id}", response_model=ObjectiveRead)
def update_objective(
    slug: str,
    objective_id: uuid.UUID,
    body: ObjectiveUpdate,
    session: DbSession,
    user: CurrentNonGuestUser,
):
    eng, row = _objective_locked(session, slug, objective_id)
    _check_version(row, body.expected_row_version)
    fields = body.model_dump(exclude={"expected_row_version"}, exclude_unset=True)
    if fields.get("status") in _TERMINAL_OBJECTIVES:
        raise HTTPException(
            status_code=400,
            detail="use the objective lifecycle endpoints for terminal states",
        )
    for key, value in fields.items():
        setattr(row, key, value)
    row.row_version += 1
    _audit(
        session,
        eng.id,
        user.id,
        "objective.updated",
        {"objective_id": str(row.id), "fields": sorted(fields), "row_version": row.row_version},
    )
    session.commit()
    session.refresh(row)
    return row


def _objective_lifecycle(
    slug: str,
    objective_id: uuid.UUID,
    body: VersionedReason,
    session: Session,
    user: Any,
    *,
    complete: bool,
):
    eng, row = _objective_locked(session, slug, objective_id)
    _check_version(row, body.expected_row_version)
    now = datetime.now(tz=UTC)
    if complete:
        if row.status == ObjectiveStatus.completed:
            return row
        if row.status == ObjectiveStatus.cancelled:
            raise HTTPException(status_code=409, detail="cancelled objective cannot be completed")
        row.status = ObjectiveStatus.completed
        row.completed_by_user_id = user.id
        row.completed_at = now
        event = "objective.completed"
    else:
        if row.status != ObjectiveStatus.completed:
            raise HTTPException(status_code=409, detail="only completed objectives can be reopened")
        row.status = ObjectiveStatus.active
        row.completed_by_user_id = None
        row.completed_at = None
        event = "objective.reopened"
    row.row_version += 1
    _audit(
        session,
        eng.id,
        user.id,
        event,
        {"objective_id": str(row.id), "reason": body.reason, "row_version": row.row_version},
    )
    session.commit()
    session.refresh(row)
    return row


@router.post("/engagements/{slug}/objectives/{objective_id}/complete", response_model=ObjectiveRead)
def complete_objective(
    slug: str,
    objective_id: uuid.UUID,
    body: VersionedReason,
    session: DbSession,
    user: CurrentNonGuestUser,
):
    return _objective_lifecycle(slug, objective_id, body, session, user, complete=True)


@router.post("/engagements/{slug}/objectives/{objective_id}/reopen", response_model=ObjectiveRead)
def reopen_objective(
    slug: str,
    objective_id: uuid.UUID,
    body: VersionedReason,
    session: DbSession,
    user: CurrentNonGuestUser,
):
    return _objective_lifecycle(slug, objective_id, body, session, user, complete=False)


@router.delete("/engagements/{slug}/objectives/{objective_id}", response_model=ObjectiveRead)
def cancel_objective(
    slug: str,
    objective_id: uuid.UUID,
    body: VersionedReason,
    session: DbSession,
    user: CurrentNonGuestUser,
):
    eng, row = _objective_locked(session, slug, objective_id)
    _check_version(row, body.expected_row_version)
    if row.status == ObjectiveStatus.completed:
        raise HTTPException(status_code=409, detail="completed objective cannot be cancelled")
    row.status = ObjectiveStatus.cancelled
    row.row_version += 1
    _audit(
        session,
        eng.id,
        user.id,
        "objective.cancelled",
        {
            "objective_id": str(row.id),
            "reason": body.reason,
            "row_version": row.row_version,
        },
    )
    session.commit()
    session.refresh(row)
    return row


# Work items ----------------------------------------------------------------


@router.get("/engagements/{slug}/work-items", response_model=list[WorkItemRead])
def list_work_items(
    slug: str,
    session: DbSession,
    _user: CurrentUser,
    work_status: Annotated[WorkItemStatus | None, Query(alias="status")] = None,
    priority: WorkItemPriority | None = None,
    executor_type: WorkItemExecutor | None = None,
    assigned_user_id: uuid.UUID | None = None,
    objective_id: uuid.UUID | None = None,
    finding_id: uuid.UUID | None = None,
    needs_decision: bool | None = None,
    q: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    cursor: uuid.UUID | None = None,
):
    eng = _engagement(session, slug)
    stmt = select(WorkItem).where(WorkItem.engagement_id == eng.id)
    if work_status is not None:
        stmt = stmt.where(WorkItem.status == work_status)
    if priority:
        stmt = stmt.where(WorkItem.priority == priority)
    if executor_type:
        stmt = stmt.where(WorkItem.executor_type == executor_type)
    if assigned_user_id:
        stmt = stmt.where(WorkItem.assigned_user_id == assigned_user_id)
    if objective_id:
        stmt = stmt.where(WorkItem.objective_id == objective_id)
    if needs_decision is not None:
        proposed_result_exists = (
            select(WorkItemResult.id)
            .where(
                WorkItemResult.work_item_id == WorkItem.id,
                WorkItemResult.state == WorkItemResultState.proposed,
            )
            .exists()
        )
        stmt = stmt.where(proposed_result_exists if needs_decision else ~proposed_result_exists)
    if finding_id:
        stmt = stmt.join(WorkItemFinding, WorkItemFinding.work_item_id == WorkItem.id).where(
            WorkItemFinding.finding_id == finding_id
        )
    if q:
        needle = f"%{q[:200]}%"
        stmt = stmt.where(
            or_(
                WorkItem.title.ilike(needle),
                WorkItem.description.ilike(needle),
                WorkItem.rationale.ilike(needle),
            )
        )
    if cursor:
        cursor_row = session.get(WorkItem, cursor)
        if cursor_row is None or cursor_row.engagement_id != eng.id:
            raise HTTPException(status_code=400, detail="invalid work-item cursor")
        stmt = stmt.where(WorkItem.created_at < cursor_row.created_at)
    rows = list(
        session.execute(stmt.order_by(WorkItem.created_at.desc()).limit(limit)).scalars().unique()
    )
    return [_work_read(session, row) for row in rows]


@router.post("/engagements/{slug}/work-items", response_model=WorkItemRead, status_code=201)
def create_work_item(
    slug: str, body: WorkItemCreate, session: DbSession, user: CurrentNonGuestUser
):
    eng = _engagement(session, slug)
    _ensure_mutable(eng)
    _objective_for_engagement(session, eng.id, body.objective_id)
    if body.status in _TERMINAL_WORK:
        raise HTTPException(status_code=400, detail="new work items cannot be terminal")
    if body.parent_work_item_id:
        parent = session.get(WorkItem, body.parent_work_item_id)
        if parent is None or parent.engagement_id != eng.id:
            raise HTTPException(
                status_code=400, detail="parent work item does not belong to engagement"
            )
    for link in body.finding_links:
        _finding_for_engagement(session, eng.id, link.finding_id)
    values = body.model_dump(exclude={"finding_links"})
    row = WorkItem(engagement_id=eng.id, created_by_user_id=user.id, **values)
    session.add(row)
    session.flush()
    for link in body.finding_links:
        session.add(
            WorkItemFinding(
                work_item_id=row.id, finding_id=link.finding_id, relationship=link.relationship
            )
        )
    _audit(
        session,
        eng.id,
        user.id,
        "work_item.created",
        {
            "work_item_id": str(row.id),
            "objective_id": str(row.objective_id) if row.objective_id else None,
            "finding_ids": [str(link.finding_id) for link in body.finding_links],
        },
    )
    session.commit()
    session.refresh(row)
    return _work_read(session, row)


@router.get("/work-items/{work_item_id}", response_model=WorkItemRead)
def get_work_item(work_item_id: uuid.UUID, session: DbSession, _user: CurrentUser):
    row = session.get(WorkItem, work_item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="work item not found")
    return _work_read(session, row)


@router.patch("/work-items/{work_item_id}", response_model=WorkItemRead)
def update_work_item(
    work_item_id: uuid.UUID, body: WorkItemUpdate, session: DbSession, user: CurrentNonGuestUser
):
    row = _work_item_locked(session, work_item_id)
    eng = _work_engagement(session, row)
    _ensure_mutable(eng)
    _check_version(row, body.expected_row_version)
    fields = body.model_dump(exclude={"expected_row_version"}, exclude_unset=True)
    if "objective_id" in fields:
        _objective_for_engagement(session, row.engagement_id, fields["objective_id"])
    if fields.get("parent_work_item_id"):
        parent = session.get(WorkItem, fields["parent_work_item_id"])
        if parent is None or parent.engagement_id != row.engagement_id or parent.id == row.id:
            raise HTTPException(status_code=400, detail="invalid parent work item")
    for key, value in fields.items():
        setattr(row, key, value)
    row.row_version += 1
    _audit(
        session,
        row.engagement_id,
        user.id,
        "work_item.updated",
        {"work_item_id": str(row.id), "fields": sorted(fields), "row_version": row.row_version},
    )
    session.commit()
    session.refresh(row)
    return _work_read(session, row)


def _transition_work(row: WorkItem, action: str, reason: str | None, user_id: uuid.UUID) -> None:
    now = datetime.now(tz=UTC)
    if action == "start":
        if row.status not in (WorkItemStatus.ready, WorkItemStatus.blocked):
            raise HTTPException(status_code=409, detail=f"cannot start {row.status.value} work")
        row.status = WorkItemStatus.in_progress
        row.started_at = row.started_at or now
        row.blocked_reason = None
    elif action == "block":
        if row.status not in (WorkItemStatus.ready, WorkItemStatus.in_progress):
            raise HTTPException(status_code=409, detail=f"cannot block {row.status.value} work")
        row.status = WorkItemStatus.blocked
        row.blocked_reason = reason
    elif action == "defer":
        if row.status in _TERMINAL_WORK:
            raise HTTPException(status_code=409, detail=f"cannot defer {row.status.value} work")
        row.status = WorkItemStatus.deferred
        row.blocked_reason = reason
    elif action == "reopen":
        if row.status not in (WorkItemStatus.completed, WorkItemStatus.deferred):
            raise HTTPException(status_code=409, detail=f"cannot reopen {row.status.value} work")
        row.status = WorkItemStatus.ready
        row.completed_at = None
        row.completed_by_user_id = None
        row.resolution_outcome = None
        row.resolution_note = None
        row.blocked_reason = None
    elif action == "cancel":
        if row.status == WorkItemStatus.completed:
            raise HTTPException(status_code=409, detail="completed work cannot be cancelled")
        row.status = WorkItemStatus.cancelled
        row.completed_at = now
        row.completed_by_user_id = user_id
        row.resolution_note = reason


def _work_lifecycle(
    work_item_id: uuid.UUID, body: VersionedReason, session: Session, user: Any, action: str
) -> WorkItemRead:
    row = _work_item_locked(session, work_item_id)
    eng = _work_engagement(session, row)
    _ensure_mutable(eng)
    _check_version(row, body.expected_row_version)
    _transition_work(row, action, body.reason, user.id)
    row.row_version += 1
    events = {
        "start": "work_item.started",
        "block": "work_item.blocked",
        "defer": "work_item.deferred",
        "reopen": "work_item.reopened",
        "cancel": "work_item.cancelled",
    }
    _audit(
        session,
        row.engagement_id,
        user.id,
        events[action],
        {"work_item_id": str(row.id), "reason": body.reason, "row_version": row.row_version},
    )
    session.commit()
    session.refresh(row)
    return _work_read(session, row)


@router.post("/work-items/{work_item_id}/start", response_model=WorkItemRead)
def start_work_item(
    work_item_id: uuid.UUID, body: VersionedReason, session: DbSession, user: CurrentNonGuestUser
):
    return _work_lifecycle(work_item_id, body, session, user, "start")


@router.post("/work-items/{work_item_id}/block", response_model=WorkItemRead)
def block_work_item(
    work_item_id: uuid.UUID, body: WorkItemBlock, session: DbSession, user: CurrentNonGuestUser
):
    return _work_lifecycle(
        work_item_id,
        VersionedReason(expected_row_version=body.expected_row_version, reason=body.reason),
        session,
        user,
        "block",
    )


@router.post("/work-items/{work_item_id}/defer", response_model=WorkItemRead)
def defer_work_item(
    work_item_id: uuid.UUID, body: VersionedReason, session: DbSession, user: CurrentNonGuestUser
):
    return _work_lifecycle(work_item_id, body, session, user, "defer")


@router.post("/work-items/{work_item_id}/reopen", response_model=WorkItemRead)
def reopen_work_item(
    work_item_id: uuid.UUID, body: VersionedReason, session: DbSession, user: CurrentNonGuestUser
):
    return _work_lifecycle(work_item_id, body, session, user, "reopen")


@router.post("/work-items/{work_item_id}/cancel", response_model=WorkItemRead)
def cancel_work_item(
    work_item_id: uuid.UUID, body: VersionedReason, session: DbSession, user: CurrentNonGuestUser
):
    return _work_lifecycle(work_item_id, body, session, user, "cancel")


@router.post("/work-items/{work_item_id}/resolve", response_model=WorkItemRead)
def resolve_work_item(
    work_item_id: uuid.UUID, body: WorkItemResolve, session: DbSession, user: CurrentNonGuestUser
):
    row = _work_item_locked(session, work_item_id)
    eng = _work_engagement(session, row)
    _ensure_mutable(eng)
    _check_version(row, body.expected_row_version)
    if row.status in _TERMINAL_WORK:
        raise HTTPException(status_code=409, detail=f"work item is {row.status.value}")
    row.status = WorkItemStatus.completed
    row.resolution_outcome = body.outcome
    row.resolution_note = body.note
    row.completed_by_user_id = user.id
    row.completed_at = datetime.now(tz=UTC)
    row.row_version += 1
    _audit(
        session,
        row.engagement_id,
        user.id,
        "work_item.resolved",
        {
            "work_item_id": str(row.id),
            "outcome": body.outcome.value,
            "note": body.note,
            "evidence_refs": body.evidence_refs,
            "row_version": row.row_version,
        },
    )
    session.commit()
    session.refresh(row)
    return _work_read(session, row)


@router.post("/work-items/{work_item_id}/findings", response_model=WorkItemRead)
def add_work_item_finding(
    work_item_id: uuid.UUID, body: WorkItemFindingAdd, session: DbSession, user: CurrentNonGuestUser
):
    row = _work_item_locked(session, work_item_id)
    eng = _work_engagement(session, row)
    _ensure_mutable(eng)
    _check_version(row, body.expected_work_item_version)
    _finding_for_engagement(session, row.engagement_id, body.finding_id)
    exists = session.execute(
        select(WorkItemFinding).where(
            WorkItemFinding.work_item_id == row.id,
            WorkItemFinding.finding_id == body.finding_id,
            WorkItemFinding.relationship == body.relationship,
        )
    ).scalar_one_or_none()
    if exists is None:
        session.add(
            WorkItemFinding(
                work_item_id=row.id, finding_id=body.finding_id, relationship=body.relationship
            )
        )
        row.row_version += 1
        _audit(
            session,
            row.engagement_id,
            user.id,
            "work_item.finding_linked",
            {
                "work_item_id": str(row.id),
                "finding_id": str(body.finding_id),
                "relationship": body.relationship.value,
            },
        )
        session.commit()
        session.refresh(row)
    return _work_read(session, row)


@router.delete("/work-items/{work_item_id}/findings/{finding_id}", response_model=WorkItemRead)
def remove_work_item_finding(
    work_item_id: uuid.UUID,
    finding_id: uuid.UUID,
    expected_work_item_version: Annotated[int, Query(ge=1)],
    session: DbSession,
    user: CurrentNonGuestUser,
):
    row = _work_item_locked(session, work_item_id)
    eng = _work_engagement(session, row)
    _ensure_mutable(eng)
    _check_version(row, expected_work_item_version)
    links = list(
        session.execute(
            select(WorkItemFinding).where(
                WorkItemFinding.work_item_id == row.id, WorkItemFinding.finding_id == finding_id
            )
        ).scalars()
    )
    if not links:
        raise HTTPException(status_code=404, detail="work item finding link not found")
    for link in links:
        session.delete(link)
    row.row_version += 1
    _audit(
        session,
        row.engagement_id,
        user.id,
        "work_item.finding_unlinked",
        {"work_item_id": str(row.id), "finding_id": str(finding_id)},
    )
    session.commit()
    session.refresh(row)
    return _work_read(session, row)


# Results -------------------------------------------------------------------


@router.get("/work-items/{work_item_id}/results", response_model=list[WorkItemResultRead])
def list_work_item_results(work_item_id: uuid.UUID, session: DbSession, _user: CurrentUser):
    if session.get(WorkItem, work_item_id) is None:
        raise HTTPException(status_code=404, detail="work item not found")
    return list(
        session.execute(
            select(WorkItemResult)
            .where(WorkItemResult.work_item_id == work_item_id)
            .order_by(WorkItemResult.revision.desc())
        ).scalars()
    )


@router.post(
    "/work-items/{work_item_id}/results", response_model=WorkItemResultRead, status_code=201
)
def create_work_item_result(
    work_item_id: uuid.UUID,
    body: WorkItemResultCreate,
    session: DbSession,
    user: CurrentNonGuestUser,
):
    work = _work_item_locked(session, work_item_id)
    eng = _work_engagement(session, work)
    _ensure_mutable(eng)
    if work.status in _TERMINAL_WORK:
        raise HTTPException(status_code=409, detail="terminal work does not accept new results")
    source_execution = None
    if body.proposed_by_execution_id is not None:
        source_execution = session.get(AgentExecution, body.proposed_by_execution_id)
        if source_execution is None or source_execution.engagement_id != work.engagement_id:
            raise HTTPException(
                status_code=400,
                detail="agent execution does not belong to the work-item engagement",
            )
    revision = (
        int(
            session.execute(
                select(func.max(WorkItemResult.revision)).where(
                    WorkItemResult.work_item_id == work.id
                )
            ).scalar_one()
            or 0
        )
        + 1
    )
    result = WorkItemResult(
        work_item_id=work.id,
        revision=revision,
        state=WorkItemResultState.proposed,
        summary=body.summary,
        structured=body.structured,
        evidence_refs=body.evidence_refs,
        proposed_by_user_id=None if source_execution else user.id,
        proposed_by_execution_id=source_execution.id if source_execution else None,
    )
    session.add(result)
    session.flush()
    _audit(
        session,
        work.engagement_id,
        user.id,
        "work_item.agent_result_proposed",
        {
            "work_item_id": str(work.id),
            "result_id": str(result.id),
            "revision": revision,
            "proposed_by_execution_id": (str(source_execution.id) if source_execution else None),
        },
    )
    session.commit()
    session.refresh(result)
    return result


def _result_and_work_locked(
    session: Session, result_id: uuid.UUID
) -> tuple[WorkItemResult, WorkItem]:
    result = session.execute(
        select(WorkItemResult).where(WorkItemResult.id == result_id).with_for_update()
    ).scalar_one_or_none()
    if result is None:
        raise HTTPException(status_code=404, detail="work item result not found")
    work = _work_item_locked(session, result.work_item_id)
    return result, work


def _signal_for_result(session: Session, result_id: uuid.UUID) -> StrategySignal | None:
    return session.execute(
        select(StrategySignal)
        .where(StrategySignal.source_work_item_result_id == result_id)
        .order_by(StrategySignal.created_at)
        .limit(1)
    ).scalar_one_or_none()


@router.post("/work-item-results/{result_id}/accept", response_model=ResultDecisionResponse)
def accept_work_item_result(
    result_id: uuid.UUID, body: WorkItemResultAccept, session: DbSession, user: CurrentNonGuestUser
):
    result, work = _result_and_work_locked(session, result_id)
    eng = _work_engagement(session, work)
    if result.state == WorkItemResultState.accepted:
        return ResultDecisionResponse(
            work_item=_work_read(session, work),
            result=WorkItemResultRead.model_validate(result),
            strategy_signal=StrategySignalRead.model_validate(signal)
            if (signal := _signal_for_result(session, result.id))
            else None,
            rollup=_rollup(session, work.engagement_id),
        )
    _ensure_mutable(eng)
    if result.state != WorkItemResultState.proposed:
        raise HTTPException(status_code=409, detail=f"result is {result.state.value}")
    _check_version(work, body.expected_work_item_version)
    if body.resolve_work_item and work.status in _TERMINAL_WORK:
        raise HTTPException(status_code=409, detail=f"work item is {work.status.value}")
    now = datetime.now(tz=UTC)
    previous = session.execute(
        select(WorkItemResult)
        .where(
            WorkItemResult.work_item_id == work.id,
            WorkItemResult.state == WorkItemResultState.accepted,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if previous and previous.id != result.id:
        previous.state = WorkItemResultState.superseded
        previous.decided_at = now
        previous.decided_by_user_id = user.id
        for stale_signal in session.execute(
            select(StrategySignal).where(
                StrategySignal.source_work_item_result_id == previous.id,
                StrategySignal.status == StrategySignalStatus.open,
            )
        ).scalars():
            stale_signal.status = StrategySignalStatus.superseded
            stale_signal.decided_by_user_id = user.id
            stale_signal.decided_at = now
        # Release the partial accepted-result unique slot before accepting the
        # new immutable revision.
        session.flush()
    result.state = WorkItemResultState.accepted
    result.decided_at = now
    result.decided_by_user_id = user.id
    signal: StrategySignal | None = None
    if body.resolve_work_item:
        work.status = WorkItemStatus.completed
        work.resolution_outcome = body.resolution_outcome
        work.resolution_note = body.resolution_note
        work.completed_at = now
        work.completed_by_user_id = user.id
        work.row_version += 1
    if body.share_with_strategy:
        signal = _signal_for_result(session, result.id)
        if signal is None:
            confidence = str(result.structured.get("confidence") or "medium")
            if confidence not in {"low", "medium", "high"}:
                confidence = "medium"
            signal = StrategySignal(
                engagement_id=work.engagement_id,
                source_work_item_id=work.id,
                source_work_item_result_id=result.id,
                signal_type="work_item_result",
                summary=result.summary,
                confidence=confidence,
                evidence_refs=list(result.evidence_refs or []),
                suggested_effect=dict(result.structured.get("suggested_effect") or {}),
                dedup_key=f"work-item-result:{result.id}",
                status=StrategySignalStatus.open,
            )
            session.add(signal)
    _audit(
        session,
        work.engagement_id,
        user.id,
        "work_item.agent_result_accepted",
        {
            "work_item_id": str(work.id),
            "result_id": str(result.id),
            "superseded_result_id": str(previous.id)
            if previous and previous.id != result.id
            else None,
            "resolve_work_item": body.resolve_work_item,
            "resolution_outcome": body.resolution_outcome.value
            if body.resolution_outcome
            else None,
            "share_with_strategy": body.share_with_strategy,
        },
    )
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=409, detail="result decision conflicted with another analyst"
        ) from exc
    session.refresh(result)
    session.refresh(work)
    if signal:
        session.refresh(signal)
    return ResultDecisionResponse(
        work_item=_work_read(session, work),
        result=WorkItemResultRead.model_validate(result),
        strategy_signal=StrategySignalRead.model_validate(signal) if signal else None,
        rollup=_rollup(session, work.engagement_id),
    )


@router.post("/work-item-results/{result_id}/reject", response_model=WorkItemResultRead)
def reject_work_item_result(
    result_id: uuid.UUID, body: WorkItemResultReject, session: DbSession, user: CurrentNonGuestUser
):
    result, work = _result_and_work_locked(session, result_id)
    eng = _work_engagement(session, work)
    if result.state == WorkItemResultState.rejected:
        return result
    _ensure_mutable(eng)
    if result.state != WorkItemResultState.proposed:
        raise HTTPException(status_code=409, detail=f"result is {result.state.value}")
    result.state = WorkItemResultState.rejected
    result.decided_by_user_id = user.id
    result.decided_at = datetime.now(tz=UTC)
    _audit(
        session,
        work.engagement_id,
        user.id,
        "work_item.agent_result_rejected",
        {"work_item_id": str(work.id), "result_id": str(result.id), "reason": body.reason},
    )
    session.commit()
    session.refresh(result)
    return result


# Rollup --------------------------------------------------------------------


@router.get("/engagements/{slug}/work-item-rollup", response_model=WorkItemRollup)
def get_work_item_rollup(slug: str, session: DbSession, _user: CurrentUser):
    return _rollup(session, _engagement(session, slug).id)


# Strategy signals ----------------------------------------------------------


def _create_signal(
    session: Session,
    engagement_id: uuid.UUID,
    body: SignalCreate,
    *,
    finding_id: uuid.UUID | None = None,
    work_item_id: uuid.UUID | None = None,
) -> StrategySignal:
    existing = session.execute(
        select(StrategySignal).where(
            StrategySignal.engagement_id == engagement_id,
            StrategySignal.dedup_key == body.dedup_key,
            StrategySignal.status == StrategySignalStatus.open,
        )
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=409,
            detail={"code": "duplicate_strategy_signal", "signal_id": str(existing.id)},
        )
    if body.source_work_item_result_id:
        result = session.get(WorkItemResult, body.source_work_item_result_id)
        if result is None:
            raise HTTPException(status_code=400, detail="work item result not found")
        if result.state != WorkItemResultState.accepted:
            raise HTTPException(
                status_code=409,
                detail="only an accepted work-item result may be shared with strategy",
            )
        result_work = session.get(WorkItem, result.work_item_id)
        if (
            result_work is None
            or result_work.engagement_id != engagement_id
            or (work_item_id and result_work.id != work_item_id)
        ):
            raise HTTPException(
                status_code=400, detail="work item result does not belong to signal engagement"
            )
    if body.source_execution_id:
        execution = session.get(AgentExecution, body.source_execution_id)
        if execution is None or execution.engagement_id != engagement_id:
            raise HTTPException(
                status_code=400,
                detail="agent execution does not belong to signal engagement",
            )
    row = StrategySignal(
        engagement_id=engagement_id,
        source_finding_id=finding_id,
        source_work_item_id=work_item_id,
        source_work_item_result_id=body.source_work_item_result_id,
        source_execution_id=body.source_execution_id,
        signal_type=body.signal_type,
        summary=body.summary,
        confidence=body.confidence,
        evidence_refs=body.evidence_refs,
        suggested_effect=body.suggested_effect,
        dedup_key=body.dedup_key,
        status=StrategySignalStatus.open,
    )
    session.add(row)
    session.flush()
    return row


@router.get("/engagements/{slug}/strategy/signals", response_model=list[StrategySignalRead])
def list_strategy_signals(
    slug: str,
    session: DbSession,
    _user: CurrentUser,
    signal_status: Annotated[StrategySignalStatus | None, Query(alias="status")] = None,
):
    eng = _engagement(session, slug)
    stmt = select(StrategySignal).where(StrategySignal.engagement_id == eng.id)
    if signal_status:
        stmt = stmt.where(StrategySignal.status == signal_status)
    return list(session.execute(stmt.order_by(StrategySignal.created_at.desc())).scalars())


@router.post(
    "/findings/{finding_id}/strategy-signals", response_model=StrategySignalRead, status_code=201
)
def create_finding_signal(
    finding_id: uuid.UUID, body: SignalCreate, session: DbSession, user: CurrentNonGuestUser
):
    finding = session.get(Finding, finding_id)
    if finding is None or finding.deleted_at is not None:
        raise HTTPException(status_code=404, detail="finding not found")
    eng = session.get(Engagement, finding.engagement_id)
    if eng is None:
        raise HTTPException(status_code=404, detail="engagement not found")
    _ensure_mutable(eng)
    row = _create_signal(session, eng.id, body, finding_id=finding.id)
    _audit(
        session,
        eng.id,
        user.id,
        "finding.strategy_signal_shared",
        {"finding_id": str(finding.id), "signal_id": str(row.id), "signal_type": row.signal_type},
    )
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="strategy signal already exists") from exc
    session.refresh(row)
    return row


@router.post(
    "/work-items/{work_item_id}/strategy-signals",
    response_model=StrategySignalRead,
    status_code=201,
)
def create_work_item_signal(
    work_item_id: uuid.UUID, body: SignalCreate, session: DbSession, user: CurrentNonGuestUser
):
    work = session.get(WorkItem, work_item_id)
    if work is None:
        raise HTTPException(status_code=404, detail="work item not found")
    eng = _work_engagement(session, work)
    _ensure_mutable(eng)
    row = _create_signal(session, eng.id, body, work_item_id=work.id)
    _audit(
        session,
        eng.id,
        user.id,
        "work_item.strategy_signal_shared",
        {"work_item_id": str(work.id), "signal_id": str(row.id), "signal_type": row.signal_type},
    )
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="strategy signal already exists") from exc
    session.refresh(row)
    return row


def _decide_signal(
    signal_id: uuid.UUID,
    body: SignalDecision,
    session: Session,
    user: Any,
    target: StrategySignalStatus,
):
    row = session.execute(
        select(StrategySignal).where(StrategySignal.id == signal_id).with_for_update()
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="strategy signal not found")
    eng = session.get(Engagement, row.engagement_id)
    if eng is None:
        raise HTTPException(status_code=404, detail="engagement not found")
    _ensure_mutable(eng)
    if row.status == target:
        return row
    if row.status != StrategySignalStatus.open:
        raise HTTPException(status_code=409, detail=f"signal is {row.status.value}")
    row.status = target
    row.decided_by_user_id = user.id
    row.decided_at = datetime.now(tz=UTC)
    _audit(
        session,
        eng.id,
        user.id,
        f"strategy_signal.{target.value}",
        {"signal_id": str(row.id), "reason": body.reason},
    )
    session.commit()
    session.refresh(row)
    return row


@router.post("/strategy-signals/{signal_id}/incorporate", response_model=StrategySignalRead)
def incorporate_signal(
    signal_id: uuid.UUID, body: SignalDecision, session: DbSession, user: CurrentNonGuestUser
):
    return _decide_signal(signal_id, body, session, user, StrategySignalStatus.incorporated)


@router.post("/strategy-signals/{signal_id}/dismiss", response_model=StrategySignalRead)
def dismiss_signal(
    signal_id: uuid.UUID, body: SignalDecision, session: DbSession, user: CurrentNonGuestUser
):
    return _decide_signal(signal_id, body, session, user, StrategySignalStatus.dismissed)


# Checkpoints and deterministic resume --------------------------------------


def _checkpoint_facts(session: Session, engagement_id: uuid.UUID) -> dict[str, Any]:
    work = list(
        session.execute(select(WorkItem).where(WorkItem.engagement_id == engagement_id)).scalars()
    )
    objectives = list(
        session.execute(
            select(EngagementObjective).where(EngagementObjective.engagement_id == engagement_id)
        ).scalars()
    )
    signals = list(
        session.execute(
            select(StrategySignal).where(StrategySignal.engagement_id == engagement_id)
        ).scalars()
    )
    current = _current_revision(session, engagement_id)
    return {
        "strategy_revision_id": str(current.id) if current else None,
        "strategy_version": current.version if current else None,
        "objectives": {
            state.value: sum(row.status == state for row in objectives) for state in ObjectiveStatus
        },
        "work_items": {
            state.value: sum(row.status == state for row in work) for state in WorkItemStatus
        },
        "open_signal_count": sum(row.status == StrategySignalStatus.open for row in signals),
        "rollup": _rollup(session, engagement_id).model_dump(mode="json"),
    }


@router.post("/engagements/{slug}/checkpoints", response_model=CheckpointRead, status_code=201)
def create_checkpoint(
    slug: str, body: CheckpointCreate, session: DbSession, user: CurrentNonGuestUser
):
    eng = _engagement(session, slug)
    _ensure_mutable(eng)
    now = datetime.now(tz=UTC)
    current = _current_revision(session, eng.id)
    row = EngagementCheckpoint(
        engagement_id=eng.id,
        strategy_revision_id=current.id if current else None,
        created_by_user_id=user.id,
        material_event_cursor=now,
        facts=_checkpoint_facts(session, eng.id),
        narrative=body.narrative,
    )
    session.add(row)
    session.flush()
    _audit(
        session,
        eng.id,
        user.id,
        "engagement.checkpoint_created",
        {
            "checkpoint_id": str(row.id),
            "strategy_revision_id": str(row.strategy_revision_id)
            if row.strategy_revision_id
            else None,
        },
    )
    session.commit()
    session.refresh(row)
    return row


@router.get("/engagements/{slug}/checkpoints", response_model=list[CheckpointRead])
def list_checkpoints(slug: str, session: DbSession, _user: CurrentUser):
    eng = _engagement(session, slug)
    return list(
        session.execute(
            select(EngagementCheckpoint)
            .where(EngagementCheckpoint.engagement_id == eng.id)
            .order_by(EngagementCheckpoint.created_at.desc())
        ).scalars()
    )


@router.get("/engagements/{slug}/resume", response_model=ResumeResponse)
def get_resume(slug: str, session: DbSession, _user: CurrentUser):
    eng = _engagement(session, slug)
    work = list(
        session.execute(
            select(WorkItem)
            .where(WorkItem.engagement_id == eng.id)
            .order_by(WorkItem.updated_at.desc())
        ).scalars()
    )
    current = _current_revision(session, eng.id)
    checkpoint = session.execute(
        select(EngagementCheckpoint)
        .where(EngagementCheckpoint.engagement_id == eng.id)
        .order_by(EngagementCheckpoint.material_event_cursor.desc())
        .limit(1)
    ).scalar_one_or_none()
    cursor = checkpoint.material_event_cursor if checkpoint else eng.created_at
    audits = list(
        session.execute(
            select(AuditLog)
            .where(
                AuditLog.engagement_id == eng.id,
                AuditLog.created_at > cursor,
                AuditLog.event_type != "engagement.checkpoint_created",
            )
            .order_by(AuditLog.created_at.desc())
            .limit(100)
        ).scalars()
    )
    signals = list(
        session.execute(
            select(StrategySignal)
            .where(
                StrategySignal.engagement_id == eng.id,
                StrategySignal.status == StrategySignalStatus.open,
            )
            .order_by(StrategySignal.created_at.desc())
        ).scalars()
    )
    proposed_revisions = list(
        session.execute(
            select(EngagementStrategyRevision).where(
                EngagementStrategyRevision.engagement_id == eng.id,
                EngagementStrategyRevision.state == StrategyRevisionState.proposed,
            )
        ).scalars()
    )
    open_suggestions = list(
        session.execute(
            select(Suggestion).where(
                Suggestion.engagement_id == eng.id,
                Suggestion.status == SuggestionStatus.open,
            )
        ).scalars()
    )
    coverage = list(
        session.execute(select(CoverageItem).where(CoverageItem.engagement_id == eng.id)).scalars()
    )
    readiness = build_report_readiness(session, engagement=eng)
    active = [
        row for row in work if row.status in (WorkItemStatus.ready, WorkItemStatus.in_progress)
    ]
    blocked = [row for row in work if row.status == WorkItemStatus.blocked]
    recommended = sorted(
        active + blocked,
        key=lambda row: (
            row.status != WorkItemStatus.blocked,
            row.due_at or datetime.max.replace(tzinfo=UTC),
            row.created_at,
        ),
    )[:10]
    facts = _checkpoint_facts(session, eng.id)
    return ResumeResponse(
        current_focus={
            "strategy_revision_id": str(current.id) if current else None,
            "strategy_version": current.version if current else None,
            "strategy_summary": current.summary if current else None,
            "rollup": facts["rollup"],
        },
        since_checkpoint={
            "checkpoint_id": str(checkpoint.id) if checkpoint else None,
            "cursor": cursor.isoformat(),
            "material_event_count": len(audits),
            "events": [
                {
                    "event_type": row.event_type,
                    "created_at": row.created_at.isoformat(),
                    "payload": row.payload,
                }
                for row in audits
            ],
        },
        active_work=[_work_read(session, row) for row in active],
        blocked_work=[_work_read(session, row) for row in blocked],
        decisions_required=(
            [
                {"type": "strategy_signal", "id": str(row.id), "summary": row.summary}
                for row in signals
            ]
            + [
                {"type": "strategy_revision", "id": str(row.id), "summary": row.summary}
                for row in proposed_revisions
            ]
            + [
                {"type": "suggestion", "id": str(row.id), "summary": row.title}
                for row in open_suggestions
            ]
        ),
        recommended_starting_records=[
            {"type": "work_item", "id": str(row.id), "title": row.title, "status": row.status.value}
            for row in recommended
        ],
        coverage_summary={
            state.value: sum(row.status == state for row in coverage) for state in CoverageStatus
        },
        report_readiness=readiness.model_dump(mode="json"),
        generated_at=datetime.now(tz=UTC),
    )


__all__ = ["router"]
