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
from sqlalchemy.orm.attributes import flag_modified

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
    FindingPhase,
    FindingStatus,
    FindingSummary,
    Observation,
    ObservationFindingLink,
    ScopeItem,
    Severity,
    Suggestion,
    SuggestionKind,
    SuggestionStatus,
    Tool,
    ToolStatus,
)
from app.orchestrator.llm import default_provider_model
from app.services.ephemeral_provider_key import resolve_for_user
from app.services.finding_activity import build_finding_activity

_SYSTEM_PROMPT = """You are a finding-scoped copilot for an authorized security engagement.
Use only the dossier supplied by the dashboard. Do not invent evidence, targets,
credentials, exploitability, or client impact. If the dossier is thin, say what
is missing and suggest safe next steps.

Return a JSON object only, with this shape:
{
  "answer": "the narrative response to show the analyst",
  "actions": [
    {
      "type": "next_step|tag_incident|add_finding|run_tool|context",
      "title": "short button/card title",
      "description": "why this action is useful",
      "params": {}
    }
  ]
}

Actions are proposals only. Do not claim they have run. For run_tool, propose
only enum/scan style actions, never exploit. If no useful action exists, return
an empty actions array."""


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


_ALLOWED_ACTION_TYPES = {"next_step", "tag_incident", "add_finding", "run_tool", "context"}
_ALLOWED_TASK_KINDS = {"enum", "scan"}
_ALLOWED_SEVERITIES = {"info", "low", "medium", "high", "critical"}
_ALLOWED_PHASES = {"osint", "vuln_scan", "exploit", "phishing", "general"}


def _parse_assistant_payload(raw: Any) -> tuple[str, dict[str, Any] | None]:
    """Extract narrative text + inert action proposals from the LLM response.

    The prompt asks for JSON, but providers occasionally wrap it in prose or
    fences. Treat parse failure as a safe narrative-only response.
    """
    text = (raw if isinstance(raw, str) else str(raw)).strip()
    parsed: Any | None = None
    candidates = [text]
    if "```" in text:
        parts = text.split("```")
        candidates.extend(part.strip().removeprefix("json").strip() for part in parts)
    for candidate in candidates:
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
            break
        except json.JSONDecodeError:
            continue
    if not isinstance(parsed, dict):
        return text, {"actions": [_fallback_next_step(text)]}

    answer = str(parsed.get("answer") or "").strip() or text
    actions = _normalize_actions(parsed.get("actions"))
    if not actions:
        actions = [_fallback_next_step(answer)]
    return answer, {"actions": actions}


def _fallback_next_step(answer: str) -> dict[str, Any]:
    """Safe action bubble when the model answers in prose instead of JSON.

    Providers sometimes ignore the structured-output instruction. Rather than
    hiding Phase-3 entirely, surface one consent-gated note action that captures
    the assistant's recommendation as an open Suggestion if the analyst approves.
    """
    excerpt = answer.strip().replace("\n", " ")[:700]
    return {
        "type": "next_step",
        "title": "Capture assistant recommendation",
        "description": excerpt or "Record this assistant recommendation as a next step.",
        "params": {"source": "fallback_prose_response"},
        "status": "proposed",
    }


