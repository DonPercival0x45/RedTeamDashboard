"""Approvals HTTP surface.

- ``GET  /engagements/{eid}/approvals?status=pending`` — list rows for an engagement
- ``GET  /approvals/{id}``                            — fetch one
- ``POST /approvals/{id}/decision``                   — decide a pending approval

The decision endpoint updates the row in-place and pushes a ``run.resume``
envelope onto ``runs:{engagement_id}:in`` so the worker can resume the paused
LangGraph thread with ``Command(resume=...)``.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.api.deps import CurrentNonGuestUser, CurrentUser, DbSession, RedisClient
from app.models import (
    ActorType,
    Approval,
    ApprovalStatus,
    AuditLog,
    Authorization,
    Engagement,
)
from app.models.command_outbox import CommandOutbox
from app.runs.streams import inbound_stream, load_run_model
from app.schemas.approval import ApprovalDecision, ApprovalInboxRead, ApprovalRead
from app.services.command_outbox import enqueue_command, publish_entry

router = APIRouter()


@router.get(
    "/engagements/{engagement_id}/approvals",
    response_model=list[ApprovalRead],
)
def list_approvals(
    engagement_id: UUID,
    session: DbSession,
    _user: CurrentUser,
    status: Annotated[ApprovalStatus | None, Query()] = None,
) -> list[Approval]:
    stmt = select(Approval).where(Approval.engagement_id == engagement_id)
    if status is not None:
        stmt = stmt.where(Approval.status == status)
    stmt = stmt.order_by(Approval.created_at.desc())
    return list(session.execute(stmt).scalars())


@router.get("/approvals", response_model=list[ApprovalInboxRead])
def list_approval_inbox(
    session: DbSession,
    _user: CurrentUser,
    status_filter: Annotated[
        ApprovalStatus, Query(alias="status")
    ] = ApprovalStatus.pending,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> list[ApprovalInboxRead]:
    """Tenant-global, recoverable approval queue for the app shell."""
    rows = session.execute(
        select(Approval, Engagement.slug, Engagement.name)
        .join(Engagement, Engagement.id == Approval.engagement_id)
        .where(Approval.status == status_filter)
        .order_by(Approval.created_at.asc(), Approval.id.asc())
        .limit(limit)
    ).all()
    return [
        ApprovalInboxRead(
            **ApprovalRead.model_validate(approval).model_dump(),
            engagement_slug=slug,
            engagement_name=name,
        )
        for approval, slug, name in rows
    ]


@router.get("/approvals/{approval_id}", response_model=ApprovalRead)
def get_approval(
    approval_id: UUID, session: DbSession, _user: CurrentUser
) -> Approval:
    approval = session.get(Approval, approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="approval not found")
    return approval


@router.post(
    "/approvals/{approval_id}/decision",
    response_model=ApprovalRead,
)
def decide_approval(
    approval_id: UUID,
    body: ApprovalDecision,
    session: DbSession,
    redis_client: RedisClient,
    user: CurrentNonGuestUser,
) -> Approval:
    # Serialize terminal decisions. The lock covers the approval update,
    # optional grant, audit row, and outbox insert in one DB transaction.
    approval = session.execute(
        select(Approval).where(Approval.id == approval_id).with_for_update()
    ).scalar_one_or_none()
    if approval is None:
        raise HTTPException(status_code=404, detail="approval not found")

    decision_args: dict[str, object] = {"approved": body.approved}
    if body.edited_args:
        decision_args["edited_args"] = body.edited_args
    if body.reason:
        decision_args["reason"] = body.reason

    outbox_key = f"approval.resume:{approval.id}"
    if approval.status is not ApprovalStatus.pending:
        same_decision = approval.decision_args == decision_args
        if body.approved and body.remember_for_session and approval.authorization_id is None:
            same_decision = False
        if not same_decision:
            raise HTTPException(
                status_code=409,
                detail=f"approval already decided as {approval.status.value}",
            )
        # An exact retry is idempotent. If the prior immediate XADD failed,
        # retry publication now while leaving the durable row for the relay.
        outbox = session.execute(
            select(CommandOutbox).where(
                CommandOutbox.idempotency_key == outbox_key
            )
        ).scalar_one_or_none()
        session.commit()
        if outbox is not None:
            publish_entry(session, redis_client, outbox.id)
        session.refresh(approval)
        return approval

    if body.approved:
        approval.status = (
            ApprovalStatus.edited if body.edited_args else ApprovalStatus.approved
        )
    else:
        approval.status = ApprovalStatus.denied
    approval.decided_by = user.id
    approval.decided_at = datetime.now(tz=UTC)
    approval.decision_args = decision_args

    # Approving "for the session" grants a standing per-(engagement, tool)
    # authorization so future in-scope calls to this tool auto-run. Reuse an
    # existing active grant rather than duplicating it.
    if body.approved and body.remember_for_session:
        grant = session.execute(
            select(Authorization).where(
                Authorization.engagement_id == approval.engagement_id,
                Authorization.tool_name == approval.tool_name,
                Authorization.revoked_at.is_(None),
            )
        ).scalar_one_or_none()
        if grant is None:
            grant = Authorization(
                engagement_id=approval.engagement_id,
                tool_name=approval.tool_name,
                granted_by=user.id,
                note=f"granted while approving a {approval.tool_name} call",
            )
            session.add(grant)
            session.flush()
            session.add(
                AuditLog(
                    engagement_id=approval.engagement_id,
                    actor_type=ActorType.user,
                    actor_id=str(user.id),
                    event_type="authorization.granted",
                    payload={
                        "authorization_id": str(grant.id),
                        "tool": approval.tool_name,
                        "via_approval_id": str(approval.id),
                    },
                )
            )
        approval.authorization_id = grant.id

    session.add(
        AuditLog(
            engagement_id=approval.engagement_id,
            actor_type=ActorType.user,
            actor_id=str(user.id),
            event_type="approval.decided",
            payload={
                "approval_id": str(approval.id),
                "thread_id": approval.thread_id,
                "tool": approval.tool_name,
                "status": approval.status.value,
                "approved": body.approved,
                **({"edited_args": body.edited_args} if body.edited_args else {}),
                **({"reason": body.reason} if body.reason else {}),
            },
        )
    )

    if not approval.tool_call_id:
        raise HTTPException(status_code=409, detail="approval lacks interrupt identity")
    resume_payload: dict[str, object] = {
        "type": "run.resume",
        "thread_id": approval.thread_id,
        "approval_id": str(approval.id),
        "tool_call_id": approval.tool_call_id,
        **decision_args,
    }

    # New approvals persist this lineage in Postgres. The Redis lookup is only
    # a compatibility fallback for approvals created before migration 0054.
    run_model = dict(approval.run_model) if approval.run_model else None
    acting_user_id = str(approval.acting_user_id) if approval.acting_user_id else None
    if run_model is None or acting_user_id is None:
        try:
            cached_model = load_run_model(redis_client, approval.thread_id)
        except Exception:  # noqa: BLE001 - outbox must survive Redis outage
            cached_model = None
        if cached_model:
            cached_acting_user = cached_model.pop("acting_user_id", None)
            run_model = run_model or cached_model
            acting_user_id = acting_user_id or cached_acting_user
    if run_model:
        resume_payload["model"] = run_model
    if acting_user_id:
        resume_payload["acting_user_id"] = acting_user_id
    if approval.run_context:
        for key in ("mcp_url", "lease_token"):
            if approval.run_context.get(key):
                resume_payload[key] = approval.run_context[key]

    outbox = enqueue_command(
        session,
        idempotency_key=outbox_key,
        engagement_id=approval.engagement_id,
        stream_name=inbound_stream(approval.engagement_id),
        payload=resume_payload,
    )
    session.commit()
    session.refresh(approval)

    # Low-latency attempt after the atomic domain+outbox commit. Failure is
    # recorded as pending and is not an API error; the worker relay retries it.
    publish_entry(session, redis_client, outbox.id)
    session.refresh(approval)
    return approval
