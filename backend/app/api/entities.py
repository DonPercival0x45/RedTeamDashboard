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
from sqlalchemy.orm import object_session

from app.api.deps import CurrentNonGuestUser, CurrentUser, DbSession
from app.models import (
    ActorType,
    AuditLog,
    Engagement,
    EngagementStatus,
    EngagementWorkState,
    Entity,
    EntityFindingLink,
    Finding,
    FindingPhase,
    FindingStatus,
    ScopeItem,
    ScopeKind,
    Severity,
)
from app.services import darkweb_import, entity_store
from app.services.entities import extract_finding_context
from app.services.maltego_import import parse_mtgx

router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class StoredEntityFindingRef(BaseModel):
    id: uuid.UUID
    title: str
    tool: str | None = None
    severity: Severity
    phase: FindingPhase
    status: FindingStatus


class StoredEntityRead(BaseModel):
    id: uuid.UUID
    type: str
    value: str
    properties: dict[str, Any] = Field(default_factory=dict)
    source_tool: str
    source_attribution: str | None = None
    finding_refs: list[StoredEntityFindingRef] = Field(default_factory=list)
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


class FindingContextCandidate(BaseModel):
    type: str
    value: str
    entity_id: uuid.UUID | None = None
    scope_item_id: uuid.UUID | None = None
    scope_source: str | None = None
    scope_compatible: bool


class FindingContextPromotionItem(BaseModel):
    type: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=500)
    add_to_entities: bool = True
    add_to_scope: bool = False


class FindingContextPromotionRequest(BaseModel):
    items: list[FindingContextPromotionItem] = Field(min_length=1, max_length=100)


class FindingContextPromotionResult(BaseModel):
    entities_created: int
    entity_links_created: int
    scope_items_created: int
    candidates: list[FindingContextCandidate]


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
    eng = session.execute(select(Engagement).where(Engagement.slug == slug)).scalar_one_or_none()
    if eng is None:
        raise HTTPException(status_code=404, detail=f"engagement '{slug}' not found")
    return eng


def _finding_or_404(session, finding_id: uuid.UUID) -> Finding:
    finding = session.get(Finding, finding_id)
    if finding is None or finding.deleted_at is not None:
        raise HTTPException(status_code=404, detail="finding not found")
    return finding


def _ensure_mutable(engagement: Engagement) -> None:
    session = object_session(engagement)
    if session is not None:
        session.refresh(engagement, with_for_update=True)
    if engagement.status == EngagementStatus.flushed:
        raise HTTPException(status_code=404, detail="engagement not found")
    if engagement.status == EngagementStatus.archived:
        raise HTTPException(status_code=409, detail="archived engagement is read-only")
    if engagement.work_state == EngagementWorkState.completed:
        raise HTTPException(status_code=409, detail="completed engagement is read-only")


def _context_candidates(session, finding: Finding) -> list[FindingContextCandidate]:
    extracted = extract_finding_context(finding)
    if not extracted:
        return []
    entities = list(
        session.execute(
            select(Entity).where(Entity.engagement_id == finding.engagement_id)
        ).scalars()
    )
    scope = list(
        session.execute(
            select(ScopeItem).where(ScopeItem.engagement_id == finding.engagement_id)
        ).scalars()
    )
    entity_map = {(row.type, row.value): row for row in entities}
    scope_map = {(row.kind.value, row.value): row for row in scope}
    compatible = {kind.value for kind in ScopeKind}
    candidates: list[FindingContextCandidate] = []
    for kind, value in extracted:
        entity = entity_map.get((kind, value))
        scope_item = scope_map.get((kind, value))
        candidates.append(
            FindingContextCandidate(
                type=kind,
                value=value,
                entity_id=entity.id if entity else None,
                scope_item_id=scope_item.id if scope_item else None,
                scope_source=scope_item.source if scope_item else None,
                scope_compatible=kind in compatible,
            )
        )
    return candidates


def _entities_to_read(session, entities: list[Entity]) -> list[StoredEntityRead]:
    """Project stored entities with typed finding provenance in one query."""
    if not entities:
        return []
    refs_by_entity: dict[uuid.UUID, list[StoredEntityFindingRef]] = {
        entity.id: [] for entity in entities
    }
    rows = session.execute(
        select(
            EntityFindingLink.entity_id,
            Finding.id,
            Finding.title,
            Finding.source_tool,
            Finding.severity,
            Finding.phase,
            Finding.status,
        )
        .join(Finding, Finding.id == EntityFindingLink.finding_id)
        .where(
            EntityFindingLink.entity_id.in_(refs_by_entity),
            Finding.engagement_id == entities[0].engagement_id,
            Finding.deleted_at.is_(None),
        )
        .order_by(EntityFindingLink.created_at.asc())
    ).all()
    for entity_id, finding_id, title, tool, severity, phase, finding_status in rows:
        refs_by_entity[entity_id].append(
            StoredEntityFindingRef(
                id=finding_id,
                title=title,
                tool=tool,
                severity=severity,
                phase=phase,
                status=finding_status,
            )
        )
    return [
        StoredEntityRead(
            id=entity.id,
            type=entity.type,
            value=entity.value,
            properties=dict(entity.properties or {}),
            source_tool=entity.source_tool,
            source_attribution=entity.source_attribution,
            finding_refs=refs_by_entity[entity.id],
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )
        for entity in entities
    ]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/findings/{finding_id}/context-candidates",
    response_model=list[FindingContextCandidate],
)
def list_finding_context_candidates(
    finding_id: uuid.UUID,
    session: DbSession,
    _user: CurrentUser,
) -> list[FindingContextCandidate]:
    """Extract entity/scope candidates without changing engagement state."""
    return _context_candidates(session, _finding_or_404(session, finding_id))


