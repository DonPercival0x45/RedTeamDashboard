"""Finding activity timeline assembly (Phase 1 of the finding "pane of glass").

Pulls every signal of "what's happened to/around this finding" into one
chronological feed so the pane can render a Sentinel-incident-style
activity log. Sources:

- the finding itself (creation: source_tool / target / producing thread)
- ``Task`` rows scoped to the finding (human + agent tasks)
- ``AgentExecution`` rows whose ``input.finding_id`` is this finding
  (triage / rewrite / strategic runs)
- ``audit_log`` rows whose payload carries this finding_id (validated,
  triaged, summary_rewritten, updated, …)

Each entry is a flat ``(ts, kind, label, actor, detail, ref)`` row so the
frontend can render it uniformly without knowing the source model.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    AgentExecution,
    AuditLog,
    Finding,
    Task,
)


def _entry(
    *,
    ts,
    kind: str,
    label: str,
    actor: str | None = None,
    detail: str | None = None,
    ref_type: str | None = None,
    ref_id: str | None = None,
) -> dict[str, Any]:
    return {
        "ts": ts.isoformat() if ts is not None else None,
        "kind": kind,
        "label": label,
        "actor": actor,
        "detail": detail,
        "ref_type": ref_type,
        "ref_id": ref_id,
    }


# audit event_type -> human label. Unknown events fall back to the raw type.
_AUDIT_LABELS: dict[str, str] = {
    "finding.validated": "Validated",
    "finding.updated": "Updated",
    "finding.triaged": "AI Triage ran",
    "finding.summary_rewritten": "Summary rewritten (AI)",
    "finding.summary_recorded": "Summary recorded",
    "finding.chat_asked": "AI assistant asked",
    "finding.chat_action.accepted": "AI action approved",
    "finding.deleted": "Deleted",
}


def build_finding_activity(
    session: Session, finding_id: uuid.UUID
) -> list[dict[str, Any]]:
    finding = session.get(Finding, finding_id)
    if finding is None:
        return []

    entries: list[dict[str, Any]] = []

    # 1. creation / origin
    details = dict(finding.details or {})
    thread_id = details.get("thread_id")
    source_tool = finding.source_tool
    origin = "manual entry" if not source_tool and not thread_id else (
        f"{source_tool}" if source_tool else "agent run"
    )
    entries.append(
        _entry(
            ts=finding.created_at,
            kind="created",
            label=f"Finding created - {origin}",
            detail=finding.target,
            ref_type="thread" if thread_id else None,
            ref_id=str(thread_id) if thread_id else None,
        )
    )

    # 2. tasks scoped to this finding (human + agent)
    for t in session.execute(
        select(Task).where(Task.finding_id == finding_id)
    ).scalars():
        ts = t.dispatched_at or t.completed_at or t.created_at
        entries.append(
            _entry(
                ts=ts,
                kind="task",
                label=t.title,
                actor=("agent" if str(t.owner_eligibility) == "agent" else "human"),
                detail=f"{t.kind.value if hasattr(t.kind, 'value') else t.kind} · "
                f"{t.status.value if hasattr(t.status, 'value') else t.status} · "
                f"{t.owner_eligibility}",
                ref_type="task",
                ref_id=str(t.id),
            )
        )

    # 3. agent executions that reference this finding (input.finding_id)
    for ex in session.execute(
        select(AgentExecution).order_by(AgentExecution.started_at.desc())
    ).scalars():
        inp = ex.input if isinstance(ex.input, dict) else {}
        if str(inp.get("finding_id") or "") != str(finding_id):
            continue
        agent = ex.agent.value if hasattr(ex.agent, "value") else str(ex.agent)
        entries.append(
            _entry(
                ts=ex.started_at or ex.created_at,
                kind="agent_run",
                label=f"{agent} run",
                detail=f"{ex.model_provider or '?'}/{ex.model_name or '?'} · "
                f"{ex.status.value if hasattr(ex.status, 'value') else ex.status}",
                ref_type="execution",
                ref_id=str(ex.id),
            )
        )

    # 4. audit log rows referencing this finding
    for row in session.execute(
        select(AuditLog)
        .order_by(AuditLog.created_at.desc())
        .limit(500)
    ).scalars():
        payload = row.payload if isinstance(row.payload, dict) else {}
        if str(payload.get("finding_id") or "") != str(finding_id):
            continue
        # creation is already covered by entry #1; skip the duplicate.
        if row.event_type in {"finding.created"}:
            continue
        entries.append(
            _entry(
                ts=row.created_at,
                kind=row.event_type,
                label=_AUDIT_LABELS.get(row.event_type, row.event_type),
                actor=row.actor_id,
                detail=_summarize_audit(row.event_type, payload),
                ref_type="audit",
                ref_id=str(row.id),
            )
        )

    entries.sort(key=lambda e: e["ts"] or "", reverse=True)
    return entries


def _summarize_audit(event_type: str, payload: dict[str, Any]) -> str | None:
    if event_type == "finding.summary_rewritten":
        d = payload.get("draft_chars")
        s = payload.get("summary_chars")
        if d is not None and s is not None:
            return f"{d} → {s} chars"
    if event_type == "finding.updated" and payload.get("changes"):
        return str(payload["changes"])
    return None
