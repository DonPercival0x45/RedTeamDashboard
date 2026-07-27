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

import hashlib
import json
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentNonGuestUser, CurrentUser, DbSession
from app.models import (
    ActorType,
    AuditLog,
    Engagement,
    EngagementStatus,
    EngagementWorkState,
    Entity,
    EntityFindingLink,
    EntityGroup,
    EntityGroupMember,
    EntityReview,
    EntityReviewDisposition,
    EntityReviewFindingLink,
    EntityReviewScopeLink,
    Finding,
    FindingExclusion,
    FindingPhase,
    FindingStatus,
    ScopeItem,
    ScopeKind,
    Severity,
)
from app.services import darkweb_import, entity_store
from app.services.entities import extract_finding_context
from app.services.entity_identity import entity_identity_key, normalize_entity_value
from app.services.entity_review import (
    ENTITY_REVIEW_SCOPE_SOURCE,
    MAX_REVIEW_TARGETS,
    ReviewTarget,
    build_review_impact,
    scope_target,
)
from app.services.findings import (
    get_active_finding_or_404,
    lock_active_finding_or_404,
)
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


class StoredEntityGroupRef(BaseModel):
    id: uuid.UUID
    canonical_entity_id: uuid.UUID | None = None
    label: str | None = None
    member_count: int
    suppressed_member_count: int
    row_version: int


class StoredEntityRead(BaseModel):
    id: uuid.UUID
    type: str
    value: str
    normalized_value: str
    properties: dict[str, Any] = Field(default_factory=dict)
    source_tool: str
    source_attribution: str | None = None
    finding_refs: list[StoredEntityFindingRef] = Field(default_factory=list)
    group: StoredEntityGroupRef | None = None
    suppressed: bool = False
    suppression_reason: str | None = None
    row_version: int
    created_at: Any
    updated_at: Any


class DuplicateEntityRef(BaseModel):
    id: uuid.UUID
    type: str
    value: str
    source_tool: str
    source_attribution: str | None = None
    finding_count: int = 0


class EntityDuplicateCandidate(BaseModel):
    type: str
    normalized_value: str
    suggested_canonical_entity_id: uuid.UUID
    entities: list[DuplicateEntityRef]


class EntityGroupCreate(BaseModel):
    entity_ids: list[uuid.UUID] = Field(min_length=2, max_length=100)
    canonical_entity_id: uuid.UUID | None = None
    label: str | None = Field(default=None, max_length=300)
    reason: str = Field(min_length=1, max_length=2000)

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("reason must not be blank")
        return value.strip()


class EntityGroupRead(BaseModel):
    id: uuid.UUID
    engagement_id: uuid.UUID
    canonical_entity_id: uuid.UUID | None = None
    label: str | None = None
    reason: str
    entity_ids: list[uuid.UUID]
    row_version: int
    created_at: Any
    updated_at: Any


class EntityReviewTargetRequest(BaseModel):
    type: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=500)


class EntityReviewPreviewRequest(BaseModel):
    targets: list[EntityReviewTargetRequest] = Field(min_length=1, max_length=MAX_REVIEW_TARGETS)
    action: Literal["keep", "exclude"]
    cascade: bool = False


class EntityReviewImpactItem(BaseModel):
    type: str
    value: str
    depth: int
    reason: str
    scope_kind: ScopeKind | None
    exact_include_ids: list[uuid.UUID] = Field(default_factory=list)
    exact_exclusion_ids: list[uuid.UUID] = Field(default_factory=list)
    managed_exclusion_ids: list[uuid.UUID] = Field(default_factory=list)


class EntityReviewFindingImpactItem(BaseModel):
    id: uuid.UUID
    title: str
    target: str | None
    source_tool: str | None
    current_exclusion: FindingExclusion | None
    parent_type: str
    parent_value: str
    depth: int


class EntityReviewPreviewRead(BaseModel):
    action: Literal["keep", "exclude"]
    cascade: bool
    preview_sha256: str
    entities: list[EntityReviewImpactItem]
    findings: list[EntityReviewFindingImpactItem]
    finding_ids: list[uuid.UUID]
    exact_include_conflicts: int
    includes_to_create: int
    exclusions_to_create: int
    managed_exclusions_to_remove: int
    findings_to_mark_out_of_scope: int
    findings_to_restore: int
    truncated: bool


class EntityReviewApplyRequest(EntityReviewPreviewRequest):
    reason: str = Field(min_length=1, max_length=2000)
    preview_sha256: str = Field(min_length=64, max_length=64)
    remove_conflicting_exact_includes: bool = False

    @field_validator("reason")
    @classmethod
    def validate_review_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("reason must not be blank")
        return value.strip()


class EntityReviewApplyRead(BaseModel):
    reviewed: int
    includes_created: int
    exclusions_created: int
    exact_includes_removed: int
    managed_exclusions_removed: int
    findings_marked_out_of_scope: int
    findings_restored: int
    preview_sha256: str


class EntityDispositionRequest(BaseModel):
    expected_row_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=2000)

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("reason must not be blank")
        return value.strip()


class EntityGroupMergeDeleteResult(BaseModel):
    status: str
    group_id: uuid.UUID
    canonical_entity_id: uuid.UUID
    suppressed_entity_ids: list[uuid.UUID]
    transferred_link_count: int
    merged_property_keys: list[str]
    canonical_entity: StoredEntityRead


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
    entity_suppressed: bool = False
    duplicate_entity_ids: list[uuid.UUID] = Field(default_factory=list)
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
    if eng is None or eng.status == EngagementStatus.flushed:
        raise HTTPException(status_code=404, detail=f"engagement '{slug}' not found")
    return eng


def _mutable_engagement(session, engagement_id: uuid.UUID) -> Engagement:
    engagement = session.execute(
        select(Engagement).where(Engagement.id == engagement_id).with_for_update()
    ).scalar_one_or_none()
    if engagement is None or engagement.status == EngagementStatus.flushed:
        raise HTTPException(status_code=404, detail="engagement not found")
    if engagement.status == EngagementStatus.archived:
        raise HTTPException(status_code=409, detail="archived engagement is read-only")
    if engagement.work_state == EngagementWorkState.completed:
        raise HTTPException(status_code=409, detail="completed engagement is read-only")
    return engagement


def _finding_or_404(session, finding_id: uuid.UUID) -> Finding:
    return get_active_finding_or_404(session, finding_id)


def _locked_finding_for_mutation(session, finding_id: uuid.UUID) -> Finding:
    finding = get_active_finding_or_404(session, finding_id)
    _mutable_engagement(session, finding.engagement_id)
    return lock_active_finding_or_404(session, finding_id)


def _group_read(session, group: EntityGroup) -> EntityGroupRead:
    entity_ids = list(
        session.execute(
            select(EntityGroupMember.entity_id)
            .where(EntityGroupMember.group_id == group.id)
            .order_by(EntityGroupMember.created_at, EntityGroupMember.entity_id)
        ).scalars()
    )
    return EntityGroupRead(
        id=group.id,
        engagement_id=group.engagement_id,
        canonical_entity_id=group.canonical_entity_id,
        label=group.label,
        reason=group.reason,
        entity_ids=entity_ids,
        row_version=group.row_version,
        created_at=group.created_at,
        updated_at=group.updated_at,
    )


