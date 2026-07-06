"""PDF engagement report.

GET /engagements/{slug}/report -> application/pdf

Pulls engagement + scope + findings + approvals + audit_log from the DB,
renders the Jinja2 template, hands HTML to WeasyPrint for PDF rendering.
The template (``app/templates/report.html``) is where layout + styling
live; this endpoint is just the wiring.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.models import (
    Approval,
    AuditLog,
    Engagement,
    Finding,
    FindingStatus,
    Observation,
    ScopeItem,
)

router = APIRouter()

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=select_autoescape(["html"]),
)


@router.get(
    "/engagements/{slug}/report",
    responses={200: {"content": {"application/pdf": {}}}},
)
def engagement_report(
    slug: str,
    session: DbSession,
    user: CurrentUser,  # noqa: ARG001 — gates the endpoint
    omit_excluded: Annotated[
        bool,
        Query(
            description=(
                "Drop findings marked out_of_scope / outside_roe from the "
                "PDF. Default false; the Report tab toggle sets this to "
                "true when the analyst wants a client-ready deliverable."
            ),
        ),
    ] = False,
) -> Response:
    eng = session.execute(
        select(Engagement).where(Engagement.slug == slug)
    ).scalar_one_or_none()
    if eng is None:
        raise HTTPException(status_code=404, detail="engagement not found")

    scope_items = list(
        session.execute(
            select(ScopeItem)
            .where(ScopeItem.engagement_id == eng.id)
            .order_by(ScopeItem.created_at)
        ).scalars()
    )
    # Only validated findings are report-eligible (Phase 8 validation gate).
    # v1.4.0: analyst-set exclusion also drops the row when the caller
    # asks for a client-clean report.
    findings_stmt = select(Finding).where(
        Finding.engagement_id == eng.id,
        Finding.status == FindingStatus.validated,
        Finding.deleted_at.is_(None),
    )
    if omit_excluded:
        findings_stmt = findings_stmt.where(Finding.exclusion.is_(None))
    findings = list(
        session.execute(findings_stmt.order_by(Finding.created_at.desc())).scalars()
    )
    approvals = list(
        session.execute(
            select(Approval)
            .where(Approval.engagement_id == eng.id)
            .order_by(Approval.created_at.desc())
        ).scalars()
    )
    observations = list(
        session.execute(
            select(Observation)
            .where(Observation.engagement_id == eng.id)
            .order_by(Observation.created_at)
        ).scalars()
    )
    audit = list(
        session.execute(
            select(AuditLog)
            .where(AuditLog.engagement_id == eng.id)
            .order_by(AuditLog.created_at)
        ).scalars()
    )

    template = _env.get_template("report.html")
    html = template.render(
        engagement=eng,
        scope_items=scope_items,
        findings=findings,
        observations=observations,
        approvals=approvals,
        audit=audit,
        generated_at=datetime.now(tz=UTC),
    )

    from weasyprint import HTML  # deferred: needs GTK, not available on all hosts

    pdf_bytes = HTML(string=html).write_pdf()
    filename = f"{eng.slug}-report-{datetime.now(tz=UTC).strftime('%Y%m%d')}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )
