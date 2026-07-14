"""Coverage ledger and deterministic engagement completion endpoints."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.api.deps import CurrentNonGuestUser, CurrentUser, DbSession
from app.models import (
    ActorType,
    AuditLog,
    CoverageCategory,
    CoverageItem,
    CoverageStatus,
    Engagement,
    EngagementCompletionAction,
    EngagementCompletionDecision,
    EngagementObjective,
    EngagementStatus,
    EngagementStrategyRevision,
    EngagementWorkState,
    ScopeItem,
    StrategyRevisionState,
)
from app.schemas.completion import (
    ApproveCompletion,
    CompletionDecisionRead,
    CompletionMutationResponse,
    CompletionReadiness,
    CoverageItemCreate,
    CoverageItemRead,
    CoverageItemUpdate,
    ReopenCompletion,
    StartCompletionReview,
)
from app.services.completion import (
    build_completion_readiness,
    validate_completion_exceptions,
)

router = APIRouter()


def _engagement(session: DbSession, slug: str, *, lock: bool = False) -> Engagement:
    stmt = select(Engagement).where(Engagement.slug == slug)
    if lock:
        stmt = stmt.with_for_update()
    row = session.execute(stmt).scalar_one_or_none()
    if row is None or row.status == EngagementStatus.flushed:
        raise HTTPException(status_code=404, detail="engagement not found")
    return row


def _mutable(engagement: Engagement) -> None:
    if engagement.status == EngagementStatus.archived:
        raise HTTPException(status_code=409, detail="archived engagement is read-only")
    if engagement.work_state == EngagementWorkState.completed:
        raise HTTPException(
            status_code=409,
            detail="completed engagement is read-only; reopen it before making changes",
        )


def _audit(
    session: DbSession,
    engagement: Engagement,
    user_id: uuid.UUID,
    event_type: str,
    payload: dict,
) -> None:
    session.add(
        AuditLog(
            engagement_id=engagement.id,
            actor_type=ActorType.user,
            actor_id=str(user_id),
            event_type=event_type,
            payload=payload,
        )
    )


def _decision_response(
    engagement: Engagement,
    decision: EngagementCompletionDecision,
    readiness: CompletionReadiness | None,
) -> CompletionMutationResponse:
    return CompletionMutationResponse(
        work_state=engagement.work_state.value,
        work_state_version=engagement.work_state_version,
        decision=CompletionDecisionRead.model_validate(decision),
        readiness=readiness,
    )


# ---------------------------------------------------------------------------
# Coverage ledger
# ---------------------------------------------------------------------------


@router.get("/engagements/{slug}/coverage", response_model=list[CoverageItemRead])
def list_coverage(
    slug: str,
    session: DbSession,
    _user: CurrentUser,
    coverage_status: Annotated[CoverageStatus | None, Query(alias="status")] = None,
    category: CoverageCategory | None = None,
) -> list[CoverageItem]:
    eng = _engagement(session, slug)
    stmt = select(CoverageItem).where(CoverageItem.engagement_id == eng.id)
    if coverage_status is not None:
        stmt = stmt.where(CoverageItem.status == coverage_status)
    if category is not None:
        stmt = stmt.where(CoverageItem.activity_category == category)
    return list(
        session.execute(
            stmt.order_by(
                CoverageItem.activity_category,
                CoverageItem.target_kind,
                CoverageItem.target_key,
            )
        ).scalars()
    )


@router.post(
    "/engagements/{slug}/coverage",
    response_model=CoverageItemRead,
    status_code=201,
)
def create_coverage(
    slug: str,
    body: CoverageItemCreate,
    session: DbSession,
    user: CurrentNonGuestUser,
) -> CoverageItem:
    eng = _engagement(session, slug)
    _mutable(eng)
    try:
        category = CoverageCategory(body.activity_category)
        item_status = CoverageStatus(body.status)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if body.objective_id is not None:
        objective = session.get(EngagementObjective, body.objective_id)
        if objective is None or objective.engagement_id != eng.id:
            raise HTTPException(status_code=422, detail="objective is not in this engagement")
    if body.scope_item_id is not None:
        scope = session.get(ScopeItem, body.scope_item_id)
        if scope is None or scope.engagement_id != eng.id:
            raise HTTPException(status_code=422, detail="scope item is not in this engagement")
    duplicate = session.execute(
        select(CoverageItem.id).where(
            CoverageItem.engagement_id == eng.id,
            CoverageItem.target_kind == body.target_kind,
            CoverageItem.target_key == body.target_key,
            CoverageItem.activity_category == category,
        )
    ).first()
    if duplicate:
        raise HTTPException(status_code=409, detail="coverage item already exists")
    now = datetime.now(tz=UTC)
    row = CoverageItem(
        engagement_id=eng.id,
        objective_id=body.objective_id,
        scope_item_id=body.scope_item_id,
        target_kind=body.target_kind,
        target_key=body.target_key,
        activity_category=category,
        status=item_status,
        supporting_refs=body.supporting_refs,
        reason=body.reason,
        accepted_by_user_id=user.id if item_status == CoverageStatus.accepted_gap else None,
        accepted_at=now if item_status == CoverageStatus.accepted_gap else None,
    )
    session.add(row)
    session.flush()
    _audit(
        session,
        eng,
        user.id,
        "coverage.created",
        {"coverage_item_id": str(row.id), "status": item_status.value},
    )
    session.commit()
    session.refresh(row)
    return row


@router.patch(
    "/engagements/{slug}/coverage/{coverage_id}",
    response_model=CoverageItemRead,
)
def update_coverage(
    slug: str,
    coverage_id: uuid.UUID,
    body: CoverageItemUpdate,
    session: DbSession,
    user: CurrentNonGuestUser,
) -> CoverageItem:
    eng = _engagement(session, slug)
    _mutable(eng)
    row = session.execute(
        select(CoverageItem)
        .where(
            CoverageItem.id == coverage_id,
            CoverageItem.engagement_id == eng.id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="coverage item not found")
    if row.row_version != body.expected_row_version:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "stale coverage item",
                "current": CoverageItemRead.model_validate(row).model_dump(mode="json"),
            },
        )
    try:
        next_status = CoverageStatus(body.status)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    prior = row.status
    row.status = next_status
    if body.supporting_refs is not None:
        row.supporting_refs = body.supporting_refs
    row.reason = body.reason
    if next_status == CoverageStatus.accepted_gap:
        if not (body.reason or "").strip():
            raise HTTPException(status_code=422, detail="accepted gaps require a reason")
        row.accepted_by_user_id = user.id
        row.accepted_at = datetime.now(tz=UTC)
    else:
        row.accepted_by_user_id = None
        row.accepted_at = None
    row.row_version += 1
    _audit(
        session,
        eng,
        user.id,
        "coverage.updated",
        {
            "coverage_item_id": str(row.id),
            "prior_status": prior.value,
            "status": next_status.value,
            "row_version": row.row_version,
        },
    )
    session.commit()
    session.refresh(row)
    return row


# ---------------------------------------------------------------------------
# Completion state and immutable decisions
# ---------------------------------------------------------------------------


@router.get(
    "/engagements/{slug}/completion/readiness",
    response_model=CompletionReadiness,
)
def completion_readiness(slug: str, session: DbSession, _user: CurrentUser) -> CompletionReadiness:
    return build_completion_readiness(session, engagement=_engagement(session, slug))


def _idempotent_decision(
    session: DbSession, engagement_id: uuid.UUID, key: str
) -> EngagementCompletionDecision | None:
    return session.execute(
        select(EngagementCompletionDecision).where(
            EngagementCompletionDecision.engagement_id == engagement_id,
            EngagementCompletionDecision.idempotency_key == key,
        )
    ).scalar_one_or_none()


def _current_strategy_id(session: DbSession, engagement_id: uuid.UUID) -> uuid.UUID | None:
    return session.execute(
        select(EngagementStrategyRevision.id).where(
            EngagementStrategyRevision.engagement_id == engagement_id,
            EngagementStrategyRevision.state == StrategyRevisionState.current,
        )
    ).scalar_one_or_none()


@router.post(
    "/engagements/{slug}/completion/review",
    response_model=CompletionMutationResponse,
)
def start_completion_review(
    slug: str,
    body: StartCompletionReview,
    session: DbSession,
    user: CurrentNonGuestUser,
) -> CompletionMutationResponse:
    eng = _engagement(session, slug, lock=True)
    _mutable(eng)
    existing = _idempotent_decision(session, eng.id, body.idempotency_key)
    if existing is not None:
        if existing.action != EngagementCompletionAction.review_started:
            raise HTTPException(status_code=409, detail="idempotency key belongs to another action")
        return _decision_response(
            eng, existing, build_completion_readiness(session, engagement=eng)
        )
    if eng.work_state != EngagementWorkState.active:
        raise HTTPException(status_code=409, detail="completion review has already started")
    if eng.work_state_version != body.expected_work_state_version:
        raise HTTPException(status_code=409, detail="stale engagement work-state version")
    readiness = build_completion_readiness(session, engagement=eng)
    if readiness.readiness_hash != body.readiness_hash:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "stale readiness hash",
                "readiness": readiness.model_dump(mode="json"),
            },
        )
    prior_state = eng.work_state
    eng.work_state = EngagementWorkState.completion_review
    eng.work_state_version += 1
    decision = EngagementCompletionDecision(
        engagement_id=eng.id,
        action=EngagementCompletionAction.review_started,
        from_work_state=prior_state,
        to_work_state=eng.work_state,
        readiness_hash=readiness.readiness_hash,
        readiness_snapshot=readiness.model_dump(mode="json"),
        accepted_exceptions=[],
        strategy_revision_id=_current_strategy_id(session, eng.id),
        idempotency_key=body.idempotency_key,
        decided_by_user_id=user.id,
    )
    session.add(decision)
    session.flush()
    _audit(
        session,
        eng,
        user.id,
        "engagement.completion_review_started",
        {"decision_id": str(decision.id), "readiness_hash": readiness.readiness_hash},
    )
    session.commit()
    session.refresh(decision)
    # Entering review changes work-state/version, both of which are hash
    # inputs. Return the new preflight so approval can submit the current hash.
    current_readiness = build_completion_readiness(session, engagement=eng)
    return _decision_response(eng, decision, current_readiness)


@router.post(
    "/engagements/{slug}/completion/approve",
    response_model=CompletionMutationResponse,
)
def approve_completion(
    slug: str,
    body: ApproveCompletion,
    session: DbSession,
    user: CurrentNonGuestUser,
) -> CompletionMutationResponse:
    eng = _engagement(session, slug, lock=True)
    if eng.status == EngagementStatus.archived:
        raise HTTPException(status_code=409, detail="archived engagement is read-only")
    existing = _idempotent_decision(session, eng.id, body.idempotency_key)
    if existing is not None:
        if existing.action != EngagementCompletionAction.approved:
            raise HTTPException(status_code=409, detail="idempotency key belongs to another action")
        return _decision_response(eng, existing, None)
    if eng.work_state != EngagementWorkState.completion_review:
        raise HTTPException(status_code=409, detail="engagement is not in completion review")
    if eng.work_state_version != body.expected_work_state_version:
        raise HTTPException(status_code=409, detail="stale engagement work-state version")
    readiness = build_completion_readiness(session, engagement=eng)
    if readiness.readiness_hash != body.readiness_hash:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "stale readiness hash",
                "readiness": readiness.model_dump(mode="json"),
            },
        )
    exceptions = [item.model_dump(mode="json") for item in body.accepted_exceptions]
    try:
        validate_completion_exceptions(readiness, exceptions)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    prior_state = eng.work_state
    eng.work_state = EngagementWorkState.completed
    eng.work_state_version += 1
    decision = EngagementCompletionDecision(
        engagement_id=eng.id,
        action=EngagementCompletionAction.approved,
        from_work_state=prior_state,
        to_work_state=eng.work_state,
        readiness_hash=readiness.readiness_hash,
        readiness_snapshot=readiness.model_dump(mode="json"),
        accepted_exceptions=exceptions,
        strategy_revision_id=_current_strategy_id(session, eng.id),
        idempotency_key=body.idempotency_key,
        decided_by_user_id=user.id,
    )
    session.add(decision)
    session.flush()
    _audit(
        session,
        eng,
        user.id,
        "engagement.completion_approved",
        {
            "decision_id": str(decision.id),
            "readiness_hash": readiness.readiness_hash,
            "accepted_exceptions": exceptions,
        },
    )
    session.commit()
    session.refresh(decision)
    return _decision_response(eng, decision, None)


@router.post(
    "/engagements/{slug}/completion/reopen",
    response_model=CompletionMutationResponse,
)
def reopen_completion(
    slug: str,
    body: ReopenCompletion,
    session: DbSession,
    user: CurrentNonGuestUser,
) -> CompletionMutationResponse:
    eng = _engagement(session, slug, lock=True)
    if eng.status == EngagementStatus.archived:
        raise HTTPException(status_code=409, detail="archived engagement is read-only")
    existing = _idempotent_decision(session, eng.id, body.idempotency_key)
    if existing is not None:
        if existing.action != EngagementCompletionAction.reopened:
            raise HTTPException(status_code=409, detail="idempotency key belongs to another action")
        return _decision_response(
            eng, existing, build_completion_readiness(session, engagement=eng)
        )
    if eng.work_state != EngagementWorkState.completed:
        raise HTTPException(status_code=409, detail="engagement is not completed")
    if eng.work_state_version != body.expected_work_state_version:
        raise HTTPException(status_code=409, detail="stale engagement work-state version")
    latest_approval = session.execute(
        select(EngagementCompletionDecision)
        .where(
            EngagementCompletionDecision.engagement_id == eng.id,
            EngagementCompletionDecision.action == EngagementCompletionAction.approved,
        )
        .order_by(EngagementCompletionDecision.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if latest_approval is None or latest_approval.id != body.prior_completion_decision_id:
        raise HTTPException(
            status_code=409, detail="prior decision is not the latest completion approval"
        )
    prior_state = eng.work_state
    eng.work_state = EngagementWorkState.active
    eng.work_state_version += 1
    decision = EngagementCompletionDecision(
        engagement_id=eng.id,
        action=EngagementCompletionAction.reopened,
        from_work_state=prior_state,
        to_work_state=eng.work_state,
        readiness_hash=None,
        readiness_snapshot=None,
        accepted_exceptions=[],
        strategy_revision_id=_current_strategy_id(session, eng.id),
        prior_completion_decision_id=latest_approval.id,
        reason=body.reason.strip(),
        idempotency_key=body.idempotency_key,
        decided_by_user_id=user.id,
    )
    session.add(decision)
    session.flush()
    _audit(
        session,
        eng,
        user.id,
        "engagement.completion_reopened",
        {
            "decision_id": str(decision.id),
            "prior_completion_decision_id": str(latest_approval.id),
            "reason": body.reason.strip(),
        },
    )
    session.commit()
    session.refresh(decision)
    readiness = build_completion_readiness(session, engagement=eng)
    return _decision_response(eng, decision, readiness)


@router.get(
    "/engagements/{slug}/completion/decisions",
    response_model=list[CompletionDecisionRead],
)
def list_completion_decisions(
    slug: str, session: DbSession, _user: CurrentUser
) -> list[EngagementCompletionDecision]:
    eng = _engagement(session, slug)
    return list(
        session.execute(
            select(EngagementCompletionDecision)
            .where(EngagementCompletionDecision.engagement_id == eng.id)
            .order_by(EngagementCompletionDecision.created_at.desc())
        ).scalars()
    )
