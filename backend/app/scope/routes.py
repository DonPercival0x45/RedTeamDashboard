"""Scope endpoints for Project X-Ray.

Endpoints::

    POST   /projects/{slug}/scope                    -> create scope item
    GET    /projects/{slug}/scope                    -> list scope items
    PATCH  /projects/{slug}/scope/{scope_id}         -> update
    DELETE /projects/{slug}/scope/{scope_id}         -> remove
    POST   /scope/parse                              -> preview parse (no DB write)
    POST   /projects/{slug}/scope/import             -> bulk import from text blob
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.models import ActorType, AuditLog, ScopeItem
from app.projects.routes import _get_project_or_404, _reject_flushed
from app.projects.schemas import (
    ScopeImportPreview,
    ScopeImportRequest,
    ScopeImportResult,
    ScopeItemCreate,
    ScopeItemRead,
    ScopeItemUpdate,
)
from app.scope.parser import parse_scope_text

router = APIRouter()


# ---------------------------------------------------------------------------
# Scope CRUD (nested under Project)
# ---------------------------------------------------------------------------


@router.post(
    "/projects/{slug}/scope",
    response_model=ScopeItemRead,
    status_code=status.HTTP_201_CREATED,
)
def create_scope_item(
    slug: str,
    body: ScopeItemCreate,
    session: DbSession,
) -> ScopeItem:
    eng = _get_project_or_404(session, slug)
    _reject_flushed(eng)
    item = ScopeItem(
        project_id=eng.id,
        kind=body.kind,
        value=body.value,
        is_exclusion=body.is_exclusion,
        note=body.note,
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


@router.post(
    "/scope/parse",
    response_model=ScopeImportPreview,
)
def parse_scope_blob(
    body: ScopeImportRequest, _user: CurrentUser
) -> ScopeImportPreview:
    """Pure parser — no Project, no DB writes.

    Lets the /new wizard preview an import before the Project exists.
    Same parser the /scope/import endpoint uses; results are interchangeable.
    """
    rows, errors = parse_scope_text(body.text)
    return ScopeImportPreview(
        preview=[
            {
                "line": r.line,
                "value": r.value,
                "kind": r.kind,
                "is_exclusion": r.is_exclusion,
            }
            for r in rows
        ],
        errors=[
            {"line": e.line, "raw": e.raw, "reason": e.reason} for e in errors
        ],
        would_create=len(rows),
    )


@router.post(
    "/projects/{slug}/scope/import",
    response_model=ScopeImportPreview | ScopeImportResult,
)
def import_scope(
    slug: str,
    body: ScopeImportRequest,
    session: DbSession,
    user: CurrentUser,
    dry_run: bool = False,
) -> ScopeImportPreview | ScopeImportResult:
    """Bulk-import scope items from a free-form text blob.

    Same parser whether the analyst uploaded a file (client read it as text)
    or pasted into a textarea. ``?dry_run=true`` returns the preview without
    persisting; the UI calls it on each debounced keystroke. The real commit
    de-dupes against the Project's existing (kind, value, is_exclusion)
    tuples so re-running an import is safe.
    """
    eng = _get_project_or_404(session, slug)
    _reject_flushed(eng)
    rows, errors = parse_scope_text(body.text)

    error_rows = [
        {"line": e.line, "raw": e.raw, "reason": e.reason} for e in errors
    ]

    if dry_run:
        return ScopeImportPreview(
            preview=[
                {
                    "line": r.line,
                    "value": r.value,
                    "kind": r.kind,
                    "is_exclusion": r.is_exclusion,
                }
                for r in rows
            ],
            errors=error_rows,
            would_create=len(rows),
        )

    existing = list(
        session.execute(
            select(ScopeItem).where(ScopeItem.project_id == eng.id)
        ).scalars()
    )
    seen = {(s.kind, s.value, s.is_exclusion) for s in existing}

    created: list[ScopeItem] = []
    duplicates: list[dict[str, Any]] = []
    for r in rows:
        key = (r.kind, r.value, r.is_exclusion)
        if key in seen:
            duplicates.append(
                {
                    "line": r.line,
                    "value": r.value,
                    "kind": r.kind,
                    "is_exclusion": r.is_exclusion,
                }
            )
            continue
        item = ScopeItem(
            project_id=eng.id,
            kind=r.kind,
            value=r.value,
            is_exclusion=r.is_exclusion,
        )
        session.add(item)
        seen.add(key)
        created.append(item)

    session.flush()
    if created:
        session.add(
            AuditLog(
                project_id=eng.id,
                actor_type=ActorType.user,
                actor_id=str(user.id),
                event_type="scope.imported",
                payload={
                    "created_count": len(created),
                    "error_count": len(errors),
                    "duplicate_count": len(duplicates),
                },
            )
        )
    session.commit()
    for c in created:
        session.refresh(c)

    return ScopeImportResult(
        created=[ScopeItemRead.model_validate(c) for c in created],
        errors=error_rows,
        duplicates=duplicates,
    )


@router.get(
    "/projects/{slug}/scope",
    response_model=list[ScopeItemRead],
)
def list_scope(slug: str, session: DbSession) -> list[ScopeItem]:
    eng = _get_project_or_404(session, slug)
    rows = session.execute(
        select(ScopeItem)
        .where(ScopeItem.project_id == eng.id)
        .order_by(ScopeItem.created_at)
    ).scalars()
    return list(rows)


@router.patch(
    "/projects/{slug}/scope/{scope_id}",
    response_model=ScopeItemRead,
)
def update_scope_item(
    slug: str,
    scope_id: uuid.UUID,
    body: ScopeItemUpdate,
    session: DbSession,
) -> ScopeItem:
    eng = _get_project_or_404(session, slug)
    _reject_flushed(eng)
    item = session.get(ScopeItem, scope_id)
    if item is None or item.project_id != eng.id:
        raise HTTPException(status_code=404, detail="scope item not found")
    if body.value is not None:
        item.value = body.value
    if body.is_exclusion is not None:
        item.is_exclusion = body.is_exclusion
    if body.note is not None:
        item.note = body.note
    session.commit()
    session.refresh(item)
    return item


@router.delete(
    "/projects/{slug}/scope/{scope_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_scope_item(
    slug: str,
    scope_id: uuid.UUID,
    session: DbSession,
) -> Response:
    eng = _get_project_or_404(session, slug)
    item = session.get(ScopeItem, scope_id)
    if item is None or item.project_id != eng.id:
        raise HTTPException(status_code=404, detail="scope item not found")
    session.delete(item)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