def _context_candidates(session, finding: Finding) -> list[FindingContextCandidate]:
    extracted = extract_finding_context(finding)
    if not extracted:
        return []
    scope = list(
        session.execute(
            select(ScopeItem).where(ScopeItem.engagement_id == finding.engagement_id)
        ).scalars()
    )
    scope_map = {(row.kind.value, row.value): row for row in scope}
    compatible = {kind.value for kind in ScopeKind}
    candidates: list[FindingContextCandidate] = []
    for kind, value in extracted:
        duplicate_ids: list[uuid.UUID] = []
        try:
            entity, _, _ = entity_store.find_semantic_entity(
                session,
                engagement_id=finding.engagement_id,
                entity_type=kind,
                value=value,
            )
        except entity_store.EntityIdentityConflict as exc:
            entity = None
            duplicate_ids = [uuid.UUID(item) for item in exc.entity_ids]
        scope_item = scope_map.get((kind, value))
        candidates.append(
            FindingContextCandidate(
                type=kind,
                value=value,
                entity_id=entity.id if entity else None,
                entity_suppressed=bool(entity and entity.suppressed_at),
                duplicate_entity_ids=duplicate_ids,
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

    group_rows = session.execute(
        select(
            EntityGroupMember.entity_id,
            EntityGroup.id,
            EntityGroup.canonical_entity_id,
            EntityGroup.label,
            EntityGroup.row_version,
        )
        .join(EntityGroup, EntityGroup.id == EntityGroupMember.group_id)
        .where(EntityGroupMember.entity_id.in_(refs_by_entity))
    ).all()
    group_ids = {group_id for _, group_id, _, _, _ in group_rows}
    count_rows = (
        session.execute(
            select(
                EntityGroupMember.group_id,
                func.count(EntityGroupMember.entity_id),
                func.count(EntityGroupMember.entity_id).filter(Entity.suppressed_at.is_not(None)),
            )
            .join(Entity, Entity.id == EntityGroupMember.entity_id)
            .where(EntityGroupMember.group_id.in_(group_ids))
            .group_by(EntityGroupMember.group_id)
        ).all()
        if group_ids
        else []
    )
    member_counts = {group_id: count for group_id, count, _ in count_rows}
    suppressed_counts = {group_id: count for group_id, _, count in count_rows}
    group_by_entity = {
        entity_id: StoredEntityGroupRef(
            id=group_id,
            canonical_entity_id=canonical_id,
            label=label,
            member_count=int(member_counts.get(group_id, 0)),
            suppressed_member_count=int(suppressed_counts.get(group_id, 0)),
            row_version=row_version,
        )
        for entity_id, group_id, canonical_id, label, row_version in group_rows
    }
    return [
        StoredEntityRead(
            id=entity.id,
            type=entity.type,
            value=entity.value,
            normalized_value=normalize_entity_value(entity.type, entity.value),
            properties=dict(entity.properties or {}),
            source_tool=entity.source_tool,
            source_attribution=entity.source_attribution,
            finding_refs=refs_by_entity[entity.id],
            group=group_by_entity.get(entity.id),
            suppressed=entity.suppressed_at is not None,
            suppression_reason=entity.suppression_reason,
            row_version=entity.row_version,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )
        for entity in entities
    ]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


def _review_impact(
    session: DbSession,
    engagement: Engagement,
    body: EntityReviewPreviewRequest,
    *,
    lock: bool = False,
):
    finding_query = select(Finding).where(
        Finding.engagement_id == engagement.id,
        Finding.deleted_at.is_(None),
    )
    scope_query = select(ScopeItem).where(ScopeItem.engagement_id == engagement.id)
    review_query = select(EntityReview).where(EntityReview.engagement_id == engagement.id)
    finding_link_query = (
        select(EntityReviewFindingLink)
        .join(EntityReview, EntityReview.id == EntityReviewFindingLink.entity_review_id)
        .where(
            EntityReview.engagement_id == engagement.id,
            EntityReviewFindingLink.released_at.is_(None),
        )
    )
    scope_link_query = (
        select(EntityReviewScopeLink)
        .join(EntityReview, EntityReview.id == EntityReviewScopeLink.entity_review_id)
        .where(
            EntityReview.engagement_id == engagement.id,
            EntityReviewScopeLink.released_at.is_(None),
        )
    )
    if lock:
        finding_query = finding_query.with_for_update()
        scope_query = scope_query.with_for_update()
        review_query = review_query.with_for_update()
        finding_link_query = finding_link_query.with_for_update(of=EntityReviewFindingLink)
        scope_link_query = scope_link_query.with_for_update(of=EntityReviewScopeLink)
    findings = list(session.execute(finding_query).scalars())
    scope_items = list(session.execute(scope_query).scalars())
    reviews = list(session.execute(review_query).scalars())
    impact = build_review_impact(
        findings=findings,
        scope_items=scope_items,
        targets=[ReviewTarget(target.type, target.value) for target in body.targets],
        action=body.action,
        cascade=body.cascade,
    )
    affected_keys = {entity_identity_key(row.entity_type, row.value) for row in impact.entities}
    reviews_by_key = {
        (row.entity_type, row.normalized_value): row
        for row in reviews
        if (row.entity_type, row.normalized_value) in affected_keys
    }
    affected_review_ids = {row.id for row in reviews_by_key.values()}
    active_links = [
        row
        for row in session.execute(finding_link_query).scalars()
        if row.entity_review_id in affected_review_ids
    ]
    all_scope_links = list(session.execute(scope_link_query).scalars())
    active_scope_links = [
        row for row in all_scope_links if row.entity_review_id in affected_review_ids
    ]
    findings_by_id = {row.id: row for row in findings}
    review_state = [
        {
            "id": str(row.id),
            "type": row.entity_type,
            "value": row.normalized_value,
            "disposition": row.disposition.value,
            "row_version": row.row_version,
        }
        for row in sorted(reviews_by_key.values(), key=lambda value: str(value.id))
    ]
    link_state = [
        {
            "id": str(row.id),
            "review_id": str(row.entity_review_id),
            "finding_id": str(row.finding_id),
            "previous_exclusion": row.previous_exclusion,
            "current_exclusion": (
                findings_by_id[row.finding_id].exclusion.value
                if row.finding_id in findings_by_id and findings_by_id[row.finding_id].exclusion
                else None
            ),
        }
        for row in sorted(active_links, key=lambda value: str(value.id))
    ]
    impacted_scope_ids = {
        uuid.UUID(scope_id) for row in impact.entities for scope_id in row.managed_exclusion_ids
    } | {row.scope_item_id for row in active_scope_links}
    relevant_scope_links = [
        row for row in all_scope_links if row.scope_item_id in impacted_scope_ids
    ]
    scope_link_state = [
        {
            "id": str(row.id),
            "review_id": str(row.entity_review_id),
            "scope_item_id": str(row.scope_item_id),
        }
        for row in sorted(relevant_scope_links, key=lambda value: str(value.id))
    ]
    state_digest = hashlib.sha256(
        json.dumps(
            {
                "impact": impact.digest,
                "reviews": review_state,
                "active_finding_links": link_state,
                "active_scope_links": scope_link_state,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return (
        replace(impact, digest=state_digest),
        findings,
        scope_items,
        reviews_by_key,
        active_links,
        active_scope_links,
        all_scope_links,
    )


def _review_preview_read(
    body: EntityReviewPreviewRequest,
    impact,
    findings: list[Finding],
    reviews_by_key: dict[tuple[str, str], EntityReview],
    active_links: list[EntityReviewFindingLink],
    active_scope_links: list[EntityReviewScopeLink],
    all_scope_links: list[EntityReviewScopeLink],
) -> EntityReviewPreviewRead:
    conflicts = (
        sum(len(row.exact_include_ids) for row in impact.entities)
        if body.action == "exclude"
        else 0
    )
    includes_to_create = len(
        {
            target
            for row in impact.entities
            if body.action == "keep"
            and not row.exact_include_ids
            and (target := scope_target(row.entity_type, row.value)) is not None
        }
    )
    exclusions_to_create = len(
        {
            target
            for row in impact.entities
            if body.action == "exclude"
            and not row.exact_exclusion_ids
            and (target := scope_target(row.entity_type, row.value)) is not None
        }
    )
    affected_review_ids = {row.id for row in reviews_by_key.values()}
    affected_scope_ids = {
        uuid.UUID(scope_id) for row in impact.entities for scope_id in row.managed_exclusion_ids
    }
    scope_ids_with_other_owners = {
        row.scope_item_id
        for row in all_scope_links
        if row.entity_review_id not in affected_review_ids
    }
    managed_to_remove = (
        len(affected_scope_ids - scope_ids_with_other_owners) if body.action == "keep" else 0
    )
    findings_by_id = {str(row.id): row for row in findings}
    review_by_id = {row.id: row for row in reviews_by_key.values()}
    finding_items: dict[str, EntityReviewFindingImpactItem] = {
        row.finding_id: EntityReviewFindingImpactItem(
            id=uuid.UUID(row.finding_id),
            title=row.title,
            target=row.target,
            source_tool=row.source_tool,
            current_exclusion=(
                FindingExclusion(row.current_exclusion) if row.current_exclusion else None
            ),
            parent_type=row.parent_type,
            parent_value=row.parent_value,
            depth=row.depth,
        )
        for row in impact.findings
    }
    for link in active_links:
        finding = findings_by_id.get(str(link.finding_id))
        review = review_by_id.get(link.entity_review_id)
        if finding is None or review is None or str(finding.id) in finding_items:
            continue
        finding_items[str(finding.id)] = EntityReviewFindingImpactItem(
            id=finding.id,
            title=finding.title,
            target=finding.target,
            source_tool=finding.source_tool,
            current_exclusion=finding.exclusion,
            parent_type=review.entity_type,
            parent_value=review.display_value,
            depth=0,
        )
    findings_to_mark = sum(
        1 for row in impact.findings if body.action == "exclude" and row.current_exclusion is None
    )
    findings_to_restore = sum(
        1
        for link in active_links
        if body.action == "keep"
        and (finding := findings_by_id.get(str(link.finding_id))) is not None
        and finding.exclusion is FindingExclusion.out_of_scope
    )
    return EntityReviewPreviewRead(
        action=body.action,
        cascade=body.cascade,
        preview_sha256=impact.digest,
        entities=[
            EntityReviewImpactItem(
                type=row.entity_type,
                value=row.value,
                depth=row.depth,
                reason=row.reason,
                scope_kind=row.scope_kind,
                exact_include_ids=[uuid.UUID(value) for value in row.exact_include_ids],
                exact_exclusion_ids=[uuid.UUID(value) for value in row.exact_exclusion_ids],
                managed_exclusion_ids=[uuid.UUID(value) for value in row.managed_exclusion_ids],
            )
            for row in impact.entities
        ],
        findings=list(finding_items.values()),
        finding_ids=[row.id for row in finding_items.values()],
        exact_include_conflicts=conflicts,
        includes_to_create=includes_to_create,
        exclusions_to_create=exclusions_to_create,
        managed_exclusions_to_remove=managed_to_remove,
        findings_to_mark_out_of_scope=findings_to_mark,
        findings_to_restore=findings_to_restore,
        truncated=impact.truncated,
    )


@router.post(
    "/engagements/{slug}/entity-reviews/preview",
    response_model=EntityReviewPreviewRead,
)
def preview_entity_review(
    slug: str,
    body: EntityReviewPreviewRequest,
    session: DbSession,
    _user: CurrentUser,
) -> EntityReviewPreviewRead:
    engagement = _engagement_by_slug(session, slug)
    (
        impact,
        findings,
        _,
        reviews_by_key,
        active_links,
        active_scope_links,
        all_scope_links,
    ) = _review_impact(session, engagement, body)
    return _review_preview_read(
        body,
        impact,
        findings,
        reviews_by_key,
        active_links,
        active_scope_links,
        all_scope_links,
    )


@router.post(
    "/engagements/{slug}/entity-reviews/apply",
    response_model=EntityReviewApplyRead,
)
def apply_entity_review(
    slug: str,
    body: EntityReviewApplyRequest,
    session: DbSession,
    user: CurrentNonGuestUser,
) -> EntityReviewApplyRead:
    engagement = _engagement_by_slug(session, slug)
    engagement = _mutable_engagement(session, engagement.id)
    (
        impact,
        findings,
        scope_items,
        reviews_by_key,
        active_links,
        active_scope_links,
        all_scope_links,
    ) = _review_impact(session, engagement, body, lock=True)
    if impact.digest != body.preview_sha256:
        raise HTTPException(
            status_code=409,
            detail="entity review impact changed; preview again before applying",
        )
    if impact.truncated:
        raise HTTPException(
            status_code=409,
            detail=(
                "cascade exceeds the 500-discovery safety bound; "
                "narrow the selection before applying"
            ),
        )
    # Exact includes remain as durable, dormant authorization history. The new
    # exact exclusion takes precedence in scope matching; releasing the managed
    # exclusion later restores the prior include without reconstructing it.
    scope_by_id = {row.id: row for row in scope_items}
    includes_removed = 0
    removed_include_ids: list[str] = []

    managed_removed = 0
    removed_managed_ids: list[str] = []
    includes_created = 0
    created_include_ids: list[str] = []
    exclusions_created = 0
    created_exclusion_ids: list[str] = []
    disposition = (
        EntityReviewDisposition.excluded
        if body.action == "exclude"
        else EntityReviewDisposition.kept
    )
    for row in impact.entities:
        key = ReviewTarget(row.entity_type, row.value).key
        review = reviews_by_key.get(key)
        if review is None:
            review = EntityReview(
                engagement_id=engagement.id,
                entity_type=key[0],
                normalized_value=key[1],
                display_value=row.value,
                disposition=disposition,
                reason=body.reason,
                reviewed_by_user_id=user.id,
            )
            session.add(review)
            reviews_by_key[key] = review
        else:
            review.display_value = row.value
            review.disposition = disposition
            review.reason = body.reason
            review.reviewed_by_user_id = user.id
            review.row_version += 1

    session.flush()

    active_scope_link_keys = {(row.entity_review_id, row.scope_item_id) for row in all_scope_links}
    managed_scope_by_target = {
        (row.kind, row.value): row
        for row in scope_items
        if row.is_exclusion and row.source == ENTITY_REVIEW_SCOPE_SOURCE
    }
    if body.action == "exclude":
        for row in impact.entities:
            target = scope_target(row.entity_type, row.value)
            review = reviews_by_key.get(ReviewTarget(row.entity_type, row.value).key)
            if target is None or review is None:
                continue
            external_exclusion_exists = any(
                scope_by_id.get(uuid.UUID(scope_id)) is not None
                and scope_by_id[uuid.UUID(scope_id)].source != ENTITY_REVIEW_SCOPE_SOURCE
                for scope_id in row.exact_exclusion_ids
            )
            if external_exclusion_exists:
                continue
            exclusion = managed_scope_by_target.get(target)
            if exclusion is None:
                exclusion = ScopeItem(
                    engagement_id=engagement.id,
                    kind=target[0],
                    value=target[1],
                    is_exclusion=True,
                    note=f"Entity review: {body.reason}"[:500],
                    source=ENTITY_REVIEW_SCOPE_SOURCE,
                )
                session.add(exclusion)
                session.flush()
                managed_scope_by_target[target] = exclusion
                scope_by_id[exclusion.id] = exclusion
                created_exclusion_ids.append(str(exclusion.id))
                session.add(
                    AuditLog(
                        engagement_id=engagement.id,
                        actor_type=ActorType.user,
                        actor_id=str(user.id),
                        event_type="scope.item.created",
                        payload={
                            "scope_id": str(exclusion.id),
                            "kind": exclusion.kind.value,
                            "value": exclusion.value,
                            "is_exclusion": True,
                            "source": exclusion.source,
                            "reason": body.reason,
                        },
                    )
                )
                exclusions_created += 1
            link_key = (review.id, exclusion.id)
            if link_key not in active_scope_link_keys:
                session.add(
                    EntityReviewScopeLink(
                        entity_review_id=review.id,
                        scope_item_id=exclusion.id,
                    )
                )
                active_scope_link_keys.add(link_key)
    else:
        now = datetime.now(UTC)
        affected_review_ids = {row.id for row in reviews_by_key.values()}
        scope_ids_with_other_owners = {
            row.scope_item_id
            for row in all_scope_links
            if row.entity_review_id not in affected_review_ids
        }
        for link in active_scope_links:
            linked_item = scope_by_id.get(link.scope_item_id)
            if linked_item is None or not linked_item.is_exclusion:
                continue
            link.released_at = now
            link.released_by_user_id = user.id
            link.release_reason = body.reason
        for scope_id in sorted(
            {row.scope_item_id for row in active_scope_links} - scope_ids_with_other_owners,
            key=str,
        ):
            item = scope_by_id.get(scope_id)
            if item is None or not item.is_exclusion or item.source != ENTITY_REVIEW_SCOPE_SOURCE:
                continue
            session.add(
                AuditLog(
                    engagement_id=engagement.id,
                    actor_type=ActorType.user,
                    actor_id=str(user.id),
                    event_type="scope.item.deleted",
                    payload={
                        "scope_id": str(item.id),
                        "kind": item.kind.value,
                        "value": item.value,
                        "is_exclusion": True,
                        "source": item.source,
                        "reason": body.reason,
                    },
                )
            )
            session.delete(item)
            removed_managed_ids.append(str(item.id))
            managed_removed += 1

        exact_includes_by_target = {
            (row.kind, row.value): row for row in scope_items if not row.is_exclusion
        }
        for row in impact.entities:
            target = scope_target(row.entity_type, row.value)
            review = reviews_by_key.get(ReviewTarget(row.entity_type, row.value).key)
            if target is None or review is None or target in exact_includes_by_target:
                continue
            include = ScopeItem(
                engagement_id=engagement.id,
                kind=target[0],
                value=target[1],
                is_exclusion=False,
                note=f"Entity review keep: {body.reason}"[:500],
                source="entity_review_keep",
            )
            session.add(include)
            session.flush()
            exact_includes_by_target[target] = include
            scope_by_id[include.id] = include
            created_include_ids.append(str(include.id))
            includes_created += 1
            session.add(
                EntityReviewScopeLink(
                    entity_review_id=review.id,
                    scope_item_id=include.id,
                )
            )
            session.add(
                AuditLog(
                    engagement_id=engagement.id,
                    actor_type=ActorType.user,
                    actor_id=str(user.id),
                    event_type="scope.item.created",
                    payload={
                        "scope_id": str(include.id),
                        "kind": include.kind.value,
                        "value": include.value,
                        "is_exclusion": False,
                        "source": include.source,
                        "reason": body.reason,
                    },
                )
            )

    findings_by_id = {str(row.id): row for row in findings}
    active_link_keys = {(row.entity_review_id, row.finding_id) for row in active_links}
    findings_marked = 0
    marked_finding_ids: list[str] = []
    findings_restored = 0
    restored_finding_ids: list[str] = []
    if body.action == "exclude":
        for impact_finding in impact.findings:
            finding = findings_by_id.get(impact_finding.finding_id)
            review = reviews_by_key.get(
                entity_identity_key(impact_finding.parent_type, impact_finding.parent_value)
            )
            if finding is None or review is None:
                continue
            if finding.exclusion is FindingExclusion.outside_roe:
                continue
            link_key = (review.id, finding.id)
            if finding.exclusion is None:
                if link_key not in active_link_keys:
                    session.add(
                        EntityReviewFindingLink(
                            entity_review_id=review.id,
                            finding_id=finding.id,
                            previous_exclusion=None,
                        )
                    )
                    active_link_keys.add(link_key)
                finding.exclusion = FindingExclusion.out_of_scope
                findings_marked += 1
                marked_finding_ids.append(str(finding.id))
    else:
        now = datetime.now(UTC)
        for link in active_links:
            finding = findings_by_id.get(str(link.finding_id))
            if finding is not None and finding.exclusion is FindingExclusion.out_of_scope:
                finding.exclusion = (
                    FindingExclusion(link.previous_exclusion) if link.previous_exclusion else None
                )
                findings_restored += 1
                restored_finding_ids.append(str(finding.id))
            link.released_at = now
            link.released_by_user_id = user.id
            link.release_reason = body.reason

    entity_identities = [
        {
            "type": row.entity_type,
            "value": row.value,
            "depth": row.depth,
        }
        for row in impact.entities
    ]
    session.add(
        AuditLog(
            engagement_id=engagement.id,
            actor_type=ActorType.user,
            actor_id=str(user.id),
            event_type="entity.review.applied",
            payload={
                "action": body.action,
                "cascade": body.cascade,
                "roots": [target.model_dump() for target in body.targets],
                "entities": entity_identities,
                "finding_ids_marked_out_of_scope": marked_finding_ids,
                "finding_ids_restored": restored_finding_ids,
                "scope_include_ids_created": created_include_ids,
                "scope_exclusion_ids_created": created_exclusion_ids,
                "scope_exclusion_ids_removed": removed_managed_ids,
                "scope_include_ids_removed": removed_include_ids,
                "preview_sha256": impact.digest,
                "reason": body.reason,
            },
        )
    )
    session.commit()
    return EntityReviewApplyRead(
        reviewed=len(impact.entities),
        includes_created=includes_created,
        exclusions_created=exclusions_created,
        exact_includes_removed=includes_removed,
        managed_exclusions_removed=managed_removed,
        findings_marked_out_of_scope=findings_marked,
        findings_restored=findings_restored,
        preview_sha256=impact.digest,
    )


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
    finding = _locked_finding_for_mutation(session, finding_id)

    entities_created = 0
    links_created = 0
    scope_created = 0
    promoted: list[dict[str, Any]] = []
    scope_kinds = {kind.value: kind for kind in ScopeKind}

    for requested in body.items:
        kind = requested.type.strip().lower()
        value = normalize_entity_value(kind, requested.value)
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
            try:
                entity, kind, normalized_value = entity_store.find_semantic_entity(
                    session,
                    engagement_id=finding.engagement_id,
                    entity_type=kind,
                    value=value,
                )
            except entity_store.EntityIdentityConflict as exc:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "message": str(exc),
                        "entity_ids": exc.entity_ids,
                    },
                ) from exc
            value = normalized_value
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
    eng = _mutable_engagement(session, eng.id)
    raw = file.file.read()
    attribution = file.filename or "maltego.mtgx"
    try:
        result = parse_mtgx(raw, source_attribution=attribution)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        inserted, merged = entity_store.persist_entities(
            session,
            engagement=eng,
            items=result.items,
            source_tool="maltego_import",
            source_attribution=attribution,
        )
    except entity_store.EntityIdentityConflict as exc:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail={"message": str(exc), "entity_ids": exc.entity_ids},
        ) from exc

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
    eng = _mutable_engagement(session, eng.id)
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

    try:
        inserted, merged = entity_store.persist_entities(
            session,
            engagement=eng,
            items=result.items,
            source_tool=f"{source}_import",
            source_attribution=attribution,
        )
    except entity_store.EntityIdentityConflict as exc:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail={"message": str(exc), "entity_ids": exc.entity_ids},
        ) from exc

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
    _user: CurrentUser,
    type: Annotated[str | None, Query(description="Filter by type.")] = None,
    q: Annotated[str | None, Query(description="Substring match on the value.")] = None,
    include_suppressed: Annotated[
        bool, Query(description="Include analyst-suppressed stored records.")
    ] = False,
) -> list[StoredEntityRead]:
    """Stored entities for the engagement (Maltego imports + future sources).

    Distinct from ``GET /engagements/{slug}/entities`` which derives
    entities from findings on the fly. The UI Entities tab shows both
    layers as separate sections.
    """
    eng = _engagement_by_slug(session, slug)
    rows = entity_store.list_stored_entities(
        session,
        engagement=eng,
        type_filter=type,
        query=q,
        include_suppressed=include_suppressed,
    )
    return _entities_to_read(session, rows)


