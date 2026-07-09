"""Tool invocation HTTP surface (v0.12.0).

Endpoints::

    POST /engagements/{slug}/tool-invocations  -> kick a run
    GET  /engagements/{slug}/tool-invocations  -> history
    GET  /tool-invocations/{id}                -> read one row + captured output

All non-guest. Charter task-kind gate lives in the orchestrator, not
here — but the runtime infra failures are surfaced as 502 so the client
can distinguish "your tool errored" from "we couldn't reach ACI".
"""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.api.deps import CurrentNonGuestUser, CurrentUser, DbSession, RedisClient
from app.models import (
    Engagement,
    Tool,
    ToolInvocation,
    ToolInvocationStatus,
)
from app.schemas.tool_invocation import ToolInvocationRead, ToolInvokeRequest
from app.services.tool_invocation import (
    ToolInvocationError,
    invoke_tool,
)

router = APIRouter()


def _get_engagement_or_404(session: DbSession, slug: str) -> Engagement:
    eng = session.execute(
        select(Engagement).where(Engagement.slug == slug)
    ).scalar_one_or_none()
    if eng is None:
        raise HTTPException(status_code=404, detail="engagement not found")
    return eng


def _row_to_read(row: ToolInvocation, tool_name: str | None = None) -> ToolInvocationRead:
    return ToolInvocationRead(
        id=row.id,
        tool_id=row.tool_id,
        tool_version=row.tool_version,
        tool_name=tool_name,
        engagement_id=row.engagement_id,
        invoker_user_id=row.invoker_user_id,
        args=dict(row.args or {}),
        runtime_ref=row.runtime_ref,
        status=row.status,
        exit_code=row.exit_code,
        stdout=row.stdout,
        stderr=row.stderr,
        error=row.error,
        started_at=row.started_at,
        completed_at=row.completed_at,
    )


@router.post(
    "/engagements/{slug}/tool-invocations",
    response_model=ToolInvocationRead,
)
async def create_tool_invocation(
    slug: str,
    body: ToolInvokeRequest,
    session: DbSession,
    user: CurrentNonGuestUser,
    redis: RedisClient,
) -> ToolInvocationRead:
    """Kick a tool invocation and block until it exits (or times out).

    Runs synchronously: the request returns when the sandbox is done,
    with the captured output on the response. That works for the
    manifest-default 120s timeout; longer-running tools should be
    handed to the worker instead (v0.15 wiring).
    """
    eng = _get_engagement_or_404(session, slug)
    tool = session.get(Tool, body.tool_id)
    if tool is None:
        raise HTTPException(status_code=404, detail="tool not found")

    try:
        row = await invoke_tool(
            session, eng, tool, body.args, user, redis_client=redis
        )
    except ToolInvocationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if row.status == ToolInvocationStatus.failed and row.error and "runner" in (
        row.error or ""
    ):
        # Runner-layer infra failure — surface as 502 so the client can
        # tell "sandbox down" from "tool exited nonzero".
        raise HTTPException(status_code=502, detail=row.error)

    return _row_to_read(row, tool.name)


@router.get(
    "/engagements/{slug}/tool-invocations",
    response_model=list[ToolInvocationRead],
)
def list_tool_invocations(
    slug: str,
    session: DbSession,
    _user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[ToolInvocationRead]:
    eng = _get_engagement_or_404(session, slug)
    rows = list(
        session.execute(
            select(ToolInvocation, Tool.name)
            .join(Tool, Tool.id == ToolInvocation.tool_id)
            .where(ToolInvocation.engagement_id == eng.id)
            .order_by(ToolInvocation.started_at.desc())
            .limit(limit)
        ).all()
    )
    return [_row_to_read(row, tool_name) for row, tool_name in rows]


@router.get(
    "/tool-invocations/{invocation_id}",
    response_model=ToolInvocationRead,
)
def get_tool_invocation(
    invocation_id: uuid.UUID,
    session: DbSession,
    _user: CurrentUser,
) -> ToolInvocationRead:
    row = session.get(ToolInvocation, invocation_id)
    if row is None:
        raise HTTPException(status_code=404, detail="invocation not found")
    tool = session.get(Tool, row.tool_id)
    return _row_to_read(row, tool.name if tool else None)
