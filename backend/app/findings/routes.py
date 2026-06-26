"""Findings, entities, and attachment endpoints for Project X-Ray.

Endpoints::

    GET    /projects/{slug}/findings                 -> list persisted findings
    GET    /projects/{slug}/entities                 -> correlated entities
    POST   /findings/{finding_id}/validate           -> promote/reject a finding
    POST   /projects/{slug}/findings/import          -> bulk import findings
    PATCH  /findings/{finding_id}                    -> update title/summary/severity/phase

    POST   /findings/{finding_id}/attachments        -> upload screenshot/evidence file
    GET    /findings/{finding_id}/attachments        -> list attachment metadata
    GET    /attachments/{attachment_id}              -> serve raw bytes
    DELETE /attachments/{attachment_id}              -> delete
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, File, HTTPException, Query, Response, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.findings.entities import extract_entities
from app.findings.schemas import AttachmentRead, EntityRead, FindingRead, FindingUpdate, FindingValidate
from app.models import (
    ActorType,
    Attachment,
    AuditLog,
    Finding,
    FindingPhase,
    FindingStatus,
    Severity,
)
from app.projects.routes import _get_project_or_404, _reject_flushed

router = APIRouter()


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------


def _finding_to_read(f: Finding) -> dict[str, Any]:
    """Unpack a persisted Finding into the same shape the SSE
    ``finding.created`` event carries.

    The worker stores ``details = {"thread_id": ..., "args": ..., **tool_data}``
    (see ``RunRunner._persist_finding``); we pop the envelope keys back out so
    the remainder is the raw tool data, letting the UI render hydrated and live
    findings through one code path.
    """
    details = dict(f.details or {})
    thread_id = details.pop("thread_id", None)
    args = details.pop("args", {})
    return {
        "id": f.id,
        "thread_id": str(thread_id) if thread_id is not None else None,
        "tool": f.source_tool,
        "target": f.target,
        "args": args if isinstance(args, dict) else {},
        "data": details,
        "severity": f.severity,
        "title": f.title,
        "summary": f.summary,
        "phase": f.phase,
        "status": f.status,
        "validated_at": f.validated_at,
        "created_at": f.created_at,
    }


# ---------------------------------------------------------------------------
# Findings (read-only; written by the worker)
# ---------------------------------------------------------------------------


@router.get(
    "/projects/{slug}/findings",
    response_model=list[FindingRead],
)
def list_findings(
    slug: str,
    session: DbSession,
    phase: Annotated[FindingPhase | None, Query(description="Filter by phase.")] = None,
    status: Annotated[
        FindingStatus | None, Query(description="Filter by validation status.")
    ] = None,
) -> list[dict[str, Any]]:
    eng = _get_project_or_404(session, slug)
    stmt = select(Finding).where(Finding.project_id == eng.id)
    if phase is not None:
        stmt = stmt.where(Finding.phase == phase)
    if status is not None:
        stmt = stmt.where(Finding.status == status)
    rows = session.execute(stmt.order_by(Finding.created_at.desc())).scalars()
    return [_finding_to_read(f) for f in rows]


@router.get(
    "/projects/{slug}/entities",
    response_model=list[EntityRead],
)
def list_entities(
    slug: str,
    session: DbSession,
    type: Annotated[str | None, Query(description="Filter by entity type.")] = None,
    q: Annotated[str | None, Query(description="Substring match on the value.")] = None,
) -> list[dict[str, Any]]:
    """Entities correlated across this Project's findings (CHARTER Idea 4)."""
    eng = _get_project_or_404(session, slug)
    findings = list(
        session.execute(
            select(Finding)
            .where(Finding.project_id == eng.id)
            .order_by(Finding.created_at)
        ).scalars()
    )
    return extract_entities(findings, type_filter=type, query=q)


