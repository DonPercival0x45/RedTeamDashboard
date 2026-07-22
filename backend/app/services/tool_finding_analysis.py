"""Post-invocation finding analysis (v0.18.0).

Closes the asymmetry between built-in tools (which persist findings as
they run) and uploaded tools (which only captured stdout). When a tool's
manifest declares ``analyze_findings: true``, a successful invocation's
captured stdout is sent to an LLM that extracts structured findings,
which are then persisted through the same grouping path built-in tools
use — and a ``finding.created`` event is emitted so the live run panel
and the strategic consumer see them.

Design notes:
- **Opt-in** (manifest flag) — no surprise token cost.
- **Non-fatal** — no key / LLM error / unparseable output logs and
  returns []; the invocation itself already succeeded.
- **Injectable extractor** — ``extract_fn`` lets tests drive the
  persistence path without an LLM; production uses
  :func:`_extract_findings_via_llm`.
- The agent-run path (``worker/runner._persist_finding``) is untouched;
  findings here derive phase from the manifest's ``task_kind``.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

import redis as redis_lib
from sqlalchemy.orm import Session

from app.models import (
    Engagement,
    Finding,
    FindingPhase,
    FindingStatus,
    Severity,
    Tool,
    ToolInvocation,
    ToolTaskKind,
    User,
)
from app.models.finding import default_status_for_phase
from app.services.finding_feedback import (
    publish_feedback_entries,
    stage_finding_feedback,
)
from app.services.finding_grouping import compute_group_key, upsert_grouped_finding

logger = logging.getLogger(__name__)

# Cap what we send to the model — a big port-sweep or CSV can blow the
# context window. 16 KiB is plenty for the kinds of enum/scan outputs
# this is meant for (user lists, port tables, DNS dumps).
STDOUT_CAP = 16_000

# task_kind → FindingPhase. Uploaded tools declare task_kind explicitly
# (unlike built-in tools, which key off phase_for_tool(tool)). enum/scan
# are factual recon → osint (auto-validates); exploit → exploit (pending).
_PHASE_FOR_TASK_KIND: dict[ToolTaskKind, FindingPhase] = {
    ToolTaskKind.enum: FindingPhase.osint,
    ToolTaskKind.scan: FindingPhase.osint,
    ToolTaskKind.exploit: FindingPhase.exploit,
}


# --- LLM extraction schema -----------------------------------------------

class _ExtractedFinding:  # pragma: no cover - placeholder for doc only
    """Shape the model returns (materialized via with_structured_output):
    {title, target?, severity, summary?}."""


async def _extract_findings_via_llm(
    stdout: str,
    tool: Tool,
    redis: redis_lib.Redis,
    invoker: User,
) -> list[dict[str, Any]]:
    """Ask the invoker's LLM to pull findings out of ``stdout``.

    Returns a list of ``{title, target, severity, summary}`` dicts.
    Raises :class:`NoProviderKeyError` if the invoker has no key for the
    default provider — the caller treats that as "skip, not an error".
    """
    from datetime import UTC, datetime

    from pydantic import BaseModel, Field

    from app.agents.strategic import _make_chat_model
    from app.orchestrator.llm import default_provider_model
    from app.services.ephemeral_provider_key import resolve_for_user

    provider, model_name = default_provider_model()
    resolved = resolve_for_user(
        redis, user_id=invoker.id, provider=provider
    )
    chat = _make_chat_model(
        provider, model_name, api_key=resolved.api_key, endpoint=resolved.endpoint
    )

    class _F(BaseModel):
        title: str = Field(min_length=1, max_length=300)
        target: str | None = None
        severity: str = "info"
        summary: str | None = None

    class _Bundle(BaseModel):
        findings: list[_F] = Field(default_factory=list)

    structured = chat.with_structured_output(_Bundle)
    manifest = (tool.manifest or {}).get("metadata", {}) or {}
    desc = manifest.get("description") or tool.name
    task_kind = (tool.task_kind.value if tool.task_kind else "enum")
    prompt = (
        f"You are a red-team engagement analyst reviewing the output of a "
        f"reconnaissance tool.\n\n"
        f"Tool: {tool.name} (task_kind={task_kind})\n"
        f"Description: {desc}\n\n"
        f"Tool output (may be truncated):\n```\n{stdout}\n```\n\n"
        f"Extract actionable findings as JSON. Each finding needs a short "
        f"title, the specific target entity (email / IP / host / URL / etc.) "
        f"if one applies, a severity (info|low|medium|high|critical), and a "
        f"one-line summary. Only extract genuine findings — e.g. for user "
        f"enumeration, one finding per account that EXISTS or is AMBIGUOUS, "
        f"none for accounts confirmed absent. If there's nothing actionable, "
        f"return an empty findings list."
    )
    bundle = await structured.ainvoke(prompt)
    now = datetime.now(tz=UTC).isoformat()
    return [
        {
            "title": f.title,
            "target": f.target,
            "severity": f.severity,
            "summary": f.summary,
            "data": {
                "summary": f.summary,
                "source": "llm_analysis",
                "analyzed_at": now,
            },
        }
        for f in bundle.findings
    ]


# --- orchestration -------------------------------------------------------

ExtractFn = Callable[
    [str, Tool, redis_lib.Redis, User], Awaitable[list[dict[str, Any]]]
]


async def analyze_and_persist(
    session: Session,
    redis: redis_lib.Redis,
    *,
    engagement: Engagement,
    invocation: ToolInvocation,
    tool: Tool,
    invoker: User,
    extract_fn: ExtractFn | None = None,
) -> list[Finding]:
    """Analyze ``invocation.stdout`` and persist extracted findings.

    Returns the created/updated Finding rows (may be empty). Never
    raises — analysis is best-effort on top of an already-successful
    invocation.
    """
    stdout = (invocation.stdout or "")[:STDOUT_CAP]
    if not stdout.strip():
        return []

    extract = extract_fn or _extract_findings_via_llm
    try:
        raw_findings = await extract(stdout, tool, redis, invoker)
    except Exception as exc:  # noqa: BLE001 — best-effort; never fail the invocation
        logger.warning(
            "tool_finding_analysis.extract_failed",
            extra={"invocation_id": str(invocation.id), "error": str(exc)},
        )
        return []

    created: list[Finding] = []
    for rf in raw_findings:
        row = _persist_one(
            session,
            engagement_id=engagement.id,
            invocation=invocation,
            tool=tool,
            rf=rf,
        )
        if row is not None:
            created.append(row)

    if created:
        session.flush()
        entries = [
            stage_finding_feedback(
                session,
                finding=row,
                acting_user_id=invoker.id,
                operation_id=invocation.id,
                source="tool_invocation",
                event_type="finding.updated" if row.group_key else "finding.created",
                thread_id=invocation.id,
                tool=tool.name,
                args=dict(invocation.args or {}),
            )
            for row in {finding.id: finding for finding in created}.values()
        ]
        session.commit()
        publish_feedback_entries(session, redis, entries)
        for row in created:
            session.refresh(row)
        logger.info(
            "tool_finding_analysis.persisted",
            extra={
                "invocation_id": str(invocation.id),
                "tool_id": str(tool.id),
                "findings": len(created),
            },
        )
    return created


def _persist_one(
    session: Session,
    *,
    engagement_id,
    invocation: ToolInvocation,
    tool: Tool,
    rf: dict[str, Any],
) -> Finding | None:
    severity_raw = str(rf.get("severity") or "info").strip().lower()
    try:
        severity = Severity(severity_raw)
    except ValueError:
        severity = Severity.info

    target = (rf.get("target") or "").strip() or None
    title = (rf.get("title") or "").strip() or (f"{tool.name} → {target}" if target else tool.name)
    data = dict(rf.get("data") or {})
    args = dict(invocation.args or {})

    phase = _PHASE_FOR_TASK_KIND.get(tool.task_kind, FindingPhase.osint)
    status = default_status_for_phase(phase)

    common = {
        "engagement_id": engagement_id,
        "tool": tool.name,
        "thread_id": str(invocation.id),  # no agent thread; invocation id is the provenance
        "args": args,
        "data": {**data, "invocation_id": str(invocation.id)},
        "incoming_severity": severity,
        "default_title": title,
        "phase": phase,
        "status": status,
    }

    group_key = compute_group_key(tool.name, args, common["data"])
    if group_key:
        row, _added = upsert_grouped_finding(
            session, group_key=group_key, **common
        )
    else:
        from datetime import UTC, datetime

        row = Finding(
            engagement_id=engagement_id,
            title=title,
            severity=severity,
            summary=rf.get("summary"),
            details={**common["data"], "args": args},
            source_tool=tool.name,
            target=target,
            phase=phase,
            status=status,
            validated_at=datetime.now(tz=UTC) if status == FindingStatus.validated else None,
        )
        session.add(row)
    session.flush()
    return row
