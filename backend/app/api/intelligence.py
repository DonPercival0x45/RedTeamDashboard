"""Per-engagement v3 activation and analyst-triggered intelligence."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select

from app.agents.intelligence import run_intelligence_analysis
from app.api.deps import CurrentNonGuestUser, DbSession, RedisClient
from app.models import (
    ActorType,
    AgentExecutionStatus,
    AgentPromptMode,
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
from app.services.agent_model_resolver import resolve_llm_for_mode
from app.services.ephemeral_provider_key import NoProviderKeyError
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


def _parsed(value: Any) -> dict[str, Any] | None:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return value
    return None


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
)
def run_intelligence_on_demand(
    slug: str,
    body: IntelligenceRunRequest,
    session: DbSession,
    user: CurrentNonGuestUser,
    redis_client: RedisClient,
) -> IntelligenceRunResponse:
    engagement = _locked_engagement(session, slug)
    _require_mutable(engagement)
    if engagement.intelligence_architecture is not EngagementArchitecture.v3:
        raise HTTPException(
            status_code=409,
            detail="legacy engagement must be converted before v3 intelligence runs",
        )

    acquire_engagement_memory_lock(session, engagement.id)
    if body.mode is AgentPromptMode.coverage_review:
        memory.compact(session, engagement_id=engagement.id)
    try:
        llm, provider, model_name = resolve_llm_for_mode(
            session,
            redis_client=redis_client,
            user_id=user.id,
            engagement_id=engagement.id,
            mode=body.mode,
        )
    except NoProviderKeyError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    parsed, execution = run_intelligence_analysis(
        session,
        engagement_id=engagement.id,
        mode=body.mode,
        acting_user_id=user.id,
        llm=llm,
        model_provider=provider,
        model_name=model_name,
        trigger=AgentTrigger.manual,
    )
    session.add(
        AuditLog(
            engagement_id=engagement.id,
            actor_type=ActorType.user,
            actor_id=str(user.id),
            event_type="intelligence.invoked",
            payload={
                "execution_id": str(execution.id),
                "mode": body.mode.value,
                "status": execution.status.value,
                "manual": True,
            },
        )
    )
    session.commit()
    return IntelligenceRunResponse(
        execution_id=execution.id,
        mode=body.mode,
        status=execution.status,
        parsed=_parsed(parsed),
        error=execution.error if execution.status is AgentExecutionStatus.failed else None,
    )
