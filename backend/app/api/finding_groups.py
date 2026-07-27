from __future__ import annotations

import hashlib
import json
import uuid
from collections import Counter

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import select, text

from app.api.deps import CurrentNonGuestUser, CurrentUser, DbSession
from app.models import (
    ActorType,
    AuditLog,
    Engagement,
    Finding,
    FindingGroup,
    FindingGroupMember,
    Severity,
)
from app.schemas.finding import FindingRead
from app.schemas.finding_group import (
    FindingGroupCreate,
    FindingGroupMemberRead,
    FindingGroupRead,
    FindingGroupRollupRead,
    FindingGroupUpdate,
)

router = APIRouter()

_SEVERITY_RANK = {
    Severity.info: 0,
    Severity.low: 1,
    Severity.medium: 2,
    Severity.high: 3,
    Severity.critical: 4,
}


def _engagement(session: DbSession, slug: str) -> Engagement:
    row = session.scalar(select(Engagement).where(Engagement.slug == slug))
    if row is None:
        raise HTTPException(status_code=404, detail="engagement not found")
    return row


def _canonical_request(body: FindingGroupCreate) -> str:
    payload = {
        "name": body.name,
        "rationale": body.rationale,
        "finding_ids": [str(value) for value in body.finding_ids],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _validate_and_lock_findings(
    session: DbSession,
    *,
    engagement_id: uuid.UUID,
    finding_ids: list[uuid.UUID],
) -> list[Finding]:
    ordered_ids = sorted(finding_ids, key=str)
    rows = list(
        session.scalars(
            select(Finding)
            .where(Finding.id.in_(ordered_ids))
            .order_by(Finding.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    by_id = {row.id: row for row in rows}
    if len(rows) != len(ordered_ids):
        raise HTTPException(
            status_code=422,
            detail="every group member must be an existing Finding",
        )
    for row in rows:
        if row.engagement_id != engagement_id:
            raise HTTPException(
                status_code=422,
                detail="every group member must belong to the route engagement",
            )
        if row.deleted_at is not None:
            raise HTTPException(
                status_code=422,
                detail="deleted Findings cannot be added to a group",
            )
    return [by_id[finding_id] for finding_id in finding_ids]


def _member_rows(
    session: DbSession,
    groups: list[FindingGroup],
) -> dict[uuid.UUID, list[tuple[FindingGroupMember, Finding]]]:
    if not groups:
        return {}
    grouped: dict[uuid.UUID, list[tuple[FindingGroupMember, Finding]]] = {
        group.id: [] for group in groups
    }
    rows = session.execute(
        select(FindingGroupMember, Finding)
        .join(Finding, Finding.id == FindingGroupMember.finding_id)
        .where(FindingGroupMember.group_id.in_([group.id for group in groups]))
        .order_by(FindingGroupMember.group_id, FindingGroupMember.sort_order)
    ).all()
    for member, finding in rows:
        grouped.setdefault(member.group_id, []).append((member, finding))
    return grouped


def _group_read(
    group: FindingGroup,
    members: list[tuple[FindingGroupMember, Finding]],
) -> FindingGroupRead:
    from app.api.engagements import _finding_to_read

    available = [finding for _, finding in members if finding.deleted_at is None]
    max_severity = max(
        (finding.severity for finding in available),
        key=lambda value: _SEVERITY_RANK[value],
        default=Severity.info,
    )
    status_counts = Counter(finding.status.value for finding in available)
    member_reads = [
        FindingGroupMemberRead(
            finding_id=finding.id,
            sort_order=member.sort_order,
            available=finding.deleted_at is None,
            finding=FindingRead.model_validate(_finding_to_read(finding)),
        )
        for member, finding in members
    ]
    return FindingGroupRead(
        id=group.id,
        engagement_id=group.engagement_id,
        name=group.name,
        rationale=group.rationale,
        created_by_user_id=group.created_by_user_id,
        row_version=group.row_version,
        created_at=group.created_at,
        updated_at=group.updated_at,
        members=member_reads,
        rollup=FindingGroupRollupRead(
            member_count=len(members),
            available_members=len(available),
            unavailable_members=len(members) - len(available),
            max_severity=max_severity,
            status_counts=dict(status_counts),
            excluded_count=sum(finding.exclusion is not None for finding in available),
        ),
    )


def _read_one(session: DbSession, group: FindingGroup) -> FindingGroupRead:
    return _group_read(group, _member_rows(session, [group]).get(group.id, []))


def _locked_group(
    session: DbSession,
    *,
    engagement_id: uuid.UUID,
    group_id: uuid.UUID,
) -> FindingGroup:
    group = session.scalar(
        select(FindingGroup)
        .where(
            FindingGroup.id == group_id,
            FindingGroup.engagement_id == engagement_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if group is None:
        raise HTTPException(status_code=404, detail="Finding group not found")
    return group


@router.get(
    "/engagements/{slug}/finding-groups",
    response_model=list[FindingGroupRead],
)
def list_finding_groups(
    slug: str,
    session: DbSession,
    _user: CurrentUser,
) -> list[FindingGroupRead]:
    engagement = _engagement(session, slug)
    groups = list(
        session.scalars(
            select(FindingGroup)
            .where(FindingGroup.engagement_id == engagement.id)
            .order_by(FindingGroup.updated_at.desc(), FindingGroup.id)
        )
    )
    memberships = _member_rows(session, groups)
    return [_group_read(group, memberships.get(group.id, [])) for group in groups]


@router.post(
    "/engagements/{slug}/finding-groups",
    response_model=FindingGroupRead,
    status_code=status.HTTP_201_CREATED,
)
def create_finding_group(
    slug: str,
    body: FindingGroupCreate,
    session: DbSession,
    user: CurrentNonGuestUser,
) -> FindingGroupRead:
    from app.api.engagements import _reject_flushed

    engagement = _engagement(session, slug)
    _reject_flushed(engagement)
    # Serialize retries for this engagement/key before the first lookup so
    # concurrent identical submissions cannot race the unique constraint.
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
        {"lock_key": f"finding-group:{engagement.id}:{body.idempotency_key}"},
    )
    digest = _canonical_request(body)
    existing = session.scalar(
        select(FindingGroup).where(
            FindingGroup.engagement_id == engagement.id,
            FindingGroup.idempotency_key == body.idempotency_key,
        )
    )
    if existing is not None:
        if existing.request_sha256 != digest:
            raise HTTPException(
                status_code=409,
                detail="idempotency key was already used for a different Finding group",
            )
        return _read_one(session, existing)

    findings = _validate_and_lock_findings(
        session,
        engagement_id=engagement.id,
        finding_ids=body.finding_ids,
    )
    group = FindingGroup(
        engagement_id=engagement.id,
        name=body.name,
        rationale=body.rationale,
        created_by_user_id=user.id,
        row_version=1,
        idempotency_key=body.idempotency_key,
        request_sha256=digest,
    )
    session.add(group)
    session.flush()
    for index, finding in enumerate(findings):
        session.add(
            FindingGroupMember(
                group_id=group.id,
                finding_id=finding.id,
                sort_order=index,
                added_by_user_id=user.id,
            )
        )
    session.add(
        AuditLog(
            engagement_id=engagement.id,
            actor_type=ActorType.user,
            actor_id=str(user.id),
            event_type="finding_group.created",
            payload={
                "group_id": str(group.id),
                "name": group.name,
                "rationale": group.rationale,
                "finding_ids": [str(value) for value in body.finding_ids],
                "row_version": group.row_version,
            },
        )
    )
    session.commit()
    session.refresh(group)
    return _read_one(session, group)


@router.put(
    "/engagements/{slug}/finding-groups/{group_id}",
    response_model=FindingGroupRead,
)
def update_finding_group(
    slug: str,
    group_id: uuid.UUID,
    body: FindingGroupUpdate,
    session: DbSession,
    user: CurrentNonGuestUser,
) -> FindingGroupRead:
    from app.api.engagements import _reject_flushed

    engagement = _engagement(session, slug)
    _reject_flushed(engagement)
    group = _locked_group(session, engagement_id=engagement.id, group_id=group_id)
    if group.row_version != body.expected_row_version:
        raise HTTPException(
            status_code=409,
            detail="Finding group changed since this editor opened; reload and retry",
        )
    findings = _validate_and_lock_findings(
        session,
        engagement_id=engagement.id,
        finding_ids=body.finding_ids,
    )
    current_members = list(
        session.scalars(
            select(FindingGroupMember)
            .where(FindingGroupMember.group_id == group.id)
            .order_by(FindingGroupMember.sort_order)
            .with_for_update()
        )
    )
    current_by_id = {member.finding_id: member for member in current_members}
    old_finding_ids = [member.finding_id for member in current_members]
    old_ids = set(current_by_id)
    new_ids = set(body.finding_ids)
    old_name = group.name
    old_rationale = group.rationale

    # Move retained rows out of the bounded display-order range before
    # assigning final positions, so reordering cannot transiently violate the
    # per-group unique sort-order constraint.
    for index, member in enumerate(current_members):
        member.sort_order = 10_000 + index
    session.flush()
    for finding_id in old_ids - new_ids:
        session.delete(current_by_id[finding_id])
    session.flush()
    for index, finding in enumerate(findings):
        retained_member = current_by_id.get(finding.id)
        if retained_member is not None:
            retained_member.sort_order = index
    session.flush()
    for index, finding in enumerate(findings):
        if finding.id not in current_by_id:
            session.add(
                FindingGroupMember(
                    group_id=group.id,
                    finding_id=finding.id,
                    sort_order=index,
                    added_by_user_id=user.id,
                )
            )

    group.name = body.name
    group.rationale = body.rationale
    group.row_version += 1
    session.add(
        AuditLog(
            engagement_id=engagement.id,
            actor_type=ActorType.user,
            actor_id=str(user.id),
            event_type="finding_group.updated",
            payload={
                "group_id": str(group.id),
                "previous": {
                    "name": old_name,
                    "rationale": old_rationale,
                    "finding_ids": [str(finding_id) for finding_id in old_finding_ids],
                    "row_version": body.expected_row_version,
                },
                "current": {
                    "name": group.name,
                    "rationale": group.rationale,
                    "finding_ids": [str(value) for value in body.finding_ids],
                    "row_version": group.row_version,
                },
                "added_finding_ids": [str(value) for value in sorted(new_ids - old_ids, key=str)],
                "removed_finding_ids": [
                    str(value) for value in sorted(old_ids - new_ids, key=str)
                ],
            },
        )
    )
    session.commit()
    session.refresh(group)
    return _read_one(session, group)


@router.delete(
    "/engagements/{slug}/finding-groups/{group_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_finding_group(
    slug: str,
    group_id: uuid.UUID,
    session: DbSession,
    user: CurrentNonGuestUser,
    expected_row_version: int = Query(ge=1),
) -> Response:
    from app.api.engagements import _reject_flushed

    engagement = _engagement(session, slug)
    _reject_flushed(engagement)
    group = _locked_group(session, engagement_id=engagement.id, group_id=group_id)
    if group.row_version != expected_row_version:
        raise HTTPException(
            status_code=409,
            detail="Finding group changed since this view loaded; reload and retry",
        )
    member_ids = list(
        session.scalars(
            select(FindingGroupMember.finding_id)
            .where(FindingGroupMember.group_id == group.id)
            .order_by(FindingGroupMember.sort_order)
        )
    )
    session.add(
        AuditLog(
            engagement_id=engagement.id,
            actor_type=ActorType.user,
            actor_id=str(user.id),
            event_type="finding_group.deleted",
            payload={
                "group_id": str(group.id),
                "name": group.name,
                "rationale": group.rationale,
                "finding_ids": [str(value) for value in member_ids],
                "row_version": group.row_version,
            },
        )
    )
    session.delete(group)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
