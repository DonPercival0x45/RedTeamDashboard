"""Stored entities — Phase 10 Maltego import + retrieval.

Two endpoints in v1:

    POST   /engagements/{slug}/entities/import/maltego  -> upload .mtgx
    GET    /engagements/{slug}/entities/stored          -> list stored

The existing ``GET /engagements/{slug}/entities`` (in
``engagements.py``) derives entities on the fly from ``Finding.target``
+ ``Finding.details``. This module owns the *stored* layer that
external imports (Maltego, future Dehashed) feed.

The Entities tab in the UI shows both: an "Imported" section sourced
from the stored table, and a "Derived from findings" section sourced
from the existing endpoint.
"""
from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.models import ActorType, AuditLog, Engagement, Entity
from app.services import darkweb_import, entity_store
from app.services.maltego_import import parse_mtgx

router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class StoredEntityRead(BaseModel):
    id: uuid.UUID
    type: str
    value: str
    properties: dict[str, Any] = Field(default_factory=dict)
    source_tool: str
    source_attribution: str | None = None
    created_at: Any
    updated_at: Any


class MaltegoImportResult(BaseModel):
    """Response shape for the .mtgx upload endpoint."""

    inserted: int
    merged: int
    skipped_empty: int
    skipped_unknown: int
    total_nodes: int
    entities: list[StoredEntityRead]


class DarkwebImportResult(BaseModel):
    """Response shape for the DarkWeb import endpoint.

    ``source`` echoes the requested source (e.g., "dehashed") so the
    UI's result panel can label the import accordingly. ``databases``
    is the distinct list of breach sources seen in the upload —
    useful header info for the analyst.
    """

    source: str
    inserted: int
    merged: int
    skipped_no_identifier: int
    skipped_malformed: int
    total_rows: int
    databases: list[str]
    entities: list[StoredEntityRead]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _engagement_by_slug(session, slug: str) -> Engagement:
    eng = session.execute(
        select(Engagement).where(Engagement.slug == slug)
    ).scalar_one_or_none()
    if eng is None:
        raise HTTPException(
            status_code=404, detail=f"engagement '{slug}' not found"
        )
    return eng


def _entity_to_read(e: Entity) -> StoredEntityRead:
    return StoredEntityRead(
        id=e.id,
        type=e.type,
        value=e.value,
        properties=dict(e.properties or {}),
        source_tool=e.source_tool,
        source_attribution=e.source_attribution,
        created_at=e.created_at,
        updated_at=e.updated_at,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/engagements/{slug}/entities/import/maltego",
    response_model=MaltegoImportResult,
    status_code=status.HTTP_201_CREATED,
)
def import_maltego(
    slug: str,
    session: DbSession,
    user: CurrentUser,
    file: Annotated[UploadFile, File(..., description="Maltego .mtgx export.")],
) -> MaltegoImportResult:
    """Import a Maltego ``.mtgx`` graph export into the stored entities table.

    Each ``MaltegoEntity`` becomes an ``Entity`` with ``source_tool="maltego_import"``
    and ``source_attribution=<filename>``. Re-imports merge into existing
    rows via UPSERT on ``(engagement_id, type, value)`` — properties
    JSONB is concatenated so prior keys not in the new payload are
    preserved.
    """
    eng = _engagement_by_slug(session, slug)
    raw = file.file.read()
    attribution = file.filename or "maltego.mtgx"
    try:
        result = parse_mtgx(raw, source_attribution=attribution)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    inserted, merged = entity_store.persist_entities(
        session,
        engagement=eng,
        items=result.items,
        source_tool="maltego_import",
        source_attribution=attribution,
    )

    session.add(
        AuditLog(
            engagement_id=eng.id,
            actor_type=ActorType.user,
            actor_id=str(user.id),
            event_type="entities.imported",
            payload={
                "source": "maltego_import",
                "filename": attribution,
                "inserted": inserted,
                "merged": merged,
                "skipped_empty": result.skipped_empty,
                "skipped_unknown": result.skipped_unknown,
                "total_nodes": result.total_nodes,
            },
        )
    )
    session.commit()

    # Return the freshly-persisted rows so the UI can render immediately.
    fresh = entity_store.list_stored_entities(session, engagement=eng)
    return MaltegoImportResult(
        inserted=inserted,
        merged=merged,
        skipped_empty=result.skipped_empty,
        skipped_unknown=result.skipped_unknown,
        total_nodes=result.total_nodes,
        entities=[_entity_to_read(e) for e in fresh],
    )


@router.post(
    "/engagements/{slug}/entities/import/darkweb",
    response_model=DarkwebImportResult,
    status_code=status.HTTP_201_CREATED,
)
def import_darkweb(
    slug: str,
    session: DbSession,
    user: CurrentUser,
    file: Annotated[
        UploadFile, File(..., description="DarkWeb source export (JSON or CSV).")
    ],
    source: Annotated[
        str,
        Query(
            description=(
                "Source name. Only 'dehashed' is supported in v1; "
                "future sources (HIBP, IntelX) will follow."
            )
        ),
    ] = "dehashed",
) -> DarkwebImportResult:
    """Import a DarkWeb data export (Dehashed today; pluggable for
    future sources). One Entity per record with type=breach_record;
    composite value ``<email-or-username>@<database_name>`` preserves
    per-breach distinction while UPSERTing cleanly on re-import.

    Format auto-detection by filename suffix:
      - ``.json`` → JSON parser
      - ``.csv`` (or no recognized suffix on a Dehashed source) → CSV parser
    """
    eng = _engagement_by_slug(session, slug)
    raw = file.file.read()
    attribution = file.filename or f"{source}.darkweb"
    filename_lower = attribution.lower()

    source = source.lower().strip()
    if source != "dehashed":
        raise HTTPException(
            status_code=400,
            detail=f"unsupported darkweb source {source!r}; only 'dehashed' is wired",
        )

    try:
        if filename_lower.endswith(".json"):
            result = darkweb_import.parse_dehashed_json(
                raw, source_attribution=attribution
            )
        elif filename_lower.endswith(".csv"):
            result = darkweb_import.parse_dehashed_csv(
                raw, source_attribution=attribution
            )
        else:
            # No clear suffix — try JSON first (Dehashed API responses
            # tend to come as raw JSON with no extension), fall back to CSV.
            try:
                result = darkweb_import.parse_dehashed_json(
                    raw, source_attribution=attribution
                )
            except ValueError:
                result = darkweb_import.parse_dehashed_csv(
                    raw, source_attribution=attribution
                )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    inserted, merged = entity_store.persist_entities(
        session,
        engagement=eng,
        items=result.items,
        source_tool=f"{source}_import",
        source_attribution=attribution,
    )

    session.add(
        AuditLog(
            engagement_id=eng.id,
            actor_type=ActorType.user,
            actor_id=str(user.id),
            event_type="entities.imported",
            payload={
                "source": f"{source}_import",
                "filename": attribution,
                "inserted": inserted,
                "merged": merged,
                "skipped_no_identifier": result.skipped_no_identifier,
                "skipped_malformed": result.skipped_malformed,
                "total_rows": result.total_rows,
                "databases": result.databases,
            },
        )
    )
    session.commit()

    fresh = entity_store.list_stored_entities(session, engagement=eng)
    return DarkwebImportResult(
        source=source,
        inserted=inserted,
        merged=merged,
        skipped_no_identifier=result.skipped_no_identifier,
        skipped_malformed=result.skipped_malformed,
        total_rows=result.total_rows,
        databases=result.databases,
        entities=[_entity_to_read(e) for e in fresh],
    )


@router.get(
    "/engagements/{slug}/entities/stored",
    response_model=list[StoredEntityRead],
)
def list_stored_entities_endpoint(
    slug: str,
    session: DbSession,
    type: Annotated[str | None, Query(description="Filter by type.")] = None,
    q: Annotated[
        str | None, Query(description="Substring match on the value.")
    ] = None,
) -> list[StoredEntityRead]:
    """Stored entities for the engagement (Maltego imports + future sources).

    Distinct from ``GET /engagements/{slug}/entities`` which derives
    entities from findings on the fly. The UI Entities tab shows both
    layers as separate sections.
    """
    eng = _engagement_by_slug(session, slug)
    rows = entity_store.list_stored_entities(
        session, engagement=eng, type_filter=type, query=q
    )
    return [_entity_to_read(e) for e in rows]
