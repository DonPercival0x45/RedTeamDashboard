"""Deterministic, analyst-triggered v3 passive tool execution."""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import CurrentNonGuestUser, DbSession
from app.models import (
    ActorType,
    AuditLog,
    Engagement,
    EngagementArchitecture,
    EngagementStatus,
    EngagementWorkState,
    ScopeItem,
    ScopeKind,
)
from app.services.playbook.executor import StepResult
from app.services.playbook.finding_bridge import TOOL_ALIASES, bridge_step_to_finding
from app.services.scope_matcher import evaluate_scope_candidates, infer_scope_kind

router = APIRouter()

# This synchronous surface is intentionally passive-only. Adding an active or
# destructive tool requires a durable approval/queue design, not another entry
# here. The authenticated analyst click is the explicit authorization for these
# passive lookups; every call is scope-checked and audit-logged.
_DIRECT_PASSIVE_TOOLS = frozenset({"whois", "dns_inventory", "dns-inventory"})
_TARGET_ARG_KEYS = frozenset({"domain", "target", "host", "hostname", "ip", "url"})


class ToolRunRequest(BaseModel):
    scope: str | None = Field(default=None, max_length=500)
    args: dict[str, Any] = Field(default_factory=dict)


class ToolRunResponse(BaseModel):
    ok: bool
    tool: str
    scope: str
    findings_new: int
    findings_total: int
    finding_id: uuid.UUID | None = None
    stub: bool = False
    error: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


def _load_tool(slug: str):
    if slug not in TOOL_ALIASES or slug not in _DIRECT_PASSIVE_TOOLS:
        raise HTTPException(
            status_code=404,
            detail=f"passive direct tool {slug!r} is not available",
        )
    module_slug = slug.replace("-", "_")
    try:
        module = __import__(
            f"app.services.playbook.tools.{module_slug}", fromlist=["run"]
        )
    except ImportError as exc:
        raise HTTPException(
            status_code=404, detail=f"tool module {module_slug!r} not importable"
        ) from exc
    fn = getattr(module, "run", None)
    if not callable(fn):
        raise HTTPException(status_code=500, detail=f"tool {slug!r} has no run()")
    return fn


def _scope_items(session: Session, engagement_id: uuid.UUID) -> list[ScopeItem]:
    return list(
        session.execute(
            select(ScopeItem)
            .where(ScopeItem.engagement_id == engagement_id)
            .order_by(ScopeItem.created_at)
        ).scalars()
    )


def _resolve_scope(
    session: Session,
    engagement_id: uuid.UUID,
    requested: str | None,
) -> str:
    items = _scope_items(session, engagement_id)
    value = (requested or "").strip()
    if not value:
        first = next(
            (
                item.value
                for item in items
                if not item.is_exclusion and item.kind == ScopeKind.domain
            ),
            None,
        )
        value = str(first or "").strip()
    if not value:
        raise HTTPException(
            status_code=422,
            detail="Add an in-scope target before running a tool.",
        )
    kind = infer_scope_kind(value)
    if kind != ScopeKind.domain:
        raise HTTPException(
            status_code=422,
            detail="WHOIS and DNS direct tools require an in-scope domain target.",
        )
    decision = evaluate_scope_candidates([(value, kind)], items)
    if not decision.allowed:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Target {value!r} is not authorized by engagement scope "
                f"({decision.reason}). Add it to Scope first."
            ),
        )
    return value


def _safe_tool_args(scope: str, supplied: dict[str, Any]) -> dict[str, Any]:
    args = dict(supplied or {})
    conflicts = [
        key
        for key in _TARGET_ARG_KEYS
        if key in args and str(args[key]).strip() not in {"", scope}
    ]
    if conflicts:
        raise HTTPException(
            status_code=422,
            detail=(
                "Tool target arguments must match the validated scope target. "
                f"Conflicting keys: {', '.join(sorted(conflicts))}."
            ),
        )
    # The bridgeable direct tools are domain-shaped. Validated scope is always
    # authoritative, preventing args.domain from smuggling another target.
    args["domain"] = scope
    return args


@router.post(
    "/engagements/{slug}/tools/{tool_slug}/run",
    response_model=ToolRunResponse,
    status_code=status.HTTP_200_OK,
)
def run_tool_direct(
    slug: str,
    tool_slug: str,
    body: ToolRunRequest,
    session: DbSession,
    user: CurrentNonGuestUser,
) -> ToolRunResponse:
    engagement = session.execute(
        select(Engagement).where(Engagement.slug == slug)
    ).scalar_one_or_none()
    if engagement is None:
        raise HTTPException(status_code=404, detail=f"engagement {slug!r} not found")
    if engagement.intelligence_architecture != EngagementArchitecture.v3:
        raise HTTPException(status_code=409, detail="direct tools require a v3 engagement")
    if (
        engagement.status != EngagementStatus.active
        or engagement.work_state == EngagementWorkState.completed
    ):
        raise HTTPException(status_code=409, detail="engagement is read-only")

    tool_fn = _load_tool(tool_slug)
    scope = _resolve_scope(session, engagement.id, body.scope)
    tool_args = _safe_tool_args(scope, body.args)
    operation_id = uuid.uuid4()

    try:
        result: StepResult = tool_fn(scope, tool_args)
    except Exception as exc:  # noqa: BLE001 - tool code is untrusted
        result = StepResult(ok=False, error=f"{type(exc).__name__}: {exc}")

    bridge = None
    if result.ok and not getattr(result, "stub", False):
        bridge = bridge_step_to_finding(
            session,
            engagement_id=engagement.id,
            playbook_tool=tool_slug,
            scope_item=scope,
            args_template=tool_args,
            data=result.data,
            thread_id=None,
            acting_user_id=user.id,
            operation_id=operation_id,
            source="tool.run.direct",
        )

    finding_id = bridge.finding_id if bridge else None
    findings_new = bridge.items_added if bridge else 0
    findings_total = bridge.items_total if bridge else 0
    session.add(
        AuditLog(
            engagement_id=engagement.id,
            actor_type=ActorType.user,
            actor_id=str(user.id),
            event_type="tool.run.direct",
            payload={
                "operation_id": str(operation_id),
                "tool_slug": tool_slug,
                "scope": scope,
                "ok": bool(result.ok),
                "findings_new": findings_new,
                "findings_total": findings_total,
                "stub": bool(getattr(result, "stub", False)),
                "finding_id": str(finding_id) if finding_id else None,
                "error": result.error,
            },
        )
    )
    session.commit()

    return ToolRunResponse(
        ok=bool(result.ok),
        tool=tool_slug,
        scope=scope,
        findings_new=findings_new,
        findings_total=findings_total,
        finding_id=finding_id,
        stub=bool(getattr(result, "stub", False)),
        error=result.error,
        data=dict(result.data or {}),
    )