@router.get(
    "/engagements/{slug}/entities/duplicate-candidates",
    response_model=list[EntityDuplicateCandidate],
)
def list_entity_duplicate_candidates(
    slug: str,
    session: DbSession,
    _user: CurrentUser,
) -> list[EntityDuplicateCandidate]:
    """Conservative semantic duplicate candidates for ungrouped stored rows."""
    engagement = _engagement_by_slug(session, slug)
    entities = entity_store.list_stored_entities(session, engagement=engagement)
    if not entities:
        return []
    grouped_ids = set(
        session.execute(
            select(EntityGroupMember.entity_id).where(
                EntityGroupMember.entity_id.in_([row.id for row in entities])
            )
        ).scalars()
    )
    counts = dict(
        session.execute(
            select(EntityFindingLink.entity_id, func.count(EntityFindingLink.id))
            .where(EntityFindingLink.entity_id.in_([row.id for row in entities]))
            .group_by(EntityFindingLink.entity_id)
        ).all()
    )
    buckets: dict[tuple[str, str], list[Entity]] = {}
    for entity in entities:
        if entity.id in grouped_ids:
            continue
        buckets.setdefault(entity_identity_key(entity.type, entity.value), []).append(entity)

    candidates: list[EntityDuplicateCandidate] = []
    for (entity_type, normalized_value), rows in sorted(buckets.items()):
        if len(rows) < 2:
            continue
        ordered = sorted(rows, key=lambda row: (row.created_at, str(row.id)))
        candidates.append(
            EntityDuplicateCandidate(
                type=entity_type,
                normalized_value=normalized_value,
                suggested_canonical_entity_id=ordered[0].id,
                entities=[
                    DuplicateEntityRef(
                        id=row.id,
                        type=row.type,
                        value=row.value,
                        source_tool=row.source_tool,
                        source_attribution=row.source_attribution,
                        finding_count=int(counts.get(row.id, 0)),
                    )
                    for row in ordered
                ],
            )
        )
    return candidates


