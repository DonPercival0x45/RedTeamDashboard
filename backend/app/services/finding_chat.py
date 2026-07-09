"""Finding-scoped AI assistant (Phase 2 of the pane of glass).

This service assembles a read-only dossier for one finding and asks the
analyst's BYO LLM for a narrative response. It deliberately does NOT run tools,
mutate findings, or create tasks. Phase 3 will add consent-gated action bubbles
that route approvals through existing suggestion/task/finding APIs.
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.strategic import _extract_usage, _make_chat_model
from app.core import pricing
from app.models import (
    AgentExecution,
    AgentExecutionStatus,
    AgentName,
    AgentTrigger,
    Attachment,
    Conversation,
    ConversationMessage,
    Engagement,
    Finding,
    FindingSummary,
    Observation,
    ObservationFindingLink,
    ScopeItem,
    Suggestion,
)
from app.orchestrator.llm import default_provider_model
from app.services.ephemeral_provider_key import resolve_for_user
from app.services.finding_activity import build_finding_activity

_SYSTEM_PROMPT = """You are a finding-scoped copilot for an authorized security engagement.
Use only the dossier supplied by the dashboard. Do not invent evidence, targets,
credentials, exploitability, or client impact. If the dossier is thin, say what
is missing and suggest safe next steps.