@router.post(
    "/findings/{finding_id}/validate",
    response_model=FindingRead,
)
def validate_finding(
    finding_id: uuid.UUID,
    body: FindingValidate,
    session: DbSession,
    user: CurrentUser,
) -> dict[str, Any]:
    """Promote/reject a pending finding. ``validated`` makes it report-eligible;
    ``rejected`` / ``false_positive`` keep it for audit but exclude it."""
    finding = session.get(Finding, finding_id)
    if finding is None:
        raise HTTPException(status_code=404, detail="finding not found")

    finding.status = body.decision
    if body.decision is FindingStatus.validated:
        finding.validated_by = user.id
        finding.validated_at = datetime.now(tz=UTC)
    else:
        # Re-deciding away from validated clears the validation stamp.
        finding.validated_by = None
        finding.validated_at = None

    session.add(
        AuditLog(
            project_id=finding.project_id,
            actor_type=ActorType.user,
            actor_id=str(user.id),
            event_type="finding.validated",
            payload={
                "finding_id": str(finding.id),
                "decision": body.decision.value,
                "reason": body.reason,
            },
        )
    )
    session.commit()
    session.refresh(finding)
    return _finding_to_read(finding)


# ---------------------------------------------------------------------------
# Findings import
# ---------------------------------------------------------------------------


class FindingImport(BaseModel):
    """Single finding in a bulk import payload."""

    title: str
    severity: Severity = Severity.info
    phase: FindingPhase = FindingPhase.general
    summary: str | None = None
    target: str | None = None
    source_tool: str | None = "import"
    details: dict[str, Any] = {}


@router.post(
    "/projects/{slug}/findings/import",
    response_model=list[FindingRead],
    status_code=status.HTTP_201_CREATED,
)
def import_findings(
    slug: str,
    body: list[FindingImport],
    session: DbSession,
    user: CurrentUser,
) -> list[dict[str, Any]]:
    """Bulk-import findings from an external source (scanner output, prior report, etc.).

    All imported findings land as ``pending_validation`` so the analyst can
    review before they become report-eligible. ``source_tool`` defaults to
    ``'import'`` if omitted.
    """
    if not body:
        return []

    eng = _get_project_or_404(session, slug)
    _reject_flushed(eng)

    created: list[Finding] = []
    for item in body:
        f = Finding(
            project_id=eng.id,
            title=item.title,
            severity=item.severity,
            phase=item.phase,
            summary=item.summary,
            target=item.target,
            source_tool=item.source_tool or "import",
            details=item.details,
            status=FindingStatus.pending_validation,
        )
        session.add(f)
        created.append(f)

    session.add(
        AuditLog(
            project_id=eng.id,
            actor_type=ActorType.user,
            actor_id=str(user.id),
            event_type="findings.imported",
            payload={"count": len(created), "source": "bulk_import"},
        )
    )
    session.commit()
    for f in created:
        session.refresh(f)
    return [_finding_to_read(f) for f in created]


# ---------------------------------------------------------------------------
# Finding update (title / summary / severity / phase)
# ---------------------------------------------------------------------------


@router.patch(
    "/findings/{finding_id}",
    response_model=FindingRead,
)
def update_finding(
    finding_id: uuid.UUID,
    body: FindingUpdate,
    session: DbSession,
    user: CurrentUser,
) -> dict[str, Any]:
    """Edit analyst-controlled fields on a finding. Only provided fields change;
    omitted fields are left as-is. ``summary`` accepts ``null`` to clear it."""
    finding = session.get(Finding, finding_id)
    if finding is None:
        raise HTTPException(status_code=404, detail="finding not found")

    changed: dict[str, Any] = {}
    if "title" in body.model_fields_set and body.title is not None:
        finding.title = body.title
        changed["title"] = body.title
    if "summary" in body.model_fields_set:
        finding.summary = body.summary
        changed["summary"] = body.summary
    if "severity" in body.model_fields_set and body.severity is not None:
        finding.severity = body.severity
        changed["severity"] = body.severity.value
    if "phase" in body.model_fields_set and body.phase is not None:
        finding.phase = body.phase
        changed["phase"] = body.phase.value

    if changed:
        session.add(
            AuditLog(
                project_id=finding.project_id,
                actor_type=ActorType.user,
                actor_id=str(user.id),
                event_type="finding.updated",
                payload={"finding_id": str(finding.id), "changes": changed},
            )
        )
        session.commit()
        session.refresh(finding)

    return _finding_to_read(finding)


