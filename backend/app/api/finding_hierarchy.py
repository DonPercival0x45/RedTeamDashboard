"""Entity-centred Findings hierarchy and non-destructive item promotion."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import CurrentNonGuestUser, CurrentUser, DbSession, RedisClient
from app.models import (
    ActorType,
    AuditLog,
    Engagement,
    EngagementStatus,
    EngagementWorkState,
    Finding,
    FindingStatus,
    ScopeItem,
)
from app.schemas.finding_hierarchy import (
    FindingDuplicateCandidate,
    FindingFromHierarchyItemCreate,
    FindingFromHierarchyItemResponse,
    FindingHierarchyResponse,
)
from app.services.finding_hierarchy import (
    build_finding_hierarchy,
    find_hierarchy_item,
    hierarchy_duplicate_target_key,
    hierarchy_item_finding_refs,
    is_inventory_source_tool,
)

router = APIRouter(tags=["finding-hierarchy"])


def _engagement_or_404(session: DbSession, slug: str) -> Engagement:
    engagement = session.scalar(select(Engagement).where(Engagement.slug == slug))
    if engagement is None:
        raise HTTPException(status_code=404, detail="engagement not found")
    if engagement.status is EngagementStatus.flushed:
        raise HTTPException(status_code=409, detail="engagement has been flushed")
    return engagement


def _lock_writable_engagement(session: DbSession, slug: str) -> Engagement:
    engagement = session.scalar(select(Engagement).where(Engagement.slug == slug).with_for_update())
    if engagement is None:
        raise HTTPException(status_code=404, detail="engagement not found")
    if engagement.status is EngagementStatus.flushed:
        raise HTTPException(status_code=409, detail="engagement has been flushed")
    if engagement.status is EngagementStatus.archived:
        raise HTTPException(status_code=409, detail="archived engagement is read-only")
    if engagement.work_state is EngagementWorkState.completed:
        raise HTTPException(
            status_code=409,
            detail="completed engagement is read-only; reopen it before making changes",
        )
    return engagement


def _projection(session: DbSession, engagement: Engagement) -> FindingHierarchyResponse:
    findings = list(
        session.scalars(
            select(Finding)
            .where(
                Finding.engagement_id == engagement.id,
                Finding.deleted_at.is_(None),
            )
            .order_by(Finding.created_at.asc(), Finding.id.asc())
        )
    )
    scope_items = list(
        session.scalars(select(ScopeItem).where(ScopeItem.engagement_id == engagement.id))
    )
    return build_finding_hierarchy(
        engagement_id=engagement.id,
        findings=findings,
        scope_items=scope_items,
    )


@router.get(
    "/engagements/{slug}/findings/hierarchy",
    response_model=FindingHierarchyResponse,
)
def get_finding_hierarchy(
    slug: str,
    session: DbSession,
    _user: CurrentUser,
) -> FindingHierarchyResponse:
    """Project active Findings into IP/domain bundles without writing data."""
    return _projection(session, _engagement_or_404(session, slug))


def _candidate(finding: Finding, *, reason: str) -> FindingDuplicateCandidate:
    return FindingDuplicateCandidate(
        id=finding.id,
        title=finding.title,
        target=finding.target,
        severity=finding.severity,
        status=finding.status,
        exclusion=finding.exclusion,
        match_reason=reason,
    )


@router.post(
    "/engagements/{slug}/findings/from-item",
    response_model=FindingFromHierarchyItemResponse,
    status_code=status.HTTP_200_OK,
)
def create_finding_from_hierarchy_item(
    slug: str,
    body: FindingFromHierarchyItemCreate,
    session: DbSession,
    redis_client: RedisClient,
    user: CurrentNonGuestUser,
) -> FindingFromHierarchyItemResponse:
    """Promote a projected item while preserving every source Finding.

    The engagement lock serializes duplicate review with creation. The hierarchy
    item is re-projected under that lock, so stale or cross-engagement opaque IDs
    cannot be used to forge provenance.
    """
    engagement = _lock_writable_engagement(session, slug)

    existing_retry = session.scalar(
        select(Finding).where(
            Finding.engagement_id == engagement.id,
            Finding.deleted_at.is_(None),
            Finding.details["hierarchy_promotion"]["idempotency_key"].astext
            == str(body.idempotency_key),
        )
    )
    if existing_retry is not None:
        from app.api.engagements import _finding_to_read

        return FindingFromHierarchyItemResponse(
            state="created",
            finding=_finding_to_read(existing_retry),
        )

    hierarchy = _projection(session, engagement)
    item = find_hierarchy_item(hierarchy, body.item_id)
    if item is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "stale_hierarchy_item",
                "message": (
                    "This item changed or is no longer available. Refresh Findings and try again."
                ),
            },
        )

    source_refs = hierarchy_item_finding_refs(item)
    source_ids = [ref.id for ref in source_refs]
    inventory_source_ids = [ref.id for ref in source_refs if is_inventory_source_tool(ref.tool)]
    target = body.target or item.suggested_target or item.value
    source_target = item.suggested_target or item.value
    source_target_key = hierarchy_duplicate_target_key(item, source_target)
    submitted_target_key = hierarchy_duplicate_target_key(item, target)
    if source_target_key is not None and submitted_target_key != source_target_key:
        raise HTTPException(
            status_code=422,
            detail=(
                "affected target must remain canonically equivalent to the promoted hierarchy item"
            ),
        )
    duplicate_query = select(Finding).where(
        Finding.engagement_id == engagement.id,
        Finding.deleted_at.is_(None),
    )
    if inventory_source_ids:
        duplicate_query = duplicate_query.where(Finding.id.not_in(inventory_source_ids))
    target_key = hierarchy_duplicate_target_key(item, target)
    duplicate_rows: list[Finding] = []
    for candidate in session.scalars(duplicate_query.order_by(Finding.created_at.desc())):
        promotion = (candidate.details or {}).get("hierarchy_promotion", {})
        same_item = promotion.get("item_id") == item.id
        same_target = (
            target_key is not None
            and hierarchy_duplicate_target_key(item, candidate.target) == target_key
        )
        if same_item or same_target:
            duplicate_rows.append(candidate)
        if len(duplicate_rows) >= 10:
            break
    candidates = [
        _candidate(
            row,
            reason=(
                "already promoted from this item"
                if (row.details or {}).get("hierarchy_promotion", {}).get("item_id") == item.id
                else "same canonical affected target"
            ),
        )
        for row in duplicate_rows
    ]
    reviewed_ids = set(body.reviewed_duplicate_ids)
    current_ids = {row.id for row in duplicate_rows}
    if body.duplicate_decision == "create_anyway" and reviewed_ids != current_ids:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "duplicate_review_stale",
                "message": "Duplicate candidates changed. Review them again before creating.",
            },
        )
    if duplicate_rows and body.duplicate_decision == "review":
        return FindingFromHierarchyItemResponse(
            state="duplicate_warning",
            candidates=candidates,
        )

    snapshot: dict[str, Any] = {
        "projection_version": hierarchy.projection_version,
        "item_id": item.id,
        "kind": item.kind,
        "canonical_key": item.canonical_key,
        "label": item.label,
        "value": item.value,
        "ip": item.ip,
        "hostname": item.hostname,
        "protocol": item.protocol,
        "port": item.port,
        "service": item.service,
        "url": item.url,
        "source_finding_ids": [str(source_id) for source_id in source_ids],
        "source_rollup": item.rollup.model_dump(mode="json"),
        "promoted_at": datetime.now(tz=UTC).isoformat(),
        "promoted_by": str(user.id),
    }
    finding = Finding(
        engagement_id=engagement.id,
        title=body.title,
        severity=body.severity,
        phase=body.phase,
        summary=body.summary,
        target=target,
        source_tool="manual_promotion",
        details={
            "hierarchy_promotion": {
                **snapshot,
                "idempotency_key": str(body.idempotency_key),
            }
        },
        status=FindingStatus.pending_validation,
        observed_at=body.observed_at,
        tags=["promoted-from-inventory"],
    )
    session.add(finding)
    session.flush()

    session.add(
        AuditLog(
            engagement_id=engagement.id,
            actor_type=ActorType.user,
            actor_id=str(user.id),
            event_type="finding.promoted_from_hierarchy_item",
            payload={
                "finding_id": str(finding.id),
                "hierarchy_item_id": item.id,
                "source_finding_ids": [str(value) for value in source_ids],
                "duplicate_override": body.duplicate_decision == "create_anyway",
            },
        )
    )
    from app.services.finding_feedback import stage_finding_feedback

    feedback_entry = stage_finding_feedback(
        session,
        finding=finding,
        acting_user_id=user.id,
        operation_id=body.idempotency_key,
        source="manual",
    )
    session.commit()
    session.refresh(finding)
    from app.api.engagements import _finding_to_read
    from app.services.finding_feedback import publish_feedback_entries

    publish_feedback_entries(session, redis_client, [feedback_entry])
    return FindingFromHierarchyItemResponse(
        state="created",
        finding=_finding_to_read(finding),
    )
