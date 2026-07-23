"""Manual Engagement Strategist runs and personal engagement chat."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Response
from sqlalchemy import delete, select
from sqlalchemy.orm import object_session
from sqlalchemy.orm.attributes import flag_modified

from app.api.deps import CurrentNonGuestUser, CurrentUser, DbSession, RedisClient
from app.core.config import settings
from app.models import (
    ActorType,
    AuditLog,
    Conversation,
    ConversationContextType,
    ConversationMessage,
    Engagement,
    EngagementArchitecture,
    EngagementStatus,
    EngagementWorkState,
    Suggestion,
    SuggestionStatus,
)
from app.schemas.engagement_strategist import (
    StrategistActionDecision,
    StrategistActionResult,
    StrategistChatMessageRead,
    StrategistChatRequest,
    StrategistChatResponse,
    StrategistChatState,
    StrategistRunResponse,
    StrategistSummary,
)
from app.services.engagement_strategist import run_engagement_strategist
from app.services.suggestion_router import accept_suggestion

router = APIRouter()


def _engagement(session: DbSession, slug: str) -> Engagement:
    row = session.execute(select(Engagement).where(Engagement.slug == slug)).scalar_one_or_none()
    if row is None or row.status == EngagementStatus.flushed:
        raise HTTPException(status_code=404, detail="engagement not found")
    return row


def _mutable(row: Engagement) -> None:
    session = object_session(row)
    if session is not None:
        session.refresh(row, with_for_update=True)
    if row.status == EngagementStatus.archived:
        raise HTTPException(status_code=409, detail="archived engagement is read-only")
    if row.work_state == EngagementWorkState.completed:
        raise HTTPException(status_code=409, detail="completed engagement is read-only")


def _legacy_only(row: Engagement) -> None:
    if row.intelligence_architecture is EngagementArchitecture.v3:
        raise HTTPException(
            status_code=409,
            detail=(
                "v3 engagement uses the shared intelligence modes; "
                "legacy Engagement Strategist calls are retired"
            ),
        )


def _run(
    mode: str,
    slug: str,
    session: DbSession,
    redis_client: RedisClient,
    user: CurrentNonGuestUser,
) -> StrategistRunResponse:
    engagement = _engagement(session, slug)
    _mutable(engagement)
    _legacy_only(engagement)
    if not settings.engagement_strategist_enabled:
        raise HTTPException(status_code=404, detail="engagement strategist is disabled")
    try:
        execution, output, context_hash, suggestions = run_engagement_strategist(
            session,
            redis_client,
            engagement=engagement,
            acting_user_id=user.id,
            mode=mode,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"strategist run failed: {exc}") from exc
    return StrategistRunResponse(
        execution_id=execution.id,
        context_hash=context_hash,
        output=output,
        suggestion_ids=[row.id for row in suggestions],
    )


@router.post("/engagements/{slug}/strategy/generate-initial", response_model=StrategistRunResponse)
def generate_initial(
    slug: str, session: DbSession, redis_client: RedisClient, user: CurrentNonGuestUser
) -> StrategistRunResponse:
    return _run("generate_initial", slug, session, redis_client, user)


@router.post("/engagements/{slug}/strategy/recommend", response_model=StrategistRunResponse)
def recommend(
    slug: str, session: DbSession, redis_client: RedisClient, user: CurrentNonGuestUser
) -> StrategistRunResponse:
    return _run("recommend", slug, session, redis_client, user)


@router.post("/engagements/{slug}/strategy/reassess", response_model=StrategistRunResponse)
def reassess(
    slug: str, session: DbSession, redis_client: RedisClient, user: CurrentNonGuestUser
) -> StrategistRunResponse:
    return _run("reassess", slug, session, redis_client, user)


@router.post("/engagements/{slug}/strategy/review-completion", response_model=StrategistRunResponse)
def review_completion(
    slug: str, session: DbSession, redis_client: RedisClient, user: CurrentNonGuestUser
) -> StrategistRunResponse:
    return _run("review_completion", slug, session, redis_client, user)


def _latest_conversation(
    session: DbSession, engagement_id: uuid.UUID, user_id: uuid.UUID
) -> Conversation | None:
    return session.execute(
        select(Conversation)
        .where(
            Conversation.engagement_id == engagement_id,
            Conversation.context_type == ConversationContextType.engagement,
            Conversation.created_by_user_id == user_id,
        )
        .order_by(Conversation.updated_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def _messages(session: DbSession, conversation_id: uuid.UUID) -> list[ConversationMessage]:
    return list(
        session.execute(
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == conversation_id)
            .order_by(ConversationMessage.created_at, ConversationMessage.id)
        ).scalars()
    )


@router.get("/engagements/{slug}/strategy/chat", response_model=StrategistChatState)
def get_chat(slug: str, session: DbSession, user: CurrentUser) -> StrategistChatState:
    engagement = _engagement(session, slug)
    conversation = _latest_conversation(session, engagement.id, user.id)
    if conversation is None:
        return StrategistChatState()
    return StrategistChatState(
        conversation_id=conversation.id,
        messages=[
            StrategistChatMessageRead.model_validate(row)
            for row in _messages(session, conversation.id)
        ],
    )


@router.post("/engagements/{slug}/strategy/chat", response_model=StrategistChatResponse)
def post_chat(
    slug: str,
    body: StrategistChatRequest,
    session: DbSession,
    redis_client: RedisClient,
    user: CurrentNonGuestUser,
) -> StrategistChatResponse:
    engagement = _engagement(session, slug)
    _mutable(engagement)
    _legacy_only(engagement)
    if not settings.engagement_strategist_enabled:
        raise HTTPException(status_code=404, detail="engagement strategist is disabled")
    conversation: Conversation | None = None
    if body.conversation_id is not None:
        conversation = session.get(Conversation, body.conversation_id)
        if (
            conversation is None
            or conversation.engagement_id != engagement.id
            or conversation.context_type != ConversationContextType.engagement
            or conversation.created_by_user_id != user.id
        ):
            raise HTTPException(status_code=404, detail="conversation not found")
    if conversation is None:
        conversation = _latest_conversation(session, engagement.id, user.id)
    if conversation is None:
        conversation = Conversation(
            engagement_id=engagement.id,
            finding_id=None,
            context_type=ConversationContextType.engagement,
            created_by_user_id=user.id,
            title=f"Strategy: {engagement.name}"[:200],
        )
        session.add(conversation)
        session.flush()
    user_message = ConversationMessage(
        conversation_id=conversation.id,
        role="user",
        content=body.message.strip(),
    )
    session.add(user_message)
    conversation.updated_at = datetime.now(tz=UTC)
    session.commit()
    session.refresh(user_message)
    history = [
        {"role": row.role, "content": row.content[:4000]}
        for row in _messages(session, conversation.id)[-20:]
    ]
    try:
        execution, output, _context_hash, suggestions = run_engagement_strategist(
            session,
            redis_client,
            engagement=engagement,
            acting_user_id=user.id,
            mode="chat",
            analyst_message=body.message,
            conversation_history=history,
            create_suggestions=True,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"strategist chat failed: {exc}") from exc

    current_engagement = session.execute(
        select(Engagement)
        .where(Engagement.id == engagement.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if current_engagement is None:
        raise HTTPException(status_code=404, detail="engagement not found")
    _mutable(current_engagement)

    actions = [
        {
            "type": "suggestion",
            "suggestion_id": str(suggestion.id),
            "suggestion_kind": suggestion.kind.value,
            "title": suggestion.title,
            "status": "proposed",
        }
        for suggestion in suggestions
    ]
    assistant = ConversationMessage(
        conversation_id=conversation.id,
        role="assistant",
        content=output.situation_summary,
        action_payload={"actions": actions, "analysis": output.model_dump(mode="json")},
        execution_id=execution.id,
    )
    session.add(assistant)
    conversation.updated_at = datetime.now(tz=UTC)
    session.commit()
    session.refresh(assistant)
    return StrategistChatResponse(
        conversation_id=conversation.id,
        user_message=StrategistChatMessageRead.model_validate(user_message),
        assistant_message=StrategistChatMessageRead.model_validate(assistant),
        execution_id=execution.id,
    )


def _action_message(
    session: DbSession,
    slug: str,
    message_id: uuid.UUID,
    user_id: uuid.UUID,
) -> tuple[Engagement, ConversationMessage, list[dict]]:
    engagement = _engagement(session, slug)
    _mutable(engagement)
    message = session.execute(
        select(ConversationMessage)
        .where(ConversationMessage.id == message_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    conversation = session.get(Conversation, message.conversation_id) if message else None
    if (
        message is None
        or conversation is None
        or conversation.engagement_id != engagement.id
        or conversation.context_type != ConversationContextType.engagement
        or conversation.created_by_user_id != user_id
    ):
        raise HTTPException(status_code=404, detail="chat message not found")
    payload = message.action_payload if isinstance(message.action_payload, dict) else {}
    actions = payload.get("actions") if isinstance(payload.get("actions"), list) else []
    return engagement, message, actions


@router.post(
    "/engagements/{slug}/strategy/chat/messages/{message_id}/actions/accept",
    response_model=StrategistActionResult,
)
def accept_action(
    slug: str,
    message_id: uuid.UUID,
    body: StrategistActionDecision,
    session: DbSession,
    redis_client: RedisClient,
    user: CurrentNonGuestUser,
) -> StrategistActionResult:
    engagement, message, actions = _action_message(session, slug, message_id, user.id)
    _mutable(engagement)
    if body.action_index >= len(actions) or not isinstance(actions[body.action_index], dict):
        raise HTTPException(status_code=404, detail="action not found")
    action = dict(actions[body.action_index])
    if action.get("status") == "accepted":
        return StrategistActionResult(
            message=StrategistChatMessageRead.model_validate(message),
            suggestion_id=uuid.UUID(action["suggestion_id"]),
            status="accepted",
        )
    if action.get("status") != "proposed":
        raise HTTPException(status_code=409, detail=f"action is {action.get('status')}")
    try:
        suggestion_id = uuid.UUID(str(action.get("suggestion_id")))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="invalid suggestion action") from exc
    result = accept_suggestion(
        session,
        redis_client,
        suggestion_id=suggestion_id,
        user_id=user.id,
        commit=False,
    )
    if result.suggestion.engagement_id != engagement.id:
        session.rollback()
        raise HTTPException(status_code=422, detail="suggestion crosses engagement")
    action["status"] = "accepted"
    actions[body.action_index] = action
    message.action_payload = {**(message.action_payload or {}), "actions": actions}
    flag_modified(message, "action_payload")
    session.commit()
    session.refresh(message)
    return StrategistActionResult(
        message=StrategistChatMessageRead.model_validate(message),
        suggestion_id=suggestion_id,
        status="accepted",
    )


@router.post(
    "/engagements/{slug}/strategy/chat/messages/{message_id}/actions/deny",
    response_model=StrategistActionResult,
)
def deny_action(
    slug: str,
    message_id: uuid.UUID,
    body: StrategistActionDecision,
    session: DbSession,
    user: CurrentNonGuestUser,
) -> StrategistActionResult:
    engagement, message, actions = _action_message(session, slug, message_id, user.id)
    _mutable(engagement)
    if body.action_index >= len(actions) or not isinstance(actions[body.action_index], dict):
        raise HTTPException(status_code=404, detail="action not found")
    action = dict(actions[body.action_index])
    try:
        suggestion_id = uuid.UUID(str(action.get("suggestion_id")))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="invalid suggestion action") from exc
    if action.get("status") == "denied":
        return StrategistActionResult(
            message=StrategistChatMessageRead.model_validate(message),
            suggestion_id=suggestion_id,
            status="denied",
        )
    if action.get("status") != "proposed":
        raise HTTPException(status_code=409, detail=f"action is {action.get('status')}")
    suggestion = session.execute(
        select(Suggestion).where(Suggestion.id == suggestion_id).with_for_update()
    ).scalar_one_or_none()
    if suggestion is None or suggestion.engagement_id != engagement.id:
        raise HTTPException(status_code=422, detail="invalid suggestion action")
    if suggestion.status == SuggestionStatus.accepted:
        raise HTTPException(
            status_code=409,
            detail="suggestion was already accepted and cannot be denied",
        )

    newly_dismissed = suggestion.status == SuggestionStatus.open
    if newly_dismissed:
        suggestion.status = SuggestionStatus.dismissed
        suggestion.decided_by = user.id
        suggestion.decided_at = datetime.now(tz=UTC)
    action["status"] = "denied"
    actions[body.action_index] = action
    message.action_payload = {**(message.action_payload or {}), "actions": actions}
    flag_modified(message, "action_payload")
    if newly_dismissed:
        session.add(
            AuditLog(
                engagement_id=engagement.id,
                actor_type=ActorType.user,
                actor_id=str(user.id),
                event_type="suggestion.dismissed",
                payload={
                    "suggestion_id": str(suggestion.id),
                    "source": "engagement_strategist_chat",
                },
            )
        )
    session.commit()
    session.refresh(message)
    return StrategistActionResult(
        message=StrategistChatMessageRead.model_validate(message),
        suggestion_id=suggestion_id,
        status="denied",
    )


@router.post("/engagements/{slug}/strategy/chat/summarize", response_model=StrategistSummary)
def summarize_chat(slug: str, session: DbSession, user: CurrentNonGuestUser) -> StrategistSummary:
    engagement = _engagement(session, slug)
    _mutable(engagement)
    conversation = _latest_conversation(session, engagement.id, user.id)
    rows = _messages(session, conversation.id) if conversation else []
    proposed = accepted = denied = 0
    for row in rows:
        payload = row.action_payload if isinstance(row.action_payload, dict) else {}
        for action in payload.get("actions", []):
            status = action.get("status") if isinstance(action, dict) else None
            proposed += status == "proposed"
            accepted += status == "accepted"
            denied += status == "denied"
    summary = (
        f"Strategist conversation with {len(rows)} messages. "
        f"Actions: {accepted} accepted, {denied} denied, "
        f"{proposed} awaiting decision."
    )
    session.add(
        AuditLog(
            engagement_id=engagement.id,
            actor_type=ActorType.user,
            actor_id=str(user.id),
            event_type="engagement.strategy_chat_summarized",
            payload={
                "conversation_id": str(conversation.id) if conversation else None,
                "message_count": len(rows),
                "summary": summary,
            },
        )
    )
    session.commit()
    return StrategistSummary(summary=summary, message_count=len(rows))


@router.delete("/engagements/{slug}/strategy/chat", status_code=204)
def clear_chat(slug: str, session: DbSession, user: CurrentNonGuestUser) -> Response:
    engagement = _engagement(session, slug)
    _mutable(engagement)
    conversations = list(
        session.execute(
            select(Conversation.id).where(
                Conversation.engagement_id == engagement.id,
                Conversation.context_type == ConversationContextType.engagement,
                Conversation.created_by_user_id == user.id,
            )
        ).scalars()
    )
    if conversations:
        session.execute(delete(Conversation).where(Conversation.id.in_(conversations)))
        session.commit()
    return Response(status_code=204)