# ---------------------------------------------------------------------------
# Finding attachments (screenshots / evidence files)
# ---------------------------------------------------------------------------

_MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024  # 10 MB


@router.post(
    "/findings/{finding_id}/attachments",
    response_model=AttachmentRead,
    status_code=status.HTTP_201_CREATED,
)
async def upload_attachment(
    finding_id: uuid.UUID,
    file: Annotated[UploadFile, File()],
    session: DbSession,
    user: CurrentUser,
) -> Attachment:
    """Upload a screenshot or evidence file and attach it to the finding."""
    finding = session.get(Finding, finding_id)
    if finding is None:
        raise HTTPException(status_code=404, detail="finding not found")

    data = await file.read()
    if len(data) > _MAX_ATTACHMENT_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"file too large — max {_MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB",
        )

    attachment = Attachment(
        finding_id=finding_id,
        project_id=finding.project_id,
        filename=file.filename or "attachment",
        content_type=file.content_type or "application/octet-stream",
        size_bytes=len(data),
        data=data,
        created_by=str(user.id),
    )
    session.add(attachment)
    session.add(
        AuditLog(
            project_id=finding.project_id,
            actor_type=ActorType.user,
            actor_id=str(user.id),
            event_type="attachment.uploaded",
            payload={
                "finding_id": str(finding_id),
                "filename": attachment.filename,
                "size_bytes": attachment.size_bytes,
            },
        )
    )
    session.commit()
    session.refresh(attachment)
    return attachment


@router.get(
    "/findings/{finding_id}/attachments",
    response_model=list[AttachmentRead],
)
def list_attachments(
    finding_id: uuid.UUID,
    session: DbSession,
    _user: CurrentUser,
) -> list[Attachment]:
    """List attachment metadata for a finding (no raw bytes — fetch individually)."""
    finding = session.get(Finding, finding_id)
    if finding is None:
        raise HTTPException(status_code=404, detail="finding not found")
    return list(
        session.execute(
            select(Attachment)
            .where(Attachment.finding_id == finding_id)
            .order_by(Attachment.created_at)
        ).scalars()
    )


@router.get("/attachments/{attachment_id}")
def serve_attachment(
    attachment_id: uuid.UUID,
    session: DbSession,
    _user: CurrentUser,
) -> Response:
    """Serve the raw bytes of an attachment with its original content-type."""
    attachment = session.get(Attachment, attachment_id)
    if attachment is None:
        raise HTTPException(status_code=404, detail="attachment not found")
    return Response(
        content=attachment.data,
        media_type=attachment.content_type,
        headers={
            "Content-Disposition": f'inline; filename="{attachment.filename}"',
            "Cache-Control": "private, max-age=3600",
        },
    )


@router.delete("/attachments/{attachment_id}")
def delete_attachment(
    attachment_id: uuid.UUID,
    session: DbSession,
    user: CurrentUser,
) -> Response:
    """Delete an attachment."""
    attachment = session.get(Attachment, attachment_id)
    if attachment is None:
        raise HTTPException(status_code=404, detail="attachment not found")
    session.add(
        AuditLog(
            project_id=attachment.project_id,
            actor_type=ActorType.user,
            actor_id=str(user.id),
            event_type="attachment.deleted",
            payload={
                "attachment_id": str(attachment_id),
                "filename": attachment.filename,
            },
        )
    )
    session.delete(attachment)
    session.commit()
    return Response(status_code=204)