You may recommend analyst actions, but you cannot execute tools or change data in
this phase. Keep recommendations consent-gated: phrase them as suggestions the
analyst can approve later. Do not provide exploit instructions. Prefer concise,
practical paragraphs and short bullet lists when useful."""


def get_latest_conversation(
    session: Session,
    *,
    finding_id: uuid.UUID,
    user_id: uuid.UUID,
) -> Conversation | None:
    return session.execute(
        select(Conversation)
        .where(
            Conversation.finding_id == finding_id,
            Conversation.created_by_user_id == user_id,
        )
        .order_by(Conversation.updated_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def get_conversation_messages(
    session: Session,
    conversation_id: uuid.UUID,
) -> list[ConversationMessage]:
    return list(
        session.execute(
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == conversation_id)
            .order_by(ConversationMessage.created_at.asc())
        ).scalars()
    )


def get_or_create_conversation(
    session: Session,
    *,
    finding: Finding,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID | None = None,
) -> Conversation:
    if conversation_id is not None:
        conv = session.get(Conversation, conversation_id)
        if conv is None or conv.finding_id != finding.id:
            raise ValueError("conversation not found for this finding")
        return conv

    conv = get_latest_conversation(session, finding_id=finding.id, user_id=user_id)
    if conv is not None:
        return conv

    title = finding.title[:180]
    conv = Conversation(
        engagement_id=finding.engagement_id,
        finding_id=finding.id,
        created_by_user_id=user_id,
        title=title,
    )
    session.add(conv)
    session.commit()
    session.refresh(conv)
    return conv


def build_finding_dossier(session: Session, finding: Finding) -> dict[str, Any]:
    """Collect the read-only context the chatbot is allowed to use."""
    engagement = session.get(Engagement, finding.engagement_id)
    scope = list(
        session.execute(
            select(ScopeItem).where(ScopeItem.engagement_id == finding.engagement_id)
        ).scalars()
    )
    summaries = list(
        session.execute(
            select(FindingSummary)
            .where(FindingSummary.finding_id == finding.id)
            .order_by(FindingSummary.created_at.desc())
            .limit(5)
        ).scalars()
    )
    observations = list(
        session.execute(
            select(Observation)
            .join(
                ObservationFindingLink,
                ObservationFindingLink.observation_id == Observation.id,
            )
            .where(ObservationFindingLink.finding_id == finding.id)
            .order_by(Observation.created_at.desc())
            .limit(10)
        ).scalars()
    )
    suggestions = list(
        session.execute(
            select(Suggestion)
            .where(Suggestion.finding_id == finding.id)
            .order_by(Suggestion.created_at.desc())
            .limit(10)
        ).scalars()
    )
    attachments = list(
        session.execute(
            select(Attachment)
            .where(Attachment.finding_id == finding.id)
            .order_by(Attachment.created_at.desc())
            .limit(10)
        ).scalars()
    )

    return {
        "engagement": {
            "id": str(finding.engagement_id),
            "slug": engagement.slug if engagement else None,
            "name": engagement.name if engagement else None,
        },
        "finding": {
            "id": str(finding.id),
            "title": finding.title,
            "severity": finding.severity.value,
            "status": finding.status.value,
            "phase": finding.phase.value,
            "target": finding.target,
            "source_tool": finding.source_tool,
            "summary": finding.summary,
            "details": finding.details,
            "tags": finding.tags or [],
            "created_at": finding.created_at.isoformat(),
            "observed_at": finding.observed_at.isoformat()
            if finding.observed_at
            else None,
        },
        "activity": build_finding_activity(session, finding.id)[:25],
        "scope": [
            {
                "kind": item.kind.value,
                "value": item.value,
                "is_exclusion": item.is_exclusion,
                "source": getattr(item, "source", None),
            }
            for item in scope[:50]
        ],
        "summary_history": [
            {
                "body": s.body,
                "author_user_id": str(s.author_user_id) if s.author_user_id else None,
                "created_at": s.created_at.isoformat(),
            }
            for s in summaries
        ],
        "observations": [
            {
                "id": str(o.id),
                "content": o.content,
                "phase": o.phase.value if o.phase else None,
                "created_at": o.created_at.isoformat(),
            }
            for o in observations
        ],
        "suggestions": [
            {
                "id": str(s.id),
                "title": s.title,
                "body": s.body,
                "kind": s.kind.value,
                "status": s.status.value,
                "payload": s.payload,
                "created_at": s.created_at.isoformat(),
            }
            for s in suggestions
        ],
        "attachments": [
            {
                "id": str(a.id),
                "filename": a.filename,
                "content_type": a.content_type,
                "size_bytes": a.size_bytes,
                "created_at": a.created_at.isoformat(),
            }
            for a in attachments
        ],
    }


def _conversation_history_prompt(messages: list[ConversationMessage]) -> str:
    clipped = messages[-12:]
    return "\n".join(
        f"{m.role.upper()}: {m.content[:2000]}" for m in clipped if m.content.strip()
    )


def generate_finding_chat_reply(
    session: Session,
    redis_client: Any,
    *,
    finding: Finding,
    conversation: Conversation,
    acting_user_id: uuid.UUID,
) -> tuple[AgentExecution, ConversationMessage]:
    """Ask the BYO LLM for the assistant response and persist the bubble."""
    provider, model_name = default_provider_model()
    resolved = resolve_for_user(redis_client, user_id=acting_user_id, provider=provider)
    llm = _make_chat_model(
        provider,
        model_name,
        api_key=resolved.api_key,
        endpoint=resolved.endpoint,
    )

    dossier = build_finding_dossier(session, finding)
    messages = get_conversation_messages(session, conversation.id)
    prompt = (
        "Finding dossier JSON:\n"
        f"{json.dumps(dossier, default=str, indent=2)[:30000]}\n\n"
        "Conversation so far:\n"
        f"{_conversation_history_prompt(messages) or '(first turn)'}\n\n"
        "Respond to the analyst's latest message."
    )

    execution = AgentExecution(
        engagement_id=finding.engagement_id,
        agent=AgentName.strategic,
        trigger=AgentTrigger.manual,
        input={
            "finding_id": str(finding.id),
            "conversation_id": str(conversation.id),
            "mode": "finding_chat",
        },
        model_provider=provider,
        model_name=model_name,
        status=AgentExecutionStatus.running,
        started_at=datetime.now(tz=UTC),
    )
    session.add(execution)
    session.commit()
    session.refresh(execution)

    try:
        response = llm.invoke([("system", _SYSTEM_PROMPT), ("user", prompt)])
        raw = response.content
        content = (raw if isinstance(raw, str) else str(raw)).strip()
        tokens_in, tokens_out = _extract_usage(response)
        execution.status = AgentExecutionStatus.completed
        execution.completed_at = datetime.now(tz=UTC)
        execution.tokens_in = tokens_in
        execution.tokens_out = tokens_out
        execution.cost_usd = pricing.cost_usd(
            model_name, tokens_in, tokens_out, provider=provider
        )
        execution.output = {"message_chars": len(content)}
        assistant = ConversationMessage(
            conversation_id=conversation.id,
            role="assistant",
            content=content,
            execution_id=execution.id,
        )
        conversation.updated_at = datetime.now(tz=UTC)
        session.add(assistant)
        session.commit()
        session.refresh(execution)
        session.refresh(assistant)
        return execution, assistant
    except Exception as exc:
        execution.status = AgentExecutionStatus.failed
        execution.completed_at = datetime.now(tz=UTC)
        execution.error = str(exc)[:1000]
        session.commit()
        raise