@router.post(
    "/engagements/{slug}/entity-groups",
    response_model=EntityGroupRead,
    status_code=status.HTTP_201_CREATED,
)
def create_entity_group(
    slug: str,
    body: EntityGroupCreate,
    session: DbSession,
    user: CurrentNonGuestUser,
) -> EntityGroupRead:
    initial = _engagement_by_slug(session, slug)
    engagement = _mutable_engagement(session, initial.id)
    entity_ids = list(dict.fromkeys(body.entity_ids))
    if len(entity_ids) < 2:
        raise HTTPException(status_code=422, detail="a duplicate group requires two entities")
    entities = list(
        session.execute(
            select(Entity).where(Entity.id.in_(entity_ids)).order_by(Entity.id).with_for_update()
        ).scalars()
    )
    if len(entities) != len(entity_ids) or any(
        row.engagement_id != engagement.id for row in entities
    ):
        raise HTTPException(status_code=422, detail="entities must belong to this engagement")
    if any(row.suppressed_at is not None for row in entities):
        raise HTTPException(status_code=409, detail="restore suppressed entities before grouping")
    keys = {entity_identity_key(row.type, row.value) for row in entities}
    if len(keys) != 1:
        raise HTTPException(
            status_code=422,
            detail="entities do not share the same conservative normalized identity",
        )
    already_grouped = session.execute(
        select(EntityGroupMember.entity_id).where(EntityGroupMember.entity_id.in_(entity_ids))
    ).first()
    if already_grouped:
        raise HTTPException(status_code=409, detail="an entity is already in a duplicate group")
    canonical_id = (
        body.canonical_entity_id or min(entities, key=lambda row: (row.created_at, str(row.id))).id
    )
    if canonical_id not in entity_ids:
        raise HTTPException(status_code=422, detail="canonical entity must be a group member")

    group = EntityGroup(
        engagement_id=engagement.id,
        canonical_entity_id=canonical_id,
        label=body.label,
        reason=body.reason.strip(),
        created_by_user_id=user.id,
    )
    session.add(group)
    session.flush()
    session.add_all(
        EntityGroupMember(
            group_id=group.id,
            entity_id=entity_id,
            added_by_user_id=user.id,
        )
        for entity_id in entity_ids
    )
    session.add(
        AuditLog(
            engagement_id=engagement.id,
            actor_type=ActorType.user,
            actor_id=str(user.id),
            event_type="entities.grouped",
            payload={
                "group_id": str(group.id),
                "entity_ids": [str(item) for item in entity_ids],
                "canonical_entity_id": str(canonical_id),
                "reason": body.reason.strip(),
            },
        )
    )
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail="an entity is already in a duplicate group; refresh and retry",
        ) from exc
    session.refresh(group)
    return _group_read(session, group)