def _normalize_actions(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw[:5]:
        if not isinstance(item, dict):
            continue
        typ = str(item.get("type") or "").strip().lower()
        if typ not in _ALLOWED_ACTION_TYPES:
            continue
        params = item.get("params") if isinstance(item.get("params"), dict) else {}
        params = _normalize_action_params(typ, params)
        title = str(item.get("title") or typ.replace("_", " ").title()).strip()[:120]
        description = str(item.get("description") or "").strip()[:1000]
        out.append(
            {
                "type": typ,
                "title": title,
                "description": description,
                "params": params,
                "status": "proposed",
            }
        )
    return out


def _normalize_action_params(typ: str, params: dict[str, Any]) -> dict[str, Any]:
    if typ == "tag_incident":
        tags = params.get("tags") if isinstance(params.get("tags"), list) else []
        return {"tags": _normalize_tags([str(t) for t in tags])[:5]}
    if typ == "add_finding":
        severity = str(params.get("severity") or "info").lower()
        phase = str(params.get("phase") or "general").lower()
        return {
            "title": str(params.get("title") or "AI-suggested finding").strip()[:300],
            "summary": str(params.get("summary") or "").strip()[:4000],
            "severity": severity if severity in _ALLOWED_SEVERITIES else "info",
            "phase": phase if phase in _ALLOWED_PHASES else "general",
            "target": str(params.get("target") or "").strip()[:500] or None,
        }
    if typ == "run_tool":
        task_kind = str(params.get("task_kind") or "enum").lower()
        tool_args = params.get("args") if isinstance(params.get("args"), dict) else {}
        return {
            "tool": str(params.get("tool") or "").strip()[:120],
            "task_kind": task_kind if task_kind in _ALLOWED_TASK_KINDS else "enum",
            "target": str(params.get("target") or "").strip()[:500] or None,
            "args": tool_args,
        }
    return dict(params)


def _normalize_tags(raw: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for tag in raw:
        cleaned = tag.strip()[:40]
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        out.append(cleaned)
    return out[:20]


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
        content, action_payload = _parse_assistant_payload(response.content)
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
            action_payload=action_payload,
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


def accept_chat_action(
    session: Session,
    *,
    finding: Finding,
    message: ConversationMessage,
    action_index: int,
    acting_user_id: uuid.UUID,
) -> tuple[str, dict[str, Any]]:
    """Apply one consent-gated action bubble.

    The LLM only writes inert JSON. This function is the narrow allow-list that
    turns an approved bubble into a dashboard mutation. Destructive/exploit
    dispatch remains out of scope.
    """
    payload = message.action_payload if isinstance(message.action_payload, dict) else {}
    actions = payload.get("actions") if isinstance(payload.get("actions"), list) else []
    if action_index >= len(actions) or not isinstance(actions[action_index], dict):
        raise ValueError("action not found")
    action = dict(actions[action_index])
    if action.get("status") != "proposed":
        raise ValueError(f"action is {action.get('status')}; cannot accept")

    typ = str(action.get("type") or "")
    params = action.get("params") if isinstance(action.get("params"), dict) else {}
    result: dict[str, Any]

    if typ == "tag_incident":
        result = _accept_tag_incident(finding, params)
    elif typ == "add_finding":
        result = _accept_add_finding(session, finding, params, acting_user_id)
    elif typ == "next_step":
        result = _accept_next_step(session, finding, action, acting_user_id)
    elif typ == "run_tool":
        result = _accept_run_tool(session, finding, action, acting_user_id)
    elif typ == "context":
        result = {"noop": True, "message": "Context acknowledged."}
    else:
        raise ValueError(f"unsupported action type: {typ}")

    action["status"] = "accepted"
    action["result"] = result
    actions[action_index] = action
    message.action_payload = {"actions": actions}
    flag_modified(message, "action_payload")
    session.add(message)
    return typ, result


def _accept_tag_incident(finding: Finding, params: dict[str, Any]) -> dict[str, Any]:
    tags = params.get("tags") if isinstance(params.get("tags"), list) else []
    merged = _normalize_tags([*(finding.tags or []), *[str(t) for t in tags]])
    finding.tags = merged
    return {"tags": merged}


def _accept_add_finding(
    session: Session,
    finding: Finding,
    params: dict[str, Any],
    acting_user_id: uuid.UUID,
) -> dict[str, Any]:
    phase = FindingPhase(str(params.get("phase") or "general"))
    severity = Severity(str(params.get("severity") or "info"))
    now = datetime.now(tz=UTC)
    status = (
        FindingStatus.validated
        if phase == FindingPhase.osint
        else FindingStatus.pending_validation
    )
    created = Finding(
        engagement_id=finding.engagement_id,
        title=str(params.get("title") or "AI-suggested finding")[:300],
        summary=str(params.get("summary") or "") or None,
        severity=severity,
        phase=phase,
        target=params.get("target") if params.get("target") else finding.target,
        source_tool="ai_assistant",
        details={
            "source": "finding_chat_action",
            "parent_finding_id": str(finding.id),
            "created_by_user_id": str(acting_user_id),
        },
        status=status,
        validated_at=now if status == FindingStatus.validated else None,
        validated_by=acting_user_id if status == FindingStatus.validated else None,
    )
    session.add(created)
    session.flush()
    return {"finding_id": str(created.id), "title": created.title}


def _accept_next_step(
    session: Session,
    finding: Finding,
    action: dict[str, Any],
    acting_user_id: uuid.UUID,
) -> dict[str, Any]:
    suggestion = Suggestion(
        engagement_id=finding.engagement_id,
        finding_id=finding.id,
        title=str(action.get("title") or "Suggested next step")[:300],
        body=str(action.get("description") or "") or None,
        kind=SuggestionKind.note,
        payload={
            "source": "finding_chat_action",
            "approved_by": str(acting_user_id),
            "params": action.get("params") if isinstance(action.get("params"), dict) else {},
        },
        status=SuggestionStatus.open,
        created_by_agent=AgentName.strategic,
    )
    session.add(suggestion)
    session.flush()
    return {"suggestion_id": str(suggestion.id), "kind": suggestion.kind.value}


def _accept_run_tool(
    session: Session,
    finding: Finding,
    action: dict[str, Any],
    acting_user_id: uuid.UUID,
) -> dict[str, Any]:
    params = action.get("params") if isinstance(action.get("params"), dict) else {}
    tool_name = str(params.get("tool") or "").strip()
    if not tool_name:
        raise ValueError("run_tool action missing tool")
    tool = session.execute(
        select(Tool)
        .where(Tool.name == tool_name, Tool.status == ToolStatus.approved)
        .order_by(Tool.version.desc())
        .limit(1)
    ).scalar_one_or_none()
    if tool is None:
        raise ValueError(f"approved tool not found: {tool_name}")
    task_kind = str(params.get("task_kind") or "enum")
    if task_kind not in _ALLOWED_TASK_KINDS:
        raise ValueError("run_tool action must be enum or scan")
    suggestion = Suggestion(
        engagement_id=finding.engagement_id,
        finding_id=finding.id,
        title=str(action.get("title") or f"Run {tool_name}")[:300],
        body=str(action.get("description") or "") or None,
        kind=SuggestionKind.task,
        payload={
            "source": "finding_chat_action",
            "approved_by": str(acting_user_id),
            "tool": tool_name,
            "tool_version": tool.version,
            "target": params.get("target") or finding.target,
            "args": params.get("args") if isinstance(params.get("args"), dict) else {},
            "task_kind": task_kind,
            "owner_eligibility": "agent",
        },
        status=SuggestionStatus.open,
        created_by_agent=AgentName.strategic,
    )
    session.add(suggestion)
    session.flush()
    return {
        "suggestion_id": str(suggestion.id),
        "kind": suggestion.kind.value,
        "note": "Created an open suggestion; accept it to dispatch through the existing gate.",
    }
