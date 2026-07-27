"""Per-engagement v3 activation and analyst-triggered intelligence."""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select

from app.api.deps import CurrentNonGuestUser, DbSession
from app.models import (
    ActorType,
    AgentExecution,
    AgentExecutionStatus,
    AgentName,
    AgentTrigger,
    AuditLog,
    Engagement,
    EngagementArchitecture,
    EngagementStatus,
    EngagementStrategyRevision,
    EngagementWorkState,
    Entity,
    Finding,
    MemoryKind,
    StrategyRevisionState,
)
from app.schemas.intelligence_api import (
    IntelligenceConversionRequest,
    IntelligenceConversionResponse,
    IntelligenceRunRequest,
    IntelligenceRunResponse,
)
from app.services import memory
from app.services import methodology as methodology_service
from app.services.milestone_runner import acquire_engagement_memory_lock

router = APIRouter(tags=["intelligence"])


def _locked_engagement(session: DbSession, slug: str) -> Engagement:
    engagement = session.execute(
        select(Engagement).where(Engagement.slug == slug).with_for_update()
    ).scalar_one_or_none()
    if engagement is None:
        raise HTTPException(status_code=404, detail=f"engagement '{slug}' not found")
    return engagement


def _require_mutable(engagement: Engagement) -> None:
    if engagement.status is not EngagementStatus.active:
        raise HTTPException(
            status_code=409,
            detail=f"engagement is {engagement.status.value}; intelligence is read-only",
        )
    if engagement.work_state is EngagementWorkState.completed:
        raise HTTPException(
            status_code=409,
            detail="completed engagement must be reopened before intelligence runs",
        )


@router.post(
    "/engagements/{slug}/intelligence/convert",
    response_model=IntelligenceConversionResponse,
)
def convert_engagement_to_v3(
    slug: str,
    body: IntelligenceConversionRequest,
    session: DbSession,
    user: CurrentNonGuestUser,
) -> IntelligenceConversionResponse:
    engagement = _locked_engagement(session, slug)
    if engagement.intelligence_architecture is EngagementArchitecture.v3:
        return IntelligenceConversionResponse(
            engagement_id=engagement.id,
            intelligence_architecture=engagement.intelligence_architecture,
            converted_to_v3_at=engagement.converted_to_v3_at,
            methodology_id=engagement.methodology_id,
            phase=engagement.phase,
            seeded_memory_element_ids=[],
            already_converted=True,
        )

    _require_mutable(engagement)
    acquire_engagement_memory_lock(session, engagement.id)
    try:
        methodology_service.select_for_engagement(
            session,
            engagement_id=engagement.id,
            slug=body.methodology_slug,
            version=body.methodology_version,
            actor_type=ActorType.user,
            actor_id=str(user.id),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    finding_count = int(
        session.scalar(
            select(func.count(Finding.id)).where(
                Finding.engagement_id == engagement.id,
                Finding.deleted_at.is_(None),
            )
        )
        or 0
    )
    entity_count = int(
        session.scalar(
            select(func.count(Entity.id)).where(
                Entity.engagement_id == engagement.id,
                Entity.suppressed_at.is_(None),
            )
        )
        or 0
    )
    current_strategy = session.execute(
        select(EngagementStrategyRevision).where(
            EngagementStrategyRevision.engagement_id == engagement.id,
            EngagementStrategyRevision.state == StrategyRevisionState.current,
        )
    ).scalar_one_or_none()
    seed = memory.create_element(
        session,
        engagement_id=engagement.id,
        kind=MemoryKind.decision,
        summary=(
            current_strategy.summary
            if current_strategy and current_strategy.summary
            else "Legacy engagement converted to v3 intelligence"
        ),
        body={
            "conversion_reason": body.reason,
            "legacy_strategy_revision_id": (
                str(current_strategy.id) if current_strategy else None
            ),
            "finding_count_at_conversion": finding_count,
            "entity_count_at_conversion": entity_count,
            "methodology_id": str(engagement.methodology_id),
        },
        author_type=ActorType.user,
        author_id=str(user.id),
    )
    converted_at = datetime.now(tz=UTC)
    engagement.intelligence_architecture = EngagementArchitecture.v3
    engagement.converted_to_v3_at = converted_at
    session.add(
        AuditLog(
            engagement_id=engagement.id,
            actor_type=ActorType.user,
            actor_id=str(user.id),
            event_type="engagement.intelligence_converted",
            payload={
                "from": EngagementArchitecture.legacy.value,
                "to": EngagementArchitecture.v3.value,
                "reason": body.reason,
                "methodology_id": str(engagement.methodology_id),
                "seeded_memory_element_ids": [str(seed.id)],
                "finding_count": finding_count,
                "entity_count": entity_count,
            },
        )
    )
    session.commit()
    return IntelligenceConversionResponse(
        engagement_id=engagement.id,
        intelligence_architecture=engagement.intelligence_architecture,
        converted_to_v3_at=engagement.converted_to_v3_at,
        methodology_id=engagement.methodology_id,
        phase=engagement.phase,
        seeded_memory_element_ids=[seed.id],
        already_converted=False,
    )


@router.post(
    "/engagements/{slug}/intelligence/runs",
    response_model=IntelligenceRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def run_intelligence_on_demand(
    slug: str,
    body: IntelligenceRunRequest,
    session: DbSession,
    user: CurrentNonGuestUser,
) -> IntelligenceRunResponse:
    engagement = _locked_engagement(session, slug)
    _require_mutable(engagement)
    if engagement.intelligence_architecture is not EngagementArchitecture.v3:
        raise HTTPException(
            status_code=409,
            detail="legacy engagement must be converted before v3 intelligence runs",
        )

    now = datetime.now(tz=UTC)
    execution = AgentExecution(
        engagement_id=engagement.id,
        agent=AgentName.engagement_strategist,
        trigger=AgentTrigger.manual,
        input={
            "mode": body.mode.value,
            "engagement_id": str(engagement.id),
            "acting_user_id": str(user.id),
            "v3_intelligence": True,
            "durable_job": True,
        },
        status=AgentExecutionStatus.pending,
        started_at=now,
    )
    session.add(execution)
    session.flush()
    session.add(
        AuditLog(
            engagement_id=engagement.id,
            actor_type=ActorType.user,
            actor_id=str(user.id),
            event_type="intelligence.queued",
            payload={
                "execution_id": str(execution.id),
                "mode": body.mode.value,
                "manual": True,
            },
        )
    )
    session.commit()
    return IntelligenceRunResponse(
        execution_id=execution.id,
        mode=body.mode,
        status=execution.status,
    )
