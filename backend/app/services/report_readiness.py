"""Deterministic engagement-wide report readiness preflight."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    AgentExecution,
    AgentExecutionStatus,
    Approval,
    ApprovalStatus,
    Attachment,
    Engagement,
    Finding,
    FindingStatus,
    ScopeItem,
    Task,
    TaskStatus,
)
from app.schemas.report import ReadinessCheck, ReportReadiness


def _finding_check(
    key: str,
    level: Literal["blocker", "warning", "info"],
    rows: list[Finding],
    message: str,
    target_view: str,
) -> ReadinessCheck:
    return ReadinessCheck(
        key=key,
        level=level,
        count=len(rows),
        message=message.format(count=len(rows)),
        finding_ids=[row.id for row in rows],
        target_view=target_view,
    )


def build_report_readiness(
    session: Session,
    *,
    engagement: Engagement,
) -> ReportReadiness:
    findings = list(
        session.execute(
            select(Finding).where(
                Finding.engagement_id == engagement.id,
                Finding.deleted_at.is_(None),
            )
        ).scalars()
    )
    reportable = [
        row
        for row in findings
        if row.status is FindingStatus.validated and row.exclusion is None
    ]
    pending = [
        row
        for row in findings
        if row.status in {FindingStatus.pending_validation, FindingStatus.needs_review}
    ]
    missing_summary = [row for row in reportable if not (row.summary or "").strip()]
    missing_target = [row for row in reportable if not (row.target or "").strip()]
    excluded = [row for row in findings if row.exclusion is not None]

    attachment_ids = set(
        session.execute(
            select(Attachment.finding_id).where(
                Attachment.engagement_id == engagement.id
            )
        ).scalars()
    )
    missing_evidence = [row for row in reportable if row.id not in attachment_ids]

    active_tasks = list(
        session.execute(
            select(Task.id).where(
                Task.engagement_id == engagement.id,
                Task.status.in_(
                    [
                        TaskStatus.pending,
                        TaskStatus.dispatched,
                        TaskStatus.running,
                    ]
                ),
            )
        ).scalars()
    )
    deferred_tasks = list(
        session.execute(
            select(Task.id).where(
                Task.engagement_id == engagement.id,
                Task.status == TaskStatus.deferred,
            )
        ).scalars()
    )
    active_agents = list(
        session.execute(
            select(AgentExecution.id).where(
                AgentExecution.engagement_id == engagement.id,
                AgentExecution.status == AgentExecutionStatus.running,
            )
        ).scalars()
    )
    pending_approvals = list(
        session.execute(
            select(Approval.id).where(
                Approval.engagement_id == engagement.id,
                Approval.status == ApprovalStatus.pending,
            )
        ).scalars()
    )
    formal_scope_count = len(
        list(
            session.execute(
                select(ScopeItem.id).where(
                    ScopeItem.engagement_id == engagement.id,
                    ScopeItem.is_exclusion.is_(False),
                    ScopeItem.source == "defined",
                )
            ).scalars()
        )
    )

    checks = [
        _finding_check(
            "pending_validation",
            "blocker",
            pending,
            "{count} findings still need analyst review",
            "findings&status=pending_validation",
        ),
        _finding_check(
            "missing_summary",
            "blocker",
            missing_summary,
            "{count} reportable findings are missing a summary",
            "findings&readiness=missing_summary",
        ),
        _finding_check(
            "missing_target",
            "warning",
            missing_target,
            "{count} reportable findings have no affected target",
            "findings&readiness=missing_target",
        ),
        _finding_check(
            "missing_evidence",
            "warning",
            missing_evidence,
            "{count} reportable findings have no attached evidence",
            "findings&readiness=missing_evidence",
        ),
        _finding_check(
            "excluded_findings",
            "info",
            excluded,
            "{count} findings are excluded from the client deliverable",
            "findings&readiness=excluded",
        ),
        ReadinessCheck(
            key="active_work",
            level="blocker",
            count=len(active_tasks) + len(active_agents),
            message=(
                f"{len(active_tasks) + len(active_agents)} tasks or agent runs are still active"
            ),
            target_view="status",
        ),
        ReadinessCheck(
            key="deferred_work",
            level="blocker",
            count=len(deferred_tasks),
            message=(
                f"{len(deferred_tasks)} deferred "
                f"task{'s' if len(deferred_tasks) != 1 else ''} "
                f"{'need' if len(deferred_tasks) != 1 else 'needs'} retry or cancellation"
            ),
            target_view="status",
        ),
        ReadinessCheck(
            key="pending_approvals",
            level="blocker",
            count=len(pending_approvals),
            message=f"{len(pending_approvals)} approvals are waiting for a decision",
            target_view="status",
        ),
        ReadinessCheck(
            key="formal_scope",
            level="blocker",
            count=0 if formal_scope_count else 1,
            message=(
                "Formal client scope is defined"
                if formal_scope_count
                else "No formal client-provided scope is defined"
            ),
            target_view="scope",
        ),
        ReadinessCheck(
            key="reportable_findings",
            level="blocker",
            count=0 if reportable else 1,
            message=(
                f"{len(reportable)} validated findings are reportable"
                if reportable
                else "No validated, non-excluded findings are reportable"
            ),
            target_view="findings",
        ),
    ]
    ready = not any(
        check.level == "blocker" and check.count > 0 for check in checks
    )
    return ReportReadiness(
        ready=ready,
        generated_at=datetime.now(tz=UTC),
        reportable_count=len(reportable),
        total_findings=len(findings),
        checks=checks,
    )
