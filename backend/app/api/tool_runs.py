"""v3.0.3 — direct playbook-tool execution endpoint.

The Scope-tab "Current tools" list used to drop an example prompt into
a free-text textarea for an analyst to dispatch through the LangGraph /
Tactical stack (LLM plan → tool dispatch → persist). v3 replaces that
loop for the collection plane: the analyst clicks the tool button and
we execute the tool function directly against a scope target, then
persist the finding via the same grouping helper the playbook runner
uses.

Design:

* **No LangGraph, no LLM.** This endpoint calls ``services/playbook/
  tools/{slug}.run`` synchronously. Deterministic collection, no
  strategist / tactical / correlate.
* **v3-friendly.** Bypasses the C6a ``enforce_v3_playbook_only`` gate
  because this IS the v3-native path — no LangGraph agent runs.
* **Same finding surface.** Uses ``finding_bridge.bridge_step_to_finding``
  so a Scope-tab click and a playbook run write to the same rows
  (subdomains:apex, whois:apex, dns_records:apex, …) and dedup
  cleanly across re-runs.
* **Audit-logged.** Every tool click is one ``tool.run.direct``
  audit_log row so the Costs / Attribution tabs can still trace who
  ran what. No AgentExecution row (there's no agent) but the audit
  entry carries the same actor + outcome shape.
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import CurrentNonGuestUser, DbSession
from app.models import ActorType, AuditLog, Engagement, ScopeItem, User
from app.services.playbook.executor import StepResult
from app.services.playbook.finding_bridge import (
    TOOL_ALIASES,
    bridge_step_to_finding,
)

router = APIRouter()


class ToolRunRequest(BaseModel):
    """Body for a direct tool click. All fields optional.

    * ``scope`` — target the tool runs against (a domain, IP, or URL).
      When omitted, the endpoint picks the engagement's first in-scope
      (non-exclusion) item. Explicit values must be in-scope; the
      matcher accepts the raw string against ScopeItem.value for the
      engagement.
    * ``args`` — extra kwargs to hand the tool (e.g. ``{"nameservers":
      ["8.8.8.8"]}`` for dns_inventory). The tool's ``run(scope, args)``
      contract accepts arbitrary dict; unknown keys are ignored by the
      tool.
    """

    scope: str | None = Field(default=None, max_length=500)
    args: dict[str, Any] = Field(default_factory=dict)


class ToolRunResponse(BaseModel):
    """One-shot direct execution result. Mirrors StepResult + adds the
    finding_id the bridge minted so the frontend can navigate directly
    to the row."""

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
    """Import the playbook tool module for ``slug``. Returns its ``run``
    callable. 404 if the slug isn't a registered playbook tool."""
    # Only tools with a bridge alias are exposed on this endpoint. That
    # keeps the surface honest — a tool without a grouping vocab entry
    # would run but drop its output on the floor.
    if slug not in TOOL_ALIASES:
        raise HTTPException(
            status_code=404,
            detail=(
                f"tool '{slug}' is not a bridgeable playbook tool. "
                f"Known: {sorted(TOOL_ALIASES.keys())}"
            ),
        )
    try:
        module = __import__(
            f"app.services.playbook.tools.{slug}", fromlist=["run"]
        )
    except ImportError as exc:
        raise HTTPException(
            status_code=404, detail=f"tool module '{slug}' not importable: {exc}"
        ) from exc
    fn = getattr(module, "run", None)
    if fn is None or not callable(fn):
        raise HTTPException(
            status_code=500,
            detail=f"tool '{slug}' module has no run() callable",
        )
    return fn


def _default_scope(session: Session, engagement_id: uuid.UUID) -> str | None:
    row = session.execute(
        select(ScopeItem)
        .where(
            ScopeItem.engagement_id == engagement_id,
            ScopeItem.is_exclusion.is_(False),
        )
        .order_by(ScopeItem.created_at)
        .limit(1)
    ).scalar_one_or_none()
    return row.value if row is not None else None


def _validate_scope_in_bounds(
    session: Session, engagement_id: uuid.UUID, scope: str
) -> None:
    """Best-effort in-scope check. Rejects ONLY when the value matches an
    explicit exclusion — permissive on the include side so the analyst
    can experiment with subdomains they haven't yet added.
    """
    hit = session.execute(
        select(ScopeItem).where(
            ScopeItem.engagement_id == engagement_id,
            ScopeItem.is_exclusion.is_(True),
            ScopeItem.value == scope,
        )
    ).scalar_one_or_none()
    if hit is not None:
        raise HTTPException(
            status_code=400,
            detail=f"'{scope}' is on the engagement's exclusion list",
        )


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
    """Execute a playbook tool once against ``body.scope`` and persist
    its output as a Finding. Deterministic, LLM-free, agent-free.

    * 404 if the engagement doesn't exist or the tool isn't a
      bridgeable playbook tool.
    * 400 if the target scope resolves to nothing (no default in-scope
      item to fall back to) or hits an explicit exclusion.
    * 200 with the StepResult + finding_id otherwise. ``ok=False``
      inside a 200 means the tool ran but reported a functional failure
      (e.g. NXDOMAIN) — same shape the playbook runner uses.
    """
    engagement = session.execute(
        select(Engagement).where(Engagement.slug == slug)
    ).scalar_one_or_none()
    if engagement is None:
        raise HTTPException(status_code=404, detail=f"engagement '{slug}' not found")

    tool_fn = _load_tool(tool_slug)

    scope = (body.scope or "").strip() or _default_scope(session, engagement.id)
    if not scope:
        raise HTTPException(
            status_code=400,
            detail=(
                "no target scope provided and engagement has no default "
                "in-scope item — add one on the Scope tab first"
            ),
        )
    _validate_scope_in_bounds(session, engagement.id, scope)

    # Execute the tool. Exceptions become StepResult(ok=False) — same
    # contract the playbook runner uses in _run_one.
    try:
        result: StepResult = tool_fn(scope, body.args or {})
    except Exception as exc:  # noqa: BLE001 - untrusted tool code
        result = StepResult(
            ok=False, error=f"{type(exc).__name__}: {exc}"
        )

    finding_id: uuid.UUID | None = None
    if result.ok and not getattr(result, "stub", False):
        try:
            finding_id = bridge_step_to_finding(
                session,
                engagement_id=engagement.id,
                playbook_tool=tool_slug,
                scope_item=scope,
                args_template={"domain": scope, **(body.args or {})},
                data=result.data,
                thread_id=None,
            )
        except Exception:  # noqa: BLE001 - bridge is best-effort
            finding_id = None

    session.add(
        AuditLog(
            engagement_id=engagement.id,
            actor_type=ActorType.user,
            actor_id=str(user.id),
            event_type="tool.run.direct",
            payload={
                "tool_slug": tool_slug,
                "scope": scope,
                "ok": bool(result.ok),
                "findings_new": int(result.findings_new),
                "findings_total": int(result.findings_total),
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
        findings_new=int(result.findings_new),
        findings_total=int(result.findings_total),
        finding_id=finding_id,
        stub=bool(getattr(result, "stub", False)),
        error=result.error,
        data=dict(result.data or {}),
    )
