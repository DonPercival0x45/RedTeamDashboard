"""Project CRUD, export, flush, and shared route helpers for Project X-Ray.

Endpoints::

    POST   /projects                                 -> create
    GET    /projects                                 -> list (?status filter)
    GET    /projects/{slug}                          -> read
    PATCH  /projects/{slug}                          -> rename / archive / unarchive
    DELETE /projects/{slug}                          -> soft archive
    POST   /projects/{slug}/flush                    -> irreversible (calls flush_engagement)
    GET    /projects/{slug}/export                   -> full JSON snapshot
    POST   /projects/{slug}/export                   -> export to blob storage (admin)

Shared helpers exported for use by other route modules::

    _slugify, _unique_slug, _get_project_or_404, _build_export_payload, _reject_flushed
"""
from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select, text

from app.api.deps import CurrentUser, DbSession, RedisClient, RequireScope
from app.core.blob import upload_engagement_export
from app.models import (
    AuditLog,
    Finding,
    Observation,
    Project,
    ProjectStatus,
    ScopeItem,
)
from app.models.api_key import APIKeyScope
from app.projects.schemas import ProjectCreate, ProjectRead, ProjectUpdate
from app.runs.streams import inbound_stream, outbound_stream

router = APIRouter()


# ---------------------------------------------------------------------------
# Shared helpers (exported for use by scope, findings, observations, runs)
# ---------------------------------------------------------------------------


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(name: str) -> str:
    cleaned = _SLUG_RE.sub("-", name.lower()).strip("-")
    return cleaned or "Project"


def _unique_slug(session: DbSession, base: str) -> str:
    candidate = base
    while session.execute(
        select(Project.id).where(Project.slug == candidate)
    ).first():
        candidate = f"{base}-{uuid.uuid4().hex[:6]}"
    return candidate


def _get_project_or_404(session: DbSession, slug: str) -> Project:
    eng = session.execute(
        select(Project).where(Project.slug == slug)
    ).scalar_one_or_none()
    if eng is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return eng


def _build_export_payload(session: DbSession, eng: Project) -> dict[str, Any]:
    """Assemble a complete Project snapshot suitable for blob archiving."""
    scope_items = list(
        session.execute(select(ScopeItem).where(ScopeItem.project_id == eng.id)).scalars()
    )
    findings = list(
        session.execute(select(Finding).where(Finding.project_id == eng.id)).scalars()
    )
    audit_rows = list(
        session.execute(
            select(AuditLog)
            .where(AuditLog.project_id == eng.id)
            .order_by(AuditLog.created_at)
        ).scalars()
    )
    audit_summary: dict[str, Any] = {"count": len(audit_rows)}
    if audit_rows:
        audit_summary["first"] = str(audit_rows[0].created_at)
        audit_summary["last"] = str(audit_rows[-1].created_at)

    observations = list(
        session.execute(
            select(Observation)
            .where(Observation.project_id == eng.id)
            .order_by(Observation.created_at)
        ).scalars()
    )

    return {
        "version": "1",
        "exported_at": str(datetime.now(tz=UTC)),
        "Project": {
            "id": str(eng.id),
            "slug": eng.slug,
            "name": eng.name,
            "status": eng.status,
            "description": eng.description,
            "created_at": str(eng.created_at),
            "archived_at": str(eng.archived_at) if eng.archived_at else None,
        },
        "scope": [
            {"kind": s.kind, "value": s.value, "is_exclusion": s.is_exclusion, "note": s.note}
            for s in scope_items
        ],
        "findings": [
            {
                "id": str(f.id),
                "title": f.title,
                "severity": f.severity,
                "status": f.status,
                "target": f.target,
                "source_tool": f.source_tool,
                "phase": f.phase,
                "summary": f.summary,
                "details": f.details,
                "created_at": str(f.created_at),
            }
            for f in findings
        ],
        "observations": [
            {
                "content": o.content,
                "phase": o.phase,
                "created_at": str(o.created_at),
            }
            for o in observations
        ],
        "audit_summary": audit_summary,
    }


def _reject_flushed(eng: Project) -> None:
    if eng.status is ProjectStatus.flushed:
        raise HTTPException(
            status_code=409,
            detail="Project has been flushed; the row will be gone shortly",
        )


# ---------------------------------------------------------------------------
# Project CRUD
# ---------------------------------------------------------------------------