@router.post(
    "/findings/{finding_id}/context/promote",
    response_model=FindingContextPromotionResult,
)
def promote_finding_context(
    finding_id: uuid.UUID,
    body: FindingContextPromotionRequest,
    session: DbSession,
    user: CurrentNonGuestUser,
) -> FindingContextPromotionResult:
    """Persist analyst-selected finding context as entities and/or found scope.

    Adding Found Scope expands the targets eligible for later approval-gated
    enumeration and scan actions, so the UI presents an explicit confirmation.
    Every mutation is provenance-linked and audit logged here.
    """
    finding = _finding_or_404(session, finding_id)
    engagement = session.get(Engagement, finding.engagement_id)
    if engagement is None:
        raise HTTPException(status_code=404, detail="engagement not found")
    _ensure_mutable(engagement)

    entities_created = 0
    links_created = 0
    scope_created = 0
    promoted: list[dict[str, Any]] = []
    scope_kinds = {kind.value: kind for kind in ScopeKind}

    for requested in body.items:
        kind = requested.type.strip().lower()
        value = requested.value.strip()
        if kind in {"domain", "subdomain"}:
            value = value.lower()
        if not (requested.add_to_entities or requested.add_to_scope):
            raise HTTPException(
                status_code=400,
                detail="each promotion item must select entities, scope, or both",
            )
        if requested.add_to_scope and kind not in scope_kinds:
            raise HTTPException(
                status_code=400,
                detail=f"entity type {kind!r} cannot be added to scope",
            )

        if requested.add_to_entities:
            entity = session.execute(
                select(Entity).where(
                    Entity.engagement_id == finding.engagement_id,
                    Entity.type == kind,
                    Entity.value == value,
                )
            ).scalar_one_or_none()
            if entity is None:
                entity = Entity(
                    engagement_id=finding.engagement_id,
                    type=kind,
                    value=value,
                    properties={},
                    source_tool="finding_promotion",
                    source_attribution=f"finding:{finding.id}",
                )
                session.add(entity)
                session.flush()
                entities_created += 1

            linked = session.execute(
                select(EntityFindingLink.id).where(
                    EntityFindingLink.entity_id == entity.id,
                    EntityFindingLink.finding_id == finding.id,
                )
            ).scalar_one_or_none()
            if linked is None:
                session.add(
                    EntityFindingLink(
                        entity_id=entity.id,
                        finding_id=finding.id,
                        created_by=user.id,
                    )
                )
                links_created += 1

        if requested.add_to_scope:
            scope_kind = scope_kinds[kind]
            existing_scope = session.execute(
                select(ScopeItem.id).where(
                    ScopeItem.engagement_id == finding.engagement_id,
                    ScopeItem.kind == scope_kind,
                    ScopeItem.value == value,
                    ScopeItem.is_exclusion.is_(False),
                )
            ).scalar_one_or_none()
            if existing_scope is None:
                session.add(
                    ScopeItem(
                        engagement_id=finding.engagement_id,
                        kind=scope_kind,
                        value=value,
                        is_exclusion=False,
                        source="found",
                        note=f"Promoted from finding {finding.id}",
                    )
                )
                scope_created += 1

        promoted.append(
            {
                "type": kind,
                "value": value,
                "entity": requested.add_to_entities,
                "scope": requested.add_to_scope,
            }
        )

    session.add(
        AuditLog(
            engagement_id=finding.engagement_id,
            actor_type=ActorType.user,
            actor_id=str(user.id),
            event_type="finding.context_promoted",
            payload={
                "finding_id": str(finding.id),
                "entities_created": entities_created,
                "entity_links_created": links_created,
                "scope_items_created": scope_created,
                "items": promoted,
            },
        )
    )
    session.commit()
    return FindingContextPromotionResult(
        entities_created=entities_created,
        entity_links_created=links_created,
        scope_items_created=scope_created,
        candidates=_context_candidates(session, finding),
    )


@router.post(
    "/engagements/{slug}/entities/import/maltego",
    response_model=MaltegoImportResult,
    status_code=status.HTTP_201_CREATED,
)
def import_maltego(
    slug: str,
    session: DbSession,
    user: CurrentNonGuestUser,
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
    _ensure_mutable(eng)
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
        entities=_entities_to_read(session, fresh),
    )


@router.post(
    "/engagements/{slug}/entities/import/darkweb",
    response_model=DarkwebImportResult,
    status_code=status.HTTP_201_CREATED,
)
def import_darkweb(
    slug: str,
    session: DbSession,
    user: CurrentNonGuestUser,
    file: Annotated[UploadFile, File(..., description="DarkWeb source export (JSON or CSV).")],
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
    _ensure_mutable(eng)
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
            result = darkweb_import.parse_dehashed_json(raw, source_attribution=attribution)
        elif filename_lower.endswith(".csv"):
            result = darkweb_import.parse_dehashed_csv(raw, source_attribution=attribution)
        else:
            # No clear suffix — try JSON first (Dehashed API responses
            # tend to come as raw JSON with no extension), fall back to CSV.
            try:
                result = darkweb_import.parse_dehashed_json(raw, source_attribution=attribution)
            except ValueError:
                result = darkweb_import.parse_dehashed_csv(raw, source_attribution=attribution)
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
        entities=_entities_to_read(session, fresh),
    )


@router.get(
    "/engagements/{slug}/entities/stored",
    response_model=list[StoredEntityRead],
)
def list_stored_entities_endpoint(
    slug: str,
    session: DbSession,
    type: Annotated[str | None, Query(description="Filter by type.")] = None,
    q: Annotated[str | None, Query(description="Substring match on the value.")] = None,
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
    return _entities_to_read(session, rows)