@router.post(
    "/entity-groups/{group_id}/merge-delete",
    response_model=EntityGroupMergeDeleteResult,
)
def merge_delete_entity_group(
    group_id: uuid.UUID,
    body: EntityDispositionRequest,
    session: DbSession,
    user: CurrentNonGuestUser,
) -> EntityGroupMergeDeleteResult:
    """Merge duplicate-group provenance into canonical and hide duplicates.

    This is intentionally non-destructive: duplicate rows, raw source
    properties, and original finding links remain in the database, but
    non-canonical members are suppressed from active views. Canonical keeps
    analyst-selected identity and gains missing finding links/properties.
    """
    engagement_id = session.execute(
        select(EntityGroup.engagement_id).where(EntityGroup.id == group_id)
    ).scalar_one_or_none()
    if engagement_id is None:
        raise HTTPException(status_code=404, detail="entity group not found")
    engagement = _mutable_engagement(session, engagement_id)
    group = session.execute(
        select(EntityGroup)
        .where(EntityGroup.id == group_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if group is None or group.engagement_id != engagement.id:
        raise HTTPException(status_code=404, detail="entity group not found")
    if group.row_version != body.expected_row_version:
        raise HTTPException(status_code=409, detail="entity group changed; refresh and retry")
    if group.canonical_entity_id is None:
        raise HTTPException(status_code=409, detail="duplicate group has no canonical entity")

    members = list(
        session.execute(
            select(Entity)
            .join(EntityGroupMember, EntityGroupMember.entity_id == Entity.id)
            .where(EntityGroupMember.group_id == group.id)
            .order_by(Entity.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalars()
    )
    canonical = next((row for row in members if row.id == group.canonical_entity_id), None)
    if canonical is None:
        raise HTTPException(status_code=409, detail="canonical entity is not a group member")
    if canonical.suppressed_at is not None:
        raise HTTPException(status_code=409, detail="restore the canonical entity before merging")
    duplicates = [row for row in members if row.id != canonical.id]
    if not duplicates:
        raise HTTPException(status_code=409, detail="duplicate group has no duplicate members")
    active_duplicates = [row for row in duplicates if row.suppressed_at is None]
    if not active_duplicates:
        raise HTTPException(status_code=409, detail="duplicate members are already removed")

    now = datetime.now(tz=UTC)
    reason = body.reason.strip()
    canonical_properties = dict(canonical.properties or {})
    merged_property_keys: set[str] = set()
    for duplicate in duplicates:
        for key, value in dict(duplicate.properties or {}).items():
            if key not in canonical_properties:
                canonical_properties[key] = value
                merged_property_keys.add(key)
    canonical.properties = canonical_properties

    existing_finding_ids = set(
        session.execute(
            select(EntityFindingLink.finding_id).where(EntityFindingLink.entity_id == canonical.id)
        ).scalars()
    )
    duplicate_links = list(
        session.execute(
            select(EntityFindingLink.finding_id).where(
                EntityFindingLink.entity_id.in_([row.id for row in duplicates])
            )
        ).scalars()
    )
    transferred_link_count = 0
    for finding_id in sorted(set(duplicate_links), key=str):
        if finding_id in existing_finding_ids:
            continue
        session.add(
            EntityFindingLink(
                entity_id=canonical.id,
                finding_id=finding_id,
                created_by=user.id,
            )
        )
        existing_finding_ids.add(finding_id)
        transferred_link_count += 1

    suppressed_ids: list[uuid.UUID] = []
    for duplicate in active_duplicates:
        duplicate.suppressed_at = now
        duplicate.suppressed_by_user_id = user.id
        duplicate.suppression_reason = reason
        duplicate.row_version += 1
        suppressed_ids.append(duplicate.id)
    canonical.row_version += 1
    group.row_version += 1
    session.add(
        AuditLog(
            engagement_id=engagement.id,
            actor_type=ActorType.user,
            actor_id=str(user.id),
            event_type="entities.group_merged_deleted",
            payload={
                "group_id": str(group.id),
                "canonical_entity_id": str(canonical.id),
                "suppressed_entity_ids": [str(item) for item in suppressed_ids],
                "transferred_link_count": transferred_link_count,
                "merged_property_keys": sorted(merged_property_keys),
                "reason": reason,
            },
        )
    )
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail="entity provenance changed; refresh the group and retry",
        ) from exc
    session.refresh(canonical)
    session.refresh(group)
    return EntityGroupMergeDeleteResult(
        status="merged_deleted",
        group_id=group.id,
        canonical_entity_id=canonical.id,
        suppressed_entity_ids=suppressed_ids,
        transferred_link_count=transferred_link_count,
        merged_property_keys=sorted(merged_property_keys),
        canonical_entity=_entities_to_read(session, [canonical])[0],
    )


@router.post("/entity-groups/{group_id}/dissolve", response_model=dict[str, str])
def dissolve_entity_group(
    group_id: uuid.UUID,
    body: EntityDispositionRequest,
    session: DbSession,
    user: CurrentNonGuestUser,
) -> dict[str, str]:
    engagement_id = session.execute(
        select(EntityGroup.engagement_id).where(EntityGroup.id == group_id)
    ).scalar_one_or_none()
    if engagement_id is None:
        raise HTTPException(status_code=404, detail="entity group not found")
    engagement = _mutable_engagement(session, engagement_id)
    group = session.execute(
        select(EntityGroup)
        .where(EntityGroup.id == group_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if group is None or group.engagement_id != engagement.id:
        raise HTTPException(status_code=404, detail="entity group not found")
    if group.row_version != body.expected_row_version:
        raise HTTPException(status_code=409, detail="entity group changed; refresh and retry")
    member_ids = list(
        session.execute(
            select(EntityGroupMember.entity_id).where(EntityGroupMember.group_id == group.id)
        ).scalars()
    )
    session.delete(group)
    session.add(
        AuditLog(
            engagement_id=engagement.id,
            actor_type=ActorType.user,
            actor_id=str(user.id),
            event_type="entities.group_dissolved",
            payload={
                "group_id": str(group_id),
                "entity_ids": [str(item) for item in member_ids],
                "reason": body.reason.strip(),
            },
        )
    )
    session.commit()
    return {"status": "dissolved", "group_id": str(group_id)}


@router.post("/entities/{entity_id}/suppress", response_model=StoredEntityRead)
def suppress_entity(
    entity_id: uuid.UUID,
    body: EntityDispositionRequest,
    session: DbSession,
    user: CurrentNonGuestUser,
) -> StoredEntityRead:
    engagement_id = session.execute(
        select(Entity.engagement_id).where(Entity.id == entity_id)
    ).scalar_one_or_none()
    if engagement_id is None:
        raise HTTPException(status_code=404, detail="stored entity not found")
    engagement = _mutable_engagement(session, engagement_id)
    entity = session.execute(
        select(Entity)
        .where(Entity.id == entity_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if entity is None or entity.engagement_id != engagement.id:
        raise HTTPException(status_code=404, detail="stored entity not found")
    if entity.row_version != body.expected_row_version:
        raise HTTPException(status_code=409, detail="stored entity changed; refresh and retry")
    membership = session.execute(
        select(EntityGroupMember.group_id).where(EntityGroupMember.entity_id == entity.id)
    ).scalar_one_or_none()
    if membership is not None:
        raise HTTPException(status_code=409, detail="dissolve the duplicate group before removal")
    if entity.suppressed_at is None:
        entity.suppressed_at = datetime.now(tz=UTC)
        entity.suppressed_by_user_id = user.id
        entity.suppression_reason = body.reason.strip()
        entity.row_version += 1
        session.add(
            AuditLog(
                engagement_id=engagement.id,
                actor_type=ActorType.user,
                actor_id=str(user.id),
                event_type="entity.suppressed",
                payload={"entity_id": str(entity.id), "reason": body.reason.strip()},
            )
        )
        session.commit()
        session.refresh(entity)
    return _entities_to_read(session, [entity])[0]


@router.post("/entities/{entity_id}/restore", response_model=StoredEntityRead)
def restore_entity(
    entity_id: uuid.UUID,
    body: EntityDispositionRequest,
    session: DbSession,
    user: CurrentNonGuestUser,
) -> StoredEntityRead:
    engagement_id = session.execute(
        select(Entity.engagement_id).where(Entity.id == entity_id)
    ).scalar_one_or_none()
    if engagement_id is None:
        raise HTTPException(status_code=404, detail="stored entity not found")
    engagement = _mutable_engagement(session, engagement_id)
    entity = session.execute(
        select(Entity)
        .where(Entity.id == entity_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if entity is None or entity.engagement_id != engagement.id:
        raise HTTPException(status_code=404, detail="stored entity not found")
    if entity.row_version != body.expected_row_version:
        raise HTTPException(status_code=409, detail="stored entity changed; refresh and retry")
    if entity.suppressed_at is not None:
        prior_reason = entity.suppression_reason
        entity.suppressed_at = None
        entity.suppressed_by_user_id = None
        entity.suppression_reason = None
        entity.row_version += 1
        session.add(
            AuditLog(
                engagement_id=engagement.id,
                actor_type=ActorType.user,
                actor_id=str(user.id),
                event_type="entity.restored",
                payload={
                    "entity_id": str(entity.id),
                    "reason": body.reason.strip(),
                    "prior_suppression_reason": prior_reason,
                },
            )
        )
        session.commit()
        session.refresh(entity)
    return _entities_to_read(session, [entity])[0]
