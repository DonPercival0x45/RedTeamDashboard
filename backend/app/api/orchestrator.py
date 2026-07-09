"""HTTP surface for the Phase 9 orchestrator.

This module provides the API for the Strategic and Tactical orchestrator agents
that assist analysts during authorized security engagements.

Agents perform **enumeration and scanning only**. They analyze findings and suggest
tasks, but validation/proof-of-concept work (TaskKind.exploit) is **analyst-only**
— the service layer refuses to dispatch such tasks to agents. All agent actions
are audit-logged via AgentExecution records.

Endpoints::

    POST   /findings/{finding_id}/analyze              -> Strategic on demand
    GET    /engagements/{slug}/suggestions             -> list (?status filter)
    POST   /suggestions/{suggestion_id}/accept         -> mint Task (+ dispatch)
    POST   /suggestions/{suggestion_id}/dismiss        -> close without acting
    GET    /engagements/{slug}/tasks                   -> list (?status filter)

Accept implicitly dispatches when the suggestion's task would be agent-eligible
(scan/enum + owner_eligibility != analyst). The dispatched run lands on the
worker's existing inbound stream and goes through the same approval gate as a
hand-started run, so an active tool still pauses for an analyst decision.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents import StrategicAgent, TacticalAgent, TacticalRefusedExploit
from app.api.deps import CurrentNonGuestUser, CurrentUser, DbSession, RedisClient
from app.core import pricing
from app.models import (
    ActorType,
    AgentExecution,
    AgentExecutionStatus,
    AgentName,
    AgentTrigger,
    AuditLog,
    Conversation,
    ConversationMessage,
    Engagement,
    Finding,
    OwnerEligibility,
    Suggestion,
    SuggestionKind,
    SuggestionStatus,
    Task,
    TaskKind,
    TaskStatus,
    Tool,
    ToolInvocation,
)
from app.schemas.cost import (
    AgentCost,
    CostBucket,
    CostRollup,
    ModelCost,
    ToolCost,
    ToolCostSummary,
)
from app.schemas.finding import FindingRead
from app.schemas.orchestrator import (
    AcceptSuggestionResponse,
    AnalyzeFindingResponse,
    FindingActivityEntry,
    FindingChatActionRequest,
    FindingChatActionResponse,
    FindingChatMessageRead,
    FindingChatRequest,
    FindingChatResponse,
    FindingChatState,
    SuggestionRead,
    TaskRead,
    TriageFindingResponse,
)
from app.services.status_notifier import notify_status_event

router = APIRouter()


def _engagement_by_slug(session: Session, slug: str) -> Engagement:
    eng = session.execute(
        select(Engagement).where(Engagement.slug == slug)
    ).scalar_one_or_none()
    if eng is None:
        raise HTTPException(status_code=404, detail=f"engagement '{slug}' not found")
    return eng


# ---------------------------------------------------------------------------
# Analyze a finding (manual Strategic trigger)
# ---------------------------------------------------------------------------


@router.post(
    "/findings/{finding_id}/analyze",
    response_model=AnalyzeFindingResponse,
)
def analyze_finding(
    finding_id: uuid.UUID,
    session: DbSession,
    redis_client: RedisClient,
    user: CurrentNonGuestUser,
) -> AnalyzeFindingResponse:
    """Run the Strategic watcher synchronously over one finding.

    Used by the findings slide-over's Agent button: the analyst clicks,
    Strategic plans, suggestions render inline. The event-driven path (worker
    subscriber) writes to the same tables out-of-band.

    The BYO key resolves against the CLICKING analyst's ephemeral Redis
    cache — not the engagement creator's.
    """
    finding = session.get(Finding, finding_id)
    if finding is None:
        raise HTTPException(status_code=404, detail="finding not found")

    agent = StrategicAgent(redis_client=redis_client)
    execution, suggestions = agent.analyze_finding(
        session,
        finding=finding,
        trigger=AgentTrigger.manual,
        acting_user_id=user.id,
    )

    session.add(
        AuditLog(
            engagement_id=finding.engagement_id,
            actor_type=ActorType.user,
            actor_id=str(user.id),
            event_type="strategic.analyzed",
            payload={
                "finding_id": str(finding.id),
                "execution_id": str(execution.id),
                "suggestion_count": len(suggestions),
            },
        )
    )
    session.commit()
    for s in suggestions:
        session.refresh(s)

    # v0.8: Strategic catches its own LLM errors and stamps execution
    # status. Ping Discord if the run failed (no-op when no integration).
    if execution.status == AgentExecutionStatus.failed:
        eng = session.get(Engagement, finding.engagement_id)
        notify_status_event(
            session,
            kind="agent",
            title=f"Strategic failed on: {finding.title}",
            status="failed",
            detail=(execution.error or "")[:500],
            engagement_slug=eng.slug if eng else None,
        )

    return AnalyzeFindingResponse(
        execution_id=execution.id,
        suggestions=[SuggestionRead.model_validate(s) for s in suggestions],
    )


# ---------------------------------------------------------------------------
# Triage a finding (LLM-written summary for the slide-over textarea)
# ---------------------------------------------------------------------------


@router.get(
    "/findings/{finding_id}",
    response_model=FindingRead,
)
def get_finding(
    finding_id: uuid.UUID,
    session: DbSession,
    _user: CurrentUser,
) -> FindingRead:
    """Single-finding read for the finding pane (deep-linkable full page).

    The engagement-scoped list stays the source of truth for the table;
    this is the cross-engagement fetch the pane needs so it can render
    from just the finding id in the URL.
    """
    finding = session.get(Finding, finding_id)
    if finding is None:
        raise HTTPException(status_code=404, detail="finding not found")
    # Return the ORM row; FastAPI's response_model validates with
    # from_attributes=True (FindingRead has no explicit model_config).
    return finding


@router.get(
    "/findings/{finding_id}/activity",
    response_model=list[FindingActivityEntry],
)
def finding_activity(
    finding_id: uuid.UUID,
    session: DbSession,
    _user: CurrentUser,
) -> list[FindingActivityEntry]:
    """Activity timeline for the finding pane of glass (Phase 1).

    Merges creation origin, finding-scoped Tasks, agent executions that
    reference the finding, and audit-log events into one chronological
    feed. Read-only — the pane renders it as the "what's happened here"
    log alongside the summary/observations/evidence sections.
    """
    from app.services.finding_activity import build_finding_activity

    finding = session.get(Finding, finding_id)
    if finding is None:
        raise HTTPException(status_code=404, detail="finding not found")
    rows = build_finding_activity(session, finding_id)
    return [FindingActivityEntry(**r) for r in rows]


@router.get(
    "/findings/{finding_id}/chat",
    response_model=FindingChatState,
)
def finding_chat_state(
    finding_id: uuid.UUID,
    session: DbSession,
    user: CurrentUser,
) -> FindingChatState:
    """Return the current analyst's persisted chat bubbles for a finding.

    Read-only and non-mutating: if the analyst has not started a conversation
    yet, the pane receives an empty message list and no conversation id.
    """
    from app.services.finding_chat import (
        get_conversation_messages,
        get_latest_conversation,
    )

    finding = session.get(Finding, finding_id)
    if finding is None:
        raise HTTPException(status_code=404, detail="finding not found")

    conv = get_latest_conversation(session, finding_id=finding_id, user_id=user.id)
    if conv is None:
        return FindingChatState(conversation_id=None, messages=[])
    messages = get_conversation_messages(session, conv.id)
    return FindingChatState(
        conversation_id=conv.id,
        messages=[FindingChatMessageRead.model_validate(m) for m in messages],
    )


@router.post(
    "/findings/{finding_id}/chat",
    response_model=FindingChatResponse,
)
def ask_finding_chat(
    finding_id: uuid.UUID,
    body: FindingChatRequest,
    session: DbSession,
    redis_client: RedisClient,
    user: CurrentNonGuestUser,
) -> FindingChatResponse:
    """Ask the finding-scoped AI assistant one question.

    Phase 2 is deliberately narrative-only: the assistant can suggest actions,
    but the endpoint does not run tools, add findings, or mutate tags. The
    user's prompt and assistant response are persisted as chat bubbles.
    """
    from app.services.ephemeral_provider_key import NoProviderKeyError
    from app.services.finding_chat import (
        generate_finding_chat_reply,
        get_or_create_conversation,
    )

    finding = session.get(Finding, finding_id)
    if finding is None:
        raise HTTPException(status_code=404, detail="finding not found")

    text = body.message.strip()
    if not text:
        raise HTTPException(status_code=400, detail="message is empty")

    try:
        conv = get_or_create_conversation(
            session,
            finding=finding,
            user_id=user.id,
            conversation_id=body.conversation_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if conv.created_by_user_id is not None and conv.created_by_user_id != user.id:
        raise HTTPException(status_code=403, detail="conversation belongs to another user")

    user_message = ConversationMessage(
        conversation_id=conv.id,
        role="user",
        content=text,
    )
    conv.updated_at = datetime.now(tz=UTC)
    session.add(user_message)
    session.commit()
    session.refresh(user_message)

    try:
        execution, assistant_message = generate_finding_chat_reply(
            session,
            redis_client,
            finding=finding,
            conversation=conv,
            acting_user_id=user.id,
        )
    except NoProviderKeyError as exc:
        session.rollback()
        raise HTTPException(
            status_code=400,
            detail=(
                f"No provider key configured for the LLM call ({exc}). "
                "Add one under /settings/keys."
            ),
        ) from exc
    except Exception as exc:
        session.rollback()
        raise HTTPException(status_code=502, detail=f"chat failed: {exc}") from exc

    session.add(
        AuditLog(
            engagement_id=finding.engagement_id,
            actor_type=ActorType.user,
            actor_id=str(user.id),
            event_type="finding.chat_asked",
            payload={
                "finding_id": str(finding.id),
                "conversation_id": str(conv.id),
                "message_id": str(assistant_message.id),
                "execution_id": str(execution.id),
            },
        )
    )
    session.commit()

    return FindingChatResponse(
        conversation_id=conv.id,
        user_message=FindingChatMessageRead.model_validate(user_message),
        assistant_message=FindingChatMessageRead.model_validate(assistant_message),
        execution_id=execution.id,
    )


@router.post(
    "/findings/{finding_id}/chat/messages/{message_id}/actions/accept",
    response_model=FindingChatActionResponse,
)
def accept_finding_chat_action(
    finding_id: uuid.UUID,
    message_id: uuid.UUID,
    body: FindingChatActionRequest,
    session: DbSession,
    redis_client: RedisClient,
    user: CurrentNonGuestUser,
) -> FindingChatActionResponse:
    """Approve one inert assistant action bubble.

    This is the Phase-3 consent gate: LLM output is just JSON on a chat bubble
    until the analyst clicks approve, then this allow-listed endpoint dispatches
    executable enum/scan tool actions through the existing Tactical path.
    """
    from app.services.finding_chat import accept_chat_action

    finding = session.get(Finding, finding_id)
    if finding is None:
        raise HTTPException(status_code=404, detail="finding not found")
    message = session.get(ConversationMessage, message_id)
    if message is None:
        raise HTTPException(status_code=404, detail="message not found")
    conv = session.get(Conversation, message.conversation_id)
    if conv is None or conv.finding_id != finding.id:
        raise HTTPException(status_code=404, detail="message not found for this finding")
    if conv.created_by_user_id is not None and conv.created_by_user_id != user.id:
        raise HTTPException(status_code=403, detail="conversation belongs to another user")

    try:
        action_type, result = accept_chat_action(
            session,
            finding=finding,
            message=message,
            action_index=body.action_index,
            acting_user_id=user.id,
            redis_client=redis_client,
        )
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    session.add(
        AuditLog(
            engagement_id=finding.engagement_id,
            actor_type=ActorType.user,
            actor_id=str(user.id),
            event_type="finding.chat_action.accepted",
            payload={
                "finding_id": str(finding.id),
                "conversation_id": str(conv.id),
                "message_id": str(message.id),
                "action_index": body.action_index,
                "action_type": action_type,
                "result": result,
            },
        )
    )
    session.commit()
    session.refresh(message)
    return FindingChatActionResponse(
        message=FindingChatMessageRead.model_validate(message),
        action_index=body.action_index,
        action_type=action_type,
        status="accepted",
        result=result,
    )


@router.post(
    "/findings/{finding_id}/triage",
    response_model=TriageFindingResponse,
)
def triage_finding(
    finding_id: uuid.UUID,
    session: DbSession,
    redis_client: RedisClient,
    user: CurrentNonGuestUser,
) -> TriageFindingResponse:
    """Generate an analyst-facing summary for a finding via the LLM.

    Wired to the "AI Triage" button in the Findings slide-over: the
    response's ``summary`` drops into the Summary textarea, the analyst
    edits and saves manually (this endpoint does NOT mutate
    ``findings.summary``). One ``AgentExecution`` row is written so the
    Costs tab roll-up keeps a single accounting view of LLM spend.

    BYO key resolves against the clicking analyst's ephemeral Redis
    cache. A missing key surfaces as a 400 pointing at /settings/keys.
    """
    from app.services.ephemeral_provider_key import NoProviderKeyError
    from app.services.triage import triage_finding_summary

    finding = session.get(Finding, finding_id)
    if finding is None:
        raise HTTPException(status_code=404, detail="finding not found")

    try:
        execution, summary = triage_finding_summary(
            session,
            redis_client,
            finding=finding,
            acting_user_id=user.id,
        )
    except NoProviderKeyError as exc:
        session.rollback()
        raise HTTPException(
            status_code=400,
            detail=(
                f"No provider key configured for the LLM call ({exc}). "
                "Add one under /settings/keys."
            ),
        ) from exc
    except Exception as exc:
        # The service marked the AgentExecution row failed before re-raising,
        # so a row exists for the Costs tab to surface the failed call. Commit
        # that row so the analyst can see it, then return 502.
        session.commit()
        # v0.8: ping Discord on agent failure (no-op if integration not set).
        eng = session.get(Engagement, finding.engagement_id)
        notify_status_event(
            session,
            kind="agent",
            title=f"Triage failed: {finding.title}",
            status="failed",
            detail=str(exc)[:500],
            engagement_slug=eng.slug if eng else None,
        )
        raise HTTPException(
            status_code=502, detail=f"triage failed: {exc}"
        ) from exc

    session.add(
        AuditLog(
            engagement_id=finding.engagement_id,
            actor_type=ActorType.user,
            actor_id=str(user.id),
            event_type="finding.triaged",
            payload={
                "finding_id": str(finding.id),
                "execution_id": str(execution.id),
                "summary_chars": len(summary),
            },
        )
    )
    session.commit()

    return TriageFindingResponse(
        execution_id=execution.id,
        summary=summary,
    )


# ---------------------------------------------------------------------------
# Suggestions
# ---------------------------------------------------------------------------


@router.get(
    "/engagements/{slug}/suggestions",
    response_model=list[SuggestionRead],
)
def list_suggestions(
    slug: str,
    session: DbSession,
    _user: CurrentUser,
    suggestion_status: Annotated[
        SuggestionStatus | None,
        Query(alias="status", description="Filter by status (default: open)."),
    ] = SuggestionStatus.open,
) -> list[Suggestion]:
    eng = _engagement_by_slug(session, slug)
    stmt = select(Suggestion).where(Suggestion.engagement_id == eng.id)
    if suggestion_status is not None:
        stmt = stmt.where(Suggestion.status == suggestion_status)
    stmt = stmt.order_by(Suggestion.created_at.desc())
    return list(session.execute(stmt).scalars())


@router.post(
    "/suggestions/{suggestion_id}/accept",
    response_model=AcceptSuggestionResponse,
)
def accept_suggestion(
    suggestion_id: uuid.UUID,
    session: DbSession,
    redis_client: RedisClient,
    user: CurrentNonGuestUser,
) -> AcceptSuggestionResponse:
    """Accept a Strategic suggestion.

    For ``kind=task`` suggestions: mint a ``Task`` row, then (if it's agent-
    eligible scan/enum) ask Tactical to dispatch it immediately. The dispatched
    run still hits the existing approval gate for active tools.
    """
    suggestion = session.get(Suggestion, suggestion_id)
    if suggestion is None:
        raise HTTPException(status_code=404, detail="suggestion not found")
    if suggestion.status != SuggestionStatus.open:
        raise HTTPException(
            status_code=409,
            detail=f"suggestion is {suggestion.status.value}; cannot accept",
        )

    suggestion.status = SuggestionStatus.accepted
    suggestion.decided_by = user.id
    suggestion.decided_at = datetime.now(tz=UTC)

    task: Task | None = None
    dispatched = False

    if suggestion.kind == SuggestionKind.task:
        payload = dict(suggestion.payload or {})
        kind_raw = payload.get("task_kind") or TaskKind.enum.value
        owner_raw = payload.get("owner_eligibility") or OwnerEligibility.either.value
        try:
            task_kind = TaskKind(kind_raw)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"invalid task_kind on suggestion payload: {kind_raw!r}",
            ) from exc
        try:
            owner_eligibility = OwnerEligibility(owner_raw)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"invalid owner_eligibility: {owner_raw!r}",
            ) from exc

        task = Task(
            engagement_id=suggestion.engagement_id,
            finding_id=suggestion.finding_id,
            title=suggestion.title,
            kind=task_kind,
            owner_eligibility=owner_eligibility,
            status=TaskStatus.pending,
            payload=payload,
        )
        session.add(task)
        session.flush()
        suggestion.task_id = task.id

        # Auto-dispatch agent-eligible scan/enum tasks. Analyst-only or
        # exploit tasks stay pending for manual action.
        agent_eligible_owner = owner_eligibility in (
            OwnerEligibility.agent,
            OwnerEligibility.either,
        )
        agent_eligible_kind = task_kind in (TaskKind.scan, TaskKind.enum)
        if agent_eligible_owner and agent_eligible_kind:
            tactical = TacticalAgent(redis_client)
            try:
                tactical.dispatch(
                    session,
                    task=task,
                    trigger=AgentTrigger.manual,
                    acting_user_id=user.id,
                )
                dispatched = True
            except TacticalRefusedExploit:
                # Defense-in-depth — shouldn't fire since we checked kind, but
                # if it ever does, swallow and leave the task pending.
                dispatched = False

    session.add(
        AuditLog(
            engagement_id=suggestion.engagement_id,
            actor_type=ActorType.user,
            actor_id=str(user.id),
            event_type="suggestion.accepted",
            payload={
                "suggestion_id": str(suggestion.id),
                "task_id": str(task.id) if task else None,
                "dispatched": dispatched,
            },
        )
    )
    session.commit()
    session.refresh(suggestion)
    if task is not None:
        session.refresh(task)

    return AcceptSuggestionResponse(
        suggestion=SuggestionRead.model_validate(suggestion),
        task=TaskRead.model_validate(task) if task else None,
        dispatched=dispatched,
    )


@router.post(
    "/suggestions/{suggestion_id}/dismiss",
    response_model=SuggestionRead,
)
def dismiss_suggestion(
    suggestion_id: uuid.UUID,
    session: DbSession,
    user: CurrentNonGuestUser,
) -> Suggestion:
    suggestion = session.get(Suggestion, suggestion_id)
    if suggestion is None:
        raise HTTPException(status_code=404, detail="suggestion not found")
    if suggestion.status != SuggestionStatus.open:
        raise HTTPException(
            status_code=409,
            detail=f"suggestion is {suggestion.status.value}; cannot dismiss",
        )
    suggestion.status = SuggestionStatus.dismissed
    suggestion.decided_by = user.id
    suggestion.decided_at = datetime.now(tz=UTC)
    session.add(
        AuditLog(
            engagement_id=suggestion.engagement_id,
            actor_type=ActorType.user,
            actor_id=str(user.id),
            event_type="suggestion.dismissed",
            payload={"suggestion_id": str(suggestion.id)},
        )
    )
    session.commit()
    session.refresh(suggestion)
    return suggestion


# ---------------------------------------------------------------------------
# Tasks (read-only for now; mutation happens via accept/dismiss)
# ---------------------------------------------------------------------------


@router.get("/engagements/{slug}/tasks", response_model=list[TaskRead])
def list_tasks(
    slug: str,
    session: DbSession,
    _user: CurrentUser,
    task_status: Annotated[
        TaskStatus | None,
        Query(alias="status", description="Filter by task status."),
    ] = None,
) -> list[Task]:
    eng = _engagement_by_slug(session, slug)
    stmt = select(Task).where(Task.engagement_id == eng.id)
    if task_status is not None:
        stmt = stmt.where(Task.status == task_status)
    stmt = stmt.order_by(Task.created_at.desc())
    return list(session.execute(stmt).scalars())


# ---------------------------------------------------------------------------
# Costs (Phase 11) — per-engagement LLM spend roll-up over agent_executions
# ---------------------------------------------------------------------------


def _new_bucket() -> dict:
    return {"executions": 0, "tokens_in": 0, "tokens_out": 0, "cost": Decimal(0)}


def _as_bucket(acc: dict) -> CostBucket:
    return CostBucket(
        executions=acc["executions"],
        tokens_in=acc["tokens_in"],
        tokens_out=acc["tokens_out"],
        cost_usd=float(acc["cost"]),
    )


@router.get("/engagements/{slug}/costs", response_model=CostRollup)
def engagement_costs(
    slug: str,
    session: DbSession,
    _user: CurrentUser,
) -> CostRollup:
    """Roll up every Strategic/Tactical LLM call for the engagement by agent and
    by model, summing tokens and deriving USD via ``app.core.pricing``. Models
    with no pricing entry are counted but contribute $0 and are surfaced in
    ``unpriced_models`` so the UI can flag them."""
    eng = _engagement_by_slug(session, slug)
    rows = list(
        session.execute(
            select(AgentExecution).where(AgentExecution.engagement_id == eng.id)
        ).scalars()
    )

    total = _new_bucket()
    by_agent: dict[AgentName, dict] = {}
    by_model: dict[tuple[str | None, str | None], dict] = {}
    unpriced: set[str] = set()

    for ex in rows:
        used_in = ex.tokens_in or 0
        used_out = ex.tokens_out or 0
        derived = pricing.cost_usd(
            ex.model_name, used_in, used_out, ex.model_provider
        )
        priced = derived is not None
        cost = derived if priced else Decimal(0)
        if not priced and ex.model_name and (used_in or used_out):
            unpriced.add(ex.model_name)

        agent_acc = by_agent.setdefault(ex.agent, _new_bucket())
        model_acc = by_model.setdefault(
            (ex.model_provider, ex.model_name), _new_bucket()
        )
        for acc in (total, agent_acc, model_acc):
            acc["executions"] += 1
            acc["tokens_in"] += used_in
            acc["tokens_out"] += used_out
            acc["cost"] += cost
        model_acc["priced"] = priced

    # v0.15.0: fold in per-tool invocation compute cost. tool_invocations
    # carry a cost_usd stamped by the orchestrator (LocalDocker=$0,
    # ACI≈$2e-5/s). Aggregate by tool_id and expose as its own block on
    # the Costs response.
    tool_rows = list(
        session.execute(
            select(ToolInvocation, Tool.name)
            .join(Tool, Tool.id == ToolInvocation.tool_id)
            .where(ToolInvocation.engagement_id == eng.id)
        ).all()
    )
    tool_summary = _summarise_tool_invocations(tool_rows)

    return CostRollup(
        engagement_id=eng.id,
        engagement_slug=eng.slug,
        total=_as_bucket(total),
        by_agent=[
            AgentCost(agent=agent, **_as_bucket(acc).model_dump())
            for agent, acc in sorted(
                by_agent.items(), key=lambda kv: kv[1]["cost"], reverse=True
            )
        ],
        by_model=[
            ModelCost(
                provider=key[0],
                model=key[1],
                priced=acc.get("priced", False),
                **_as_bucket(acc).model_dump(),
            )
            for key, acc in sorted(
                by_model.items(), key=lambda kv: kv[1]["cost"], reverse=True
            )
        ],
        unpriced_models=sorted(unpriced),
        tools=tool_summary,
    )


def _summarise_tool_invocations(
    rows: list[tuple[ToolInvocation, str]],
) -> ToolCostSummary:
    """Aggregate per-tool: count of invocations, total wall-clock, sum
    of cost_usd. Sort by cost descending so the Costs tab shows the
    heavy spenders first."""
    total_invocations = 0
    total_seconds = 0.0
    total_cost = Decimal(0)
    per_tool: dict[Any, dict[str, Any]] = {}
    for row, tool_name in rows:
        seconds = 0.0
        if row.completed_at and row.started_at:
            seconds = (row.completed_at - row.started_at).total_seconds()
        cost = row.cost_usd if row.cost_usd is not None else Decimal(0)
        total_invocations += 1
        total_seconds += seconds
        total_cost += cost
        acc = per_tool.setdefault(
            row.tool_id,
            {
                "tool_id": row.tool_id,
                "tool_name": tool_name,
                "invocations": 0,
                "seconds": 0.0,
                "cost": Decimal(0),
            },
        )
        acc["invocations"] += 1
        acc["seconds"] += seconds
        acc["cost"] += cost

    return ToolCostSummary(
        invocations=total_invocations,
        total_duration_seconds=round(total_seconds, 3),
        cost_usd=float(total_cost),
        by_tool=sorted(
            [
                ToolCost(
                    tool_id=acc["tool_id"],
                    tool_name=acc["tool_name"],
                    invocations=acc["invocations"],
                    total_duration_seconds=round(acc["seconds"], 3),
                    cost_usd=float(acc["cost"]),
                )
                for acc in per_tool.values()
            ],
            key=lambda t: t.cost_usd,
            reverse=True,
        ),
    )