@router.post(
    "/projects",
    response_model=ProjectRead,
    status_code=status.HTTP_201_CREATED,
)
def create_engagement(
    body: ProjectCreate,
    session: DbSession,
    user: CurrentUser,
) -> Project:
    base_slug = _slugify(body.slug) if body.slug else _slugify(body.name)
    slug = _unique_slug(session, base_slug)
    eng = Project(
        name=body.name,
        slug=slug,
        description=body.description,
        status=ProjectStatus.active,
        created_by=user.id,
    )
    session.add(eng)
    session.commit()
    session.refresh(eng)
    return eng


@router.get("/projects", response_model=list[ProjectRead])
def list_engagements(
    session: DbSession,
    status_filter: Annotated[
        ProjectStatus | None,
        Query(alias="status", description="Filter by status."),
    ] = None,
) -> list[Project]:
    stmt = select(Project)
    if status_filter is not None:
        stmt = stmt.where(Project.status == status_filter)
    stmt = stmt.order_by(Project.created_at.desc())
    return list(session.execute(stmt).scalars())


@router.get("/projects/{slug}", response_model=ProjectRead)
def get_engagement(slug: str, session: DbSession) -> Project:
    return _get_project_or_404(session, slug)


@router.patch("/projects/{slug}", response_model=ProjectRead)
def update_engagement(
    slug: str,
    body: ProjectUpdate,
    session: DbSession,
) -> Project:
    eng = _get_project_or_404(session, slug)
    _reject_flushed(eng)

    if body.name is not None:
        eng.name = body.name

    if body.status is not None:
        if body.status is ProjectStatus.flushed:
            raise HTTPException(
                status_code=400,
                detail="use POST /projects/{slug}/flush to flush",
            )
        if body.status is ProjectStatus.active and eng.status is ProjectStatus.archived:
            eng.archived_at = None
        elif (
            body.status is ProjectStatus.archived
            and eng.status is ProjectStatus.active
        ):
            eng.archived_at = datetime.now(tz=UTC)
        eng.status = body.status

    session.commit()
    session.refresh(eng)
    return eng


@router.post("/projects/{slug}/export", dependencies=[Depends(RequireScope(APIKeyScope.admin))])
def export_engagement(slug: str, session: DbSession) -> dict[str, Any]:
    """Export all Project data (findings, scope, audit summary) to blob storage.

    Returns the blob URL if storage is configured, or the full payload inline
    if AZURE_STORAGE_ACCOUNT_NAME is unset (useful for local dev / manual backup).
    Requires admin scope.
    """
    eng = _get_project_or_404(session, slug)
    payload = _build_export_payload(session, eng)
    blob_url = upload_engagement_export(slug, payload)
    if blob_url:
        return {"slug": slug, "blob_url": blob_url}
    return {"slug": slug, "blob_url": None, "payload": payload}


@router.delete(
    "/projects/{slug}",
    response_model=ProjectRead,
)
def archive_engagement(slug: str, session: DbSession, _user: CurrentUser) -> Project:
    eng = _get_project_or_404(session, slug)
    _reject_flushed(eng)
    if eng.status is not ProjectStatus.archived:
        eng.status = ProjectStatus.archived
        eng.archived_at = datetime.now(tz=UTC)
        session.commit()
        session.refresh(eng)
        # Export to blob; failure doesn't block the archive.
        upload_engagement_export(slug, _build_export_payload(session, eng))
    else:
        session.commit()
        session.refresh(eng)
    return eng


@router.post("/projects/{slug}/flush", status_code=204)
def flush_engagement(
    slug: str,
    session: DbSession,
    redis_client: RedisClient,
    _user: CurrentUser,
) -> Response:
    """Permanently delete all Project data. Export to blob first, then purge."""
    eng = _get_project_or_404(session, slug)
    eid = eng.id
    slug_val = eng.slug

    # Export before destroying — failure is logged but doesn't block the flush.
    payload = _build_export_payload(session, eng)
    upload_engagement_export(slug_val, payload)

    # The DB-side flush_engagement() handles audit_log + engagements (with
    # cascades to scope_items, findings, approvals). Streams aren't FKs, so we
    # explicitly drop them here.
    session.execute(text("SELECT flush_engagement(:id)"), {"id": eid})
    session.commit()
    redis_client.delete(inbound_stream(eid), outbound_stream(eid))
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Project JSON export
# ---------------------------------------------------------------------------


@router.get("/projects/{slug}/export")
def export_engagement(
    slug: str,
    session: DbSession,
    _user: CurrentUser,
) -> dict[str, Any]:
    """Full Project snapshot as structured JSON — findings, scope, observations,
    and audit summary. Suitable for archiving or importing into another instance."""
    eng = _get_project_or_404(session, slug)
    return _build_export_payload(session, eng)
