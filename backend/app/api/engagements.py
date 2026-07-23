"""Engagements + nested scope + runs HTTP surface.

Engagements are addressed in URLs by their ``slug`` (human-set, non-sequential)
rather than the UUIDv7 primary key — the UUIDs still appear in JSON responses
(``id`` field) and as FKs internally, just never in paths.

Endpoints::

    POST   /engagements                                 -> create
    GET    /engagements                                 -> list (?status filter)
    GET    /engagements/{slug}                          -> read
    PATCH  /engagements/{slug}                          -> rename / archive / unarchive
    DELETE /engagements/{slug}                          -> soft archive
    POST   /engagements/{slug}/flush                    -> irreversible (calls flush_engagement)

    POST   /engagements/{slug}/scope                    -> create scope item
    GET    /engagements/{slug}/scope                    -> list scope items
    PATCH  /engagements/{slug}/scope/{scope_id}         -> update
    DELETE /engagements/{slug}/scope/{scope_id}         -> remove

    GET    /engagements/{slug}/findings                 -> list persisted findings

    GET    /engagements/{slug}/observations              -> list observations
    POST   /engagements/{slug}/observations              -> create observation
    DELETE /observations/{observation_id}                -> delete observation

    POST   /engagements/{slug}/findings/import           -> bulk import findings (JSON/CSV)
    POST   /engagements/{slug}/findings/import/nessus    -> import Nessus .nessus v2 XML
    POST   /engagements/{slug}/findings/import/nmap      -> import Nmap -oX XML
    POST   /engagements/{slug}/findings/import/{source}/preview -> parse without writes
    POST   /engagements/{slug}/findings/import/{source}/commit  -> persist selected groups
    PATCH  /findings/{finding_id}                        -> update title/summary/severity/phase
    GET    /engagements/{slug}/export                    -> full JSON snapshot

    POST   /findings/{finding_id}/attachments            -> upload screenshot/evidence file
    GET    /findings/{finding_id}/attachments            -> list attachment metadata
    GET    /attachments/{attachment_id}                  -> serve raw bytes
    DELETE /attachments/{attachment_id}                  -> delete

    POST   /engagements/{slug}/runs                     -> enqueue run.start

DELETE soft-archives the engagement (worker stops considering it for new runs
once status != active); /flush is the destructive operation, gated to a
separate endpoint so it can't fire from a stray HTTP verb.
"""

from __future__ import annotations

import contextlib
import json
import re
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field, RootModel, field_validator, model_validator
from sqlalchemy import case, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import object_session

from app.api.deps import (
    CurrentAdminUser,
    CurrentNonGuestUser,
    CurrentUser,
    DbSession,
    RedisClient,
    RequireScope,
)
from app.core.blob import upload_engagement_export
from app.models import (
    ActorType,
    Attachment,
    AuditLog,
    Engagement,
    EngagementStatus,
    Finding,
    FindingPhase,
    FindingStatus,
    FindingSummary,
    Observation,
    ObservationFindingLink,
    ScopeItem,
    Severity,
    TaskKind,
    User,
)
from app.models.api_key import APIKeyScope
from app.orchestrator.llm import default_provider_model
from app.runs.streams import inbound_stream, outbound_stream, store_run_model
from app.schemas.engagement import (
    EngagementCreate,
    EngagementRead,
    EngagementUpdate,
    RunModel,
    RunStart,
    RunStartResponse,
    ScopeImportPreview,
    ScopeImportRequest,
    ScopeImportResult,
    ScopeItemCreate,
    ScopeItemRead,
    ScopeItemUpdate,
)
from app.schemas.finding import (
    MAX_FINDING_SUMMARY_CHARS,
    MAX_FINDING_TAGS,
    AttachmentRead,
    CorrelateGroup,
    CorrelateResponse,
    EntityRead,
    FindingBulkUpdate,
    FindingBulkUpdateResult,
    FindingCreate,
    FindingRead,
    FindingSummaryCreate,
    FindingSummaryRead,
    FindingUpdate,
    FindingValidate,
    MergeRequest,
    RegroupApplyRequest,
    RegroupApplyResult,
    RegroupPreview,
    RegroupProposal,
    RepairGroupsResult,
    _normalize_tags,
)
from app.schemas.observation import ObservationCreate, ObservationRead
from app.services import methodology as methodology_service
from app.services.command_outbox import enqueue_command, publish_entry
from app.services.entities import annotate_scope_status, extract_entities
from app.services.findings import (
    get_active_finding_or_404,
    lock_active_finding_or_404,
)
from app.services.scope_import import parse_scope_text

router = APIRouter()


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(name: str) -> str:
    cleaned = _SLUG_RE.sub("-", name.lower()).strip("-")
    return cleaned or "engagement"


def _unique_slug(session: DbSession, base: str) -> str:
    candidate = base
    while session.execute(select(Engagement.id).where(Engagement.slug == candidate)).first():
        candidate = f"{base}-{uuid.uuid4().hex[:6]}"
    return candidate


def _get_engagement_or_404(session: DbSession, slug: str) -> Engagement:
    eng = session.execute(select(Engagement).where(Engagement.slug == slug)).scalar_one_or_none()
    if eng is None:
        raise HTTPException(status_code=404, detail="engagement not found")
    return eng


# v1.4.5: scope quick-actions. Cheap aggregate so the engagement list
# cards can render ``N in scope · M exclusions`` without an N+1 trip per
# card. ``scope_count`` counts the actionable (non-exclusion) items;
# ``exclusion_count`` counts the !-marked items. Returns
# ``(scope_count, exclusion_count)`` for one engagement.
def _scope_audit_value(item: ScopeItem) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "kind": item.kind.value,
        "value": item.value,
        "is_exclusion": item.is_exclusion,
        "note": item.note,
        "source": item.source,
    }


def _scope_counts_for(session: DbSession, engagement_id: uuid.UUID) -> tuple[int, int]:
    rows = session.execute(
        select(
            func.sum(case((ScopeItem.is_exclusion.is_(False), 1), else_=0)),
            func.sum(case((ScopeItem.is_exclusion.is_(True), 1), else_=0)),
        ).where(ScopeItem.engagement_id == engagement_id)
    ).one()
    return int(rows[0] or 0), int(rows[1] or 0)


def _populate_scope_counts(session: DbSession, reads: list[EngagementRead]) -> list[EngagementRead]:
    """Bulk fill ``scope_count`` / ``exclusion_count`` on a list of read
    shapes with a single grouped query. No-op when ``reads`` is empty."""
    if not reads:
        return reads
    engagement_ids = [r.id for r in reads]
    grouped = session.execute(
        select(
            ScopeItem.engagement_id,
            func.sum(case((ScopeItem.is_exclusion.is_(False), 1), else_=0)),
            func.sum(case((ScopeItem.is_exclusion.is_(True), 1), else_=0)),
        )
        .where(ScopeItem.engagement_id.in_(engagement_ids))
        .group_by(ScopeItem.engagement_id)
    ).all()
    counts: dict[uuid.UUID, tuple[int, int]] = {
        row[0]: (int(row[1] or 0), int(row[2] or 0)) for row in grouped
    }
    for r in reads:
        scope, excl = counts.get(r.id, (0, 0))
        r.scope_count = scope
        r.exclusion_count = excl
    return reads


def _populate_has_strategy(session: DbSession, reads: list[EngagementRead]) -> list[EngagementRead]:
    """v2.4.0: bulk fill ``has_strategy`` — true when the engagement has a
    ``state = current`` row in ``engagement_strategy_revisions``. Frontend
    combines this with ``scope_count`` + ``start_date`` to derive whether
    the engagement should render as "pending" (setup incomplete) instead
    of "active" on the list page. Single grouped query so this is cheap
    on the list endpoint. Uses raw SQL against
    ``engagement_strategy_revisions`` so the engagement schema doesn't
    need to import the strategy model tree."""
    if not reads:
        return reads
    engagement_ids = [str(r.id) for r in reads]
    rows = session.execute(
        text(
            "SELECT engagement_id FROM engagement_strategy_revisions "
            "WHERE state = 'current' AND engagement_id = ANY(:ids)"
        ).bindparams(ids=engagement_ids)
    ).all()
    have = {row[0] for row in rows}
    for r in reads:
        r.has_strategy = r.id in have
    return reads


_AUDIT_LEDGER_LIMIT = 1000


def _build_export_payload(
    session: DbSession,
    eng: Engagement,
    *,
    omit_excluded: bool = False,
) -> dict[str, Any]:
    """Assemble a complete engagement snapshot suitable for blob archiving.

    ``omit_excluded=True`` drops findings marked ``out_of_scope`` or
    ``outside_roe`` from the ``findings`` list (v1.4.0). The count of
    dropped rows is surfaced on the payload as ``excluded_count`` so the
    caller can see the toggle actually did something.
    """
    scope_items = list(
        session.execute(select(ScopeItem).where(ScopeItem.engagement_id == eng.id)).scalars()
    )
    findings_stmt = select(Finding).where(
        Finding.engagement_id == eng.id,
        Finding.deleted_at.is_(None),
    )
    if omit_excluded:
        findings_stmt = findings_stmt.where(Finding.exclusion.is_(None))
    findings = list(session.execute(findings_stmt).scalars())
    excluded_count = 0
    if omit_excluded:
        excluded_count = int(
            session.execute(
                select(func.count(Finding.id)).where(
                    Finding.engagement_id == eng.id,
                    Finding.deleted_at.is_(None),
                    Finding.exclusion.is_not(None),
                )
            ).scalar_one()
        )
    audit_count, audit_first, audit_last = session.execute(
        select(
            func.count(AuditLog.id),
            func.min(AuditLog.created_at),
            func.max(AuditLog.created_at),
        ).where(AuditLog.engagement_id == eng.id)
    ).one()
    audit_summary: dict[str, Any] = {"count": int(audit_count or 0)}
    if audit_first is not None:
        audit_summary["first"] = str(audit_first)
        audit_summary["last"] = str(audit_last)

    # Internal archives carry the durable ledger itself. Keep only the newest
    # bounded window, then restore chronological order for deterministic replay.
    audit_rows: list[AuditLog] = []
    if not omit_excluded:
        audit_rows = list(
            session.execute(
                select(AuditLog)
                .where(AuditLog.engagement_id == eng.id)
                .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
                .limit(_AUDIT_LEDGER_LIMIT)
            ).scalars()
        )
        audit_rows.reverse()

    observations = list(
        session.execute(
            select(Observation)
            .where(Observation.engagement_id == eng.id)
            .order_by(Observation.created_at)
        ).scalars()
    )

    # Shared Engagement Strategist state is part of the canonical archive.
    # Personal raw strategist/finding conversations remain user-scoped and are
    # intentionally excluded, matching the existing conversation privacy rule.
    from app.models import (
        CoverageItem,
        EngagementCheckpoint,
        EngagementCompletionDecision,
        EngagementObjective,
        EngagementStrategyRevision,
        StrategySignal,
        Task,
        WorkItem,
        WorkItemFinding,
        WorkItemResult,
    )

    strategies = list(
        session.execute(
            select(EngagementStrategyRevision)
            .where(EngagementStrategyRevision.engagement_id == eng.id)
            .order_by(EngagementStrategyRevision.version)
        ).scalars()
    )
    objectives = list(
        session.execute(
            select(EngagementObjective)
            .where(EngagementObjective.engagement_id == eng.id)
            .order_by(EngagementObjective.display_order, EngagementObjective.created_at)
        ).scalars()
    )
    work_items = list(
        session.execute(
            select(WorkItem).where(WorkItem.engagement_id == eng.id).order_by(WorkItem.created_at)
        ).scalars()
    )
    work_ids = [row.id for row in work_items]
    work_results = (
        list(
            session.execute(
                select(WorkItemResult)
                .where(WorkItemResult.work_item_id.in_(work_ids))
                .order_by(WorkItemResult.work_item_id, WorkItemResult.revision)
            ).scalars()
        )
        if work_ids
        else []
    )
    work_findings = (
        list(
            session.execute(
                select(WorkItemFinding).where(WorkItemFinding.work_item_id.in_(work_ids))
            ).scalars()
        )
        if work_ids
        else []
    )
    signals = list(
        session.execute(
            select(StrategySignal)
            .where(StrategySignal.engagement_id == eng.id)
            .order_by(StrategySignal.created_at)
        ).scalars()
    )
    coverage = list(
        session.execute(
            select(CoverageItem)
            .where(CoverageItem.engagement_id == eng.id)
            .order_by(CoverageItem.created_at)
        ).scalars()
    )
    checkpoints = list(
        session.execute(
            select(EngagementCheckpoint)
            .where(EngagementCheckpoint.engagement_id == eng.id)
            .order_by(EngagementCheckpoint.created_at)
        ).scalars()
    )
    completion_decisions = list(
        session.execute(
            select(EngagementCompletionDecision)
            .where(EngagementCompletionDecision.engagement_id == eng.id)
            .order_by(EngagementCompletionDecision.created_at)
        ).scalars()
    )
    linked_tasks = list(
        session.execute(
            select(Task)
            .where(Task.engagement_id == eng.id, Task.work_item_id.is_not(None))
            .order_by(Task.created_at)
        ).scalars()
    )

    return {
        "version": "2",
        "exported_at": str(datetime.now(tz=UTC)),
        "engagement": {
            "id": str(eng.id),
            "slug": eng.slug,
            "name": eng.name,
            "status": eng.status,
            "description": eng.description,
            "work_state": eng.work_state,
            "work_state_version": eng.work_state_version,
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
                "exclusion": f.exclusion.value if f.exclusion else None,
                "target": f.target,
                "source_tool": f.source_tool,
                "phase": f.phase,
                "summary": f.summary,
                "details": f.details,
                "created_at": str(f.created_at),
            }
            for f in findings
        ],
        "export_profile": "client" if omit_excluded else "internal",
        "omit_excluded": omit_excluded,
        "excluded_count": excluded_count,
        "observations": [
            {
                "content": o.content,
                "phase": o.phase,
                "created_at": str(o.created_at),
            }
            for o in observations
        ],
        "strategy_revisions": [
            {
                "id": str(row.id),
                "version": row.version,
                "state": row.state,
                "based_on_revision_id": str(row.based_on_revision_id)
                if row.based_on_revision_id
                else None,
                "summary": row.summary,
                "body": row.body,
                "structured": row.structured,
                "created_by_user_id": str(row.created_by_user_id)
                if row.created_by_user_id
                else None,
                "created_at": str(row.created_at),
            }
            for row in strategies
        ],
        "objectives": [
            {
                "id": str(row.id),
                "title": row.title,
                "description": row.description,
                "success_criteria": row.success_criteria,
                "status": row.status,
                "priority": row.priority,
                "display_order": row.display_order,
                "owner_user_id": str(row.owner_user_id) if row.owner_user_id else None,
                "target_date": str(row.target_date) if row.target_date else None,
                "row_version": row.row_version,
            }
            for row in objectives
        ],
        "work_items": [
            {
                "id": str(row.id),
                "objective_id": str(row.objective_id) if row.objective_id else None,
                "parent_work_item_id": str(row.parent_work_item_id)
                if row.parent_work_item_id
                else None,
                "title": row.title,
                "description": row.description,
                "rationale": row.rationale,
                "acceptance_criteria": row.acceptance_criteria,
                "status": row.status,
                "priority": row.priority,
                "executor_type": row.executor_type,
                "assigned_user_id": str(row.assigned_user_id) if row.assigned_user_id else None,
                "blocked_reason": row.blocked_reason,
                "due_at": str(row.due_at) if row.due_at else None,
                "resolution_outcome": row.resolution_outcome,
                "resolution_note": row.resolution_note,
                "row_version": row.row_version,
                "created_at": str(row.created_at),
            }
            for row in work_items
        ],
        "work_item_findings": [
            {
                "work_item_id": str(row.work_item_id),
                "finding_id": str(row.finding_id),
                "relationship": row.relationship,
                "created_at": str(row.created_at),
            }
            for row in work_findings
        ],
        "work_item_results": [
            {
                "id": str(row.id),
                "work_item_id": str(row.work_item_id),
                "revision": row.revision,
                "state": row.state,
                "summary": row.summary,
                "structured": row.structured,
                "evidence_refs": row.evidence_refs,
                "created_at": str(row.created_at),
            }
            for row in work_results
        ],
        "strategy_signals": [
            {
                "id": str(row.id),
                "source_finding_id": str(row.source_finding_id) if row.source_finding_id else None,
                "source_work_item_id": str(row.source_work_item_id)
                if row.source_work_item_id
                else None,
                "source_work_item_result_id": str(row.source_work_item_result_id)
                if row.source_work_item_result_id
                else None,
                "signal_type": row.signal_type,
                "summary": row.summary,
                "confidence": row.confidence,
                "evidence_refs": row.evidence_refs,
                "suggested_effect": row.suggested_effect,
                "dedup_key": row.dedup_key,
                "status": row.status,
            }
            for row in signals
        ],
        "coverage_items": [
            {
                "id": str(row.id),
                "objective_id": str(row.objective_id) if row.objective_id else None,
                "scope_item_id": str(row.scope_item_id) if row.scope_item_id else None,
                "target_kind": row.target_kind,
                "target_key": row.target_key,
                "activity_category": row.activity_category,
                "status": row.status,
                "supporting_refs": row.supporting_refs,
                "reason": row.reason,
                "row_version": row.row_version,
            }
            for row in coverage
        ],
        "checkpoints": [
            {
                "id": str(row.id),
                "strategy_revision_id": str(row.strategy_revision_id)
                if row.strategy_revision_id
                else None,
                "material_event_cursor": str(row.material_event_cursor),
                "facts": row.facts,
                "narrative": row.narrative,
                "created_at": str(row.created_at),
            }
            for row in checkpoints
        ],
        "completion_decisions": [
            {
                "id": str(row.id),
                "action": row.action,
                "from_work_state": row.from_work_state,
                "to_work_state": row.to_work_state,
                "readiness_hash": row.readiness_hash,
                "readiness_snapshot": row.readiness_snapshot,
                "accepted_exceptions": row.accepted_exceptions,
                "prior_completion_decision_id": str(row.prior_completion_decision_id)
                if row.prior_completion_decision_id
                else None,
                "reason": row.reason,
                "idempotency_key": row.idempotency_key,
                "decided_by_user_id": str(row.decided_by_user_id),
                "created_at": str(row.created_at),
            }
            for row in completion_decisions
        ],
        "work_item_execution_links": [
            {"task_id": str(row.id), "work_item_id": str(row.work_item_id)} for row in linked_tasks
        ],
        "audit_summary": audit_summary,
        **(
            {
                "audit_ledger": [
                    {
                        "event_id": str(row.id),
                        "event_type": row.event_type,
                        "actor": {
                            "type": row.actor_type.value,
                            "id": row.actor_id,
                        },
                        "timestamp": str(row.created_at),
                        "payload": row.payload,
                    }
                    for row in audit_rows
                ],
                "audit_ledger_truncated": int(audit_count or 0) > _AUDIT_LEDGER_LIMIT,
                "audit_ledger_limit": _AUDIT_LEDGER_LIMIT,
            }
            if not omit_excluded
            else {}
        ),
    }


def _reject_only_flushed(eng: Engagement) -> None:
    """Unlocked read/preview guard; does not serialize a mutation."""
    if eng.status is EngagementStatus.flushed:
        raise HTTPException(
            status_code=409,
            detail="engagement has been flushed; the row will be gone shortly",
        )


def _lock_not_flushed(eng: Engagement) -> None:
    session = object_session(eng)
    if session is not None:
        session.refresh(eng, with_for_update=True)
    _reject_only_flushed(eng)


def _reject_flushed(eng: Engagement) -> None:
    """Lock the engagement and reject read-only lifecycle mutations."""
    _lock_not_flushed(eng)
    if eng.status is EngagementStatus.archived:
        raise HTTPException(status_code=409, detail="archived engagement is read-only")
    from app.models import EngagementWorkState

    if eng.work_state is EngagementWorkState.completed:
        raise HTTPException(
            status_code=409,
            detail="completed engagement is read-only; reopen it before making changes",
        )


def _reject_engagement_id(session: DbSession, engagement_id: uuid.UUID) -> None:
    eng = session.get(Engagement, engagement_id)
    if eng is None:
        raise HTTPException(status_code=404, detail="engagement not found")
    _reject_flushed(eng)


def _lock_active_finding_for_mutation(session: DbSession, finding_id: uuid.UUID) -> Finding:
    """Lock the engagement then recheck and lock its visible finding."""
    finding = get_active_finding_or_404(session, finding_id)
    _reject_engagement_id(session, finding.engagement_id)
    return lock_active_finding_or_404(session, finding_id)


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
    # v1.4.0 (part 2): items[] is the grouped shape's per-hit list.
    # Surface the count on the wire so the Findings table can render
    # "(N)" without unpacking data on every row.
    items_val = details.get("items")
    item_count = len(items_val) if isinstance(items_val, list) else 0
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
        "exclusion": f.exclusion,
        "group_key": f.group_key,
        "item_count": item_count,
        "validated_at": f.validated_at,
        "observed_at": f.observed_at,
        "burp_serial_number": f.burp_serial_number,
        "created_at": f.created_at,
        "tags": list(f.tags or []),
    }


# ---------------------------------------------------------------------------
# Engagement CRUD
# ---------------------------------------------------------------------------


@router.post(
    "/engagements",
    response_model=EngagementRead,
    status_code=status.HTTP_201_CREATED,
)
def create_engagement(
    body: EngagementCreate,
    session: DbSession,
    user: CurrentNonGuestUser,
) -> EngagementRead:
    base_slug = _slugify(body.slug) if body.slug else _slugify(body.name)
    slug = _unique_slug(session, base_slug)
    eng = Engagement(
        name=body.name,
        slug=slug,
        description=body.description,
        status=EngagementStatus.active,
        time_frame=body.time_frame,
        start_date=body.start_date,
        end_date=body.end_date,
        created_by=user.id,
        intelligence_architecture=body.intelligence_architecture,
    )
    session.add(eng)
    try:
        session.flush()
    except IntegrityError as exc:
        constraint_name = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        session.rollback()
        if constraint_name in {"ix_engagements_slug", "engagements_slug_key"}:
            raise HTTPException(
                status_code=409,
                detail="engagement slug was claimed concurrently; retry creation",
            ) from exc
        raise

    if body.methodology_slug is not None:
        try:
            methodology_service.select_for_engagement(
                session,
                engagement_id=eng.id,
                slug=body.methodology_slug,
                version=body.methodology_version,
                actor_type=ActorType.user,
                actor_id=str(user.id),
            )
        except ValueError as exc:
            session.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Persist staged scope in the same transaction as the engagement. The
    # request schema rejects exact duplicates before this endpoint runs.
    scope_items: list[ScopeItem] = []
    for draft in body.initial_scope:
        scope_item = ScopeItem(
            engagement_id=eng.id,
            kind=draft.kind,
            value=draft.value,
            is_exclusion=draft.is_exclusion,
            note=draft.note,
            source="defined",
        )
        session.add(scope_item)
        scope_items.append(scope_item)

    include_count = sum(not item.is_exclusion for item in scope_items)
    exclusion_count = len(scope_items) - include_count
    session.add(
        AuditLog(
            engagement_id=eng.id,
            actor_type=ActorType.user,
            actor_id=str(user.id),
            event_type="engagement.created",
            payload={
                "name": eng.name,
                "slug": eng.slug,
                "initial_scope_count": len(scope_items),
                "include_count": include_count,
                "exclusion_count": exclusion_count,
                "intelligence_architecture": eng.intelligence_architecture.value,
                "methodology_id": (
                    str(eng.methodology_id) if eng.methodology_id else None
                ),
                "initial_scope": [
                    {
                        "kind": item.kind.value,
                        "value": item.value,
                        "is_exclusion": item.is_exclusion,
                        "note": item.note,
                        "source": "defined",
                    }
                    for item in scope_items
                ],
            },
        )
    )
    try:
        session.commit()
    except Exception:
        session.rollback()
        raise
    session.refresh(eng)
    read = EngagementRead.model_validate(eng)
    read.scope_count = include_count
    read.exclusion_count = exclusion_count
    _populate_has_strategy(session, [read])
    return read


@router.get("/engagements", response_model=list[EngagementRead])
def list_engagements(
    session: DbSession,
    _user: CurrentUser,
    status_filter: Annotated[
        EngagementStatus | None,
        Query(alias="status", description="Filter by status."),
    ] = None,
) -> list[EngagementRead]:
    stmt = select(Engagement)
    if status_filter is not None:
        stmt = stmt.where(Engagement.status == status_filter)
    stmt = stmt.order_by(Engagement.created_at.desc())
    rows = list(session.execute(stmt).scalars())
    reads = [EngagementRead.model_validate(r) for r in rows]
    _populate_scope_counts(session, reads)
    _populate_has_strategy(session, reads)
    return reads


@router.get("/engagements/{slug}", response_model=EngagementRead)
def get_engagement(slug: str, session: DbSession, _user: CurrentUser) -> EngagementRead:
    eng = _get_engagement_or_404(session, slug)
    read = EngagementRead.model_validate(eng)
    read.scope_count, read.exclusion_count = _scope_counts_for(session, eng.id)
    _populate_has_strategy(session, [read])
    return read


@router.patch("/engagements/{slug}", response_model=EngagementRead)
def update_engagement(
    slug: str,
    body: EngagementUpdate,
    session: DbSession,
    user: CurrentNonGuestUser,
) -> EngagementRead:
    eng = _get_engagement_or_404(session, slug)
    _lock_not_flushed(eng)
    from app.models import EngagementWorkState

    if eng.status is EngagementStatus.archived and (
        body.name is not None
        or body.auto_assess_enabled is not None
        or body.status is not EngagementStatus.active
    ):
        raise HTTPException(
            status_code=409,
            detail="archived engagement is read-only; only unarchive is allowed",
        )
    if eng.work_state is EngagementWorkState.completed and (
        body.name is not None or body.auto_assess_enabled is not None
    ):
        raise HTTPException(
            status_code=409,
            detail="completed engagement must be reopened before updating it",
        )

    audit_rows: list[AuditLog] = []
    if body.name is not None and body.name != eng.name:
        before = {"name": eng.name}
        eng.name = body.name
        audit_rows.append(
            AuditLog(
                engagement_id=eng.id,
                actor_type=ActorType.user,
                actor_id=str(user.id),
                event_type="engagement.updated",
                payload={"before": before, "after": {"name": eng.name}},
            )
        )

    if body.status is not None:
        if body.status is EngagementStatus.flushed:
            raise HTTPException(
                status_code=400,
                detail="use POST /engagements/{slug}/flush to flush",
            )
        if body.status is not eng.status:
            before = {
                "status": eng.status.value,
                "archived_at": str(eng.archived_at) if eng.archived_at else None,
            }
            event_type = (
                "engagement.unarchived"
                if body.status is EngagementStatus.active
                else "engagement.archived"
            )
            eng.archived_at = (
                None if body.status is EngagementStatus.active else datetime.now(tz=UTC)
            )
            eng.status = body.status
            audit_rows.append(
                AuditLog(
                    engagement_id=eng.id,
                    actor_type=ActorType.user,
                    actor_id=str(user.id),
                    event_type=event_type,
                    payload={
                        "before": before,
                        "after": {
                            "status": eng.status.value,
                            "archived_at": str(eng.archived_at) if eng.archived_at else None,
                        },
                    },
                )
            )

    if body.auto_assess_enabled is not None and body.auto_assess_enabled != eng.auto_assess_enabled:
        before = {"auto_assess_enabled": eng.auto_assess_enabled}
        eng.auto_assess_enabled = body.auto_assess_enabled
        audit_rows.append(
            AuditLog(
                engagement_id=eng.id,
                actor_type=ActorType.user,
                actor_id=str(user.id),
                event_type="engagement.auto_assess_updated",
                payload={
                    "before": before,
                    "after": {"auto_assess_enabled": eng.auto_assess_enabled},
                },
            )
        )

    session.add_all(audit_rows)
    session.commit()
    session.refresh(eng)
    read = EngagementRead.model_validate(eng)
    read.scope_count, read.exclusion_count = _scope_counts_for(session, eng.id)
    _populate_has_strategy(session, [read])
    return read


@router.post("/engagements/{slug}/export", dependencies=[Depends(RequireScope(APIKeyScope.admin))])
def export_engagement(
    slug: str,
    session: DbSession,
    omit_excluded: Annotated[
        bool,
        Query(
            description=(
                "Drop findings marked out_of_scope / outside_roe from the "
                "exported payload. Default false — the full engagement "
                "record still archives everything."
            ),
        ),
    ] = False,
) -> dict[str, Any]:
    """Export all engagement data (findings, scope, audit summary) to blob storage.

    Returns the blob URL if storage is configured, or the full payload inline
    if AZURE_STORAGE_ACCOUNT_NAME is unset (useful for local dev / manual backup).
    Requires admin scope.
    """
    eng = _get_engagement_or_404(session, slug)
    payload = _build_export_payload(session, eng, omit_excluded=omit_excluded)
    blob_url = upload_engagement_export(slug, payload)
    if blob_url:
        return {"slug": slug, "blob_url": blob_url}
    return {"slug": slug, "blob_url": None, "payload": payload}


@router.delete(
    "/engagements/{slug}",
    response_model=EngagementRead,
)
def archive_engagement(slug: str, session: DbSession, user: CurrentNonGuestUser) -> Engagement:
    eng = _get_engagement_or_404(session, slug)
    _lock_not_flushed(eng)
    if eng.status is not EngagementStatus.archived:
        before = {
            "status": eng.status.value,
            "archived_at": str(eng.archived_at) if eng.archived_at else None,
        }
        eng.status = EngagementStatus.archived
        eng.archived_at = datetime.now(tz=UTC)
        session.add(
            AuditLog(
                engagement_id=eng.id,
                actor_type=ActorType.user,
                actor_id=str(user.id),
                event_type="engagement.archived",
                payload={
                    "before": before,
                    "after": {
                        "status": eng.status.value,
                        "archived_at": str(eng.archived_at),
                    },
                },
            )
        )
        session.commit()
        session.refresh(eng)
        # Export after the audit commit so the archive contains its own event.
        upload_engagement_export(slug, _build_export_payload(session, eng))
    else:
        session.commit()
        session.refresh(eng)
    return eng


@router.post("/engagements/{slug}/flush", status_code=204)
def flush_engagement(
    slug: str,
    session: DbSession,
    redis_client: RedisClient,
    _user: CurrentAdminUser,
) -> Response:
    """Permanently delete all engagement data. Export to blob first, then purge.

    Admin-only — hard delete is irreversible; non-guest users still get
    archive via DELETE /engagements/{slug}.
    """
    eng = _get_engagement_or_404(session, slug)
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
# Scope CRUD (nested under engagement)
# ---------------------------------------------------------------------------


@router.post(
    "/engagements/{slug}/scope",
    response_model=ScopeItemRead,
    status_code=status.HTTP_201_CREATED,
)
def create_scope_item(
    slug: str,
    body: ScopeItemCreate,
    session: DbSession,
    user: CurrentNonGuestUser,
) -> ScopeItem:
    eng = _get_engagement_or_404(session, slug)
    _reject_flushed(eng)
    item = ScopeItem(
        engagement_id=eng.id,
        kind=body.kind,
        value=body.value,
        is_exclusion=body.is_exclusion,
        note=body.note,
        source=body.source,
    )
    session.add(item)
    session.flush()
    session.add(
        AuditLog(
            engagement_id=eng.id,
            actor_type=ActorType.user,
            actor_id=str(user.id),
            event_type="scope.item.created",
            payload={"before": None, "after": _scope_audit_value(item)},
        )
    )
    session.commit()
    session.refresh(item)
    return item


@router.post(
    "/scope/parse",
    response_model=ScopeImportPreview,
)
def parse_scope_blob(body: ScopeImportRequest, _user: CurrentUser) -> ScopeImportPreview:
    """Pure parser — no engagement, no DB writes.

    Lets the /new wizard preview an import before the engagement exists.
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
        errors=[{"line": e.line, "raw": e.raw, "reason": e.reason} for e in errors],
        would_create=len(rows),
    )


@router.post(
    "/engagements/{slug}/scope/import",
    response_model=ScopeImportPreview | ScopeImportResult,
)
def import_scope(
    slug: str,
    body: ScopeImportRequest,
    session: DbSession,
    user: CurrentNonGuestUser,
    dry_run: bool = False,
) -> ScopeImportPreview | ScopeImportResult:
    """Bulk-import scope items from a free-form text blob.

    Same parser whether the analyst uploaded a file (client read it as text)
    or pasted into a textarea. ``?dry_run=true`` returns the preview without
    persisting; the UI calls it on each debounced keystroke. The real commit
    de-dupes against the engagement's existing (kind, value, is_exclusion)
    tuples so re-running an import is safe.
    """
    eng = _get_engagement_or_404(session, slug)
    if dry_run:
        _reject_only_flushed(eng)
    else:
        _reject_flushed(eng)
    rows, errors = parse_scope_text(body.text)

    error_rows = [{"line": e.line, "raw": e.raw, "reason": e.reason} for e in errors]

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
        session.execute(select(ScopeItem).where(ScopeItem.engagement_id == eng.id)).scalars()
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
            engagement_id=eng.id,
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
                engagement_id=eng.id,
                actor_type=ActorType.user,
                actor_id=str(user.id),
                event_type="scope.imported",
                payload={
                    "created_count": len(created),
                    "error_count": len(errors),
                    "duplicate_count": len(duplicates),
                    "changes": [
                        {"before": None, "after": _scope_audit_value(item)} for item in created
                    ],
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
    "/engagements/{slug}/scope",
    response_model=list[ScopeItemRead],
)
def list_scope(slug: str, session: DbSession, _user: CurrentUser) -> list[ScopeItem]:
    eng = _get_engagement_or_404(session, slug)
    rows = session.execute(
        select(ScopeItem).where(ScopeItem.engagement_id == eng.id).order_by(ScopeItem.created_at)
    ).scalars()
    return list(rows)


@router.patch(
    "/engagements/{slug}/scope/{scope_id}",
    response_model=ScopeItemRead,
)
def update_scope_item(
    slug: str,
    scope_id: uuid.UUID,
    body: ScopeItemUpdate,
    session: DbSession,
    user: CurrentNonGuestUser,
) -> ScopeItem:
    eng = _get_engagement_or_404(session, slug)
    _reject_flushed(eng)
    item = session.get(ScopeItem, scope_id)
    if item is None or item.engagement_id != eng.id:
        raise HTTPException(status_code=404, detail="scope item not found")
    session.refresh(item, with_for_update=True)
    before = _scope_audit_value(item)
    if body.value is not None:
        item.value = body.value
    if body.is_exclusion is not None:
        item.is_exclusion = body.is_exclusion
    if body.note is not None:
        item.note = body.note
    after = _scope_audit_value(item)
    if after != before:
        session.add(
            AuditLog(
                engagement_id=eng.id,
                actor_type=ActorType.user,
                actor_id=str(user.id),
                event_type="scope.item.updated",
                payload={"before": before, "after": after},
            )
        )
    session.commit()
    session.refresh(item)
    return item


@router.delete(
    "/engagements/{slug}/scope/{scope_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_scope_item(
    slug: str,
    scope_id: uuid.UUID,
    session: DbSession,
    user: CurrentNonGuestUser,
) -> Response:
    eng = _get_engagement_or_404(session, slug)
    _reject_flushed(eng)
    item = session.get(ScopeItem, scope_id)
    if item is None or item.engagement_id != eng.id:
        raise HTTPException(status_code=404, detail="scope item not found")
    session.refresh(item, with_for_update=True)
    before = _scope_audit_value(item)
    # v2.19.0: record the deletion so the Entities tab can flip previously
    # in-scope values from "live" to "legacy". Payload carries the exact
    # value string so downstream classifiers can match without a join.
    session.add(
        AuditLog(
            engagement_id=eng.id,
            actor_type=ActorType.user,
            actor_id=str(user.id),
            event_type="scope.item.deleted",
            payload={
                "scope_id": str(item.id),
                "kind": item.kind.value,
                "value": item.value,
                "is_exclusion": item.is_exclusion,
                "before": before,
                "after": None,
            },
        )
    )
    session.delete(item)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Findings (read-only; written by the worker)
# ---------------------------------------------------------------------------


_FINDING_SORTS = ("newest", "severity", "observed")

# Postgres has no native ordinal for the Severity enum (textual), so we
# project it to an int with CASE for "severity" sorts. critical first.
_SEVERITY_RANK = case(
    (Finding.severity == Severity.critical, 4),
    (Finding.severity == Severity.high, 3),
    (Finding.severity == Severity.medium, 2),
    (Finding.severity == Severity.low, 1),
    else_=0,
)


@router.get(
    "/engagements/{slug}/findings",
    response_model=list[FindingRead],
)
def list_findings(
    slug: str,
    session: DbSession,
    _user: CurrentUser,
    phase: Annotated[FindingPhase | None, Query(description="Filter by phase.")] = None,
    status: Annotated[
        FindingStatus | None, Query(description="Filter by validation status.")
    ] = None,
    sort: Annotated[
        str,
        Query(
            description=(
                "Sort order. 'newest' (default) = created_at desc; "
                "'severity' = critical→info then newest; "
                "'observed' = observed_at desc, NULLs last, then newest."
            ),
        ),
    ] = "newest",
    limit: Annotated[
        int | None,
        Query(
            ge=1,
            le=500,
            description=("Optional page size. Omit to preserve the current full-list UI behavior."),
        ),
    ] = None,
    offset: Annotated[
        int,
        Query(ge=0, description="Rows to skip when limit is provided."),
    ] = 0,
) -> list[dict[str, Any]]:
    if sort not in _FINDING_SORTS:
        raise HTTPException(
            status_code=400,
            detail=f"invalid sort '{sort}'; must be one of {_FINDING_SORTS}",
        )
    eng = _get_engagement_or_404(session, slug)
    stmt = select(Finding).where(
        Finding.engagement_id == eng.id,
        Finding.deleted_at.is_(None),
    )
    if phase is not None:
        stmt = stmt.where(Finding.phase == phase)
    if status is not None:
        stmt = stmt.where(Finding.status == status)

    if sort == "severity":
        stmt = stmt.order_by(_SEVERITY_RANK.desc(), Finding.created_at.desc())
    elif sort == "observed":
        # Postgres puts NULLs LAST when using DESC by default; explicit
        # nullslast() for clarity. Tie-break on created_at so the result
        # is stable for findings that share an observed_at.
        stmt = stmt.order_by(Finding.observed_at.desc().nullslast(), Finding.created_at.desc())
    else:  # newest
        stmt = stmt.order_by(Finding.created_at.desc())

    # Backend pagination groundwork for large engagements. The current UI still
    # omits limit so behavior is unchanged; API clients can opt in now.
    if limit is not None:
        stmt = stmt.offset(offset).limit(limit)

    rows = session.execute(stmt).scalars()
    return [_finding_to_read(f) for f in rows]


@router.get(
    "/engagements/{slug}/entities",
    response_model=list[EntityRead],
)
def list_entities(
    slug: str,
    session: DbSession,
    redis: RedisClient,
    _user: CurrentUser,
    type: Annotated[str | None, Query(description="Filter by entity type.")] = None,
    q: Annotated[str | None, Query(description="Substring match on the value.")] = None,
) -> list[dict[str, Any]]:
    """Entities correlated across this engagement's findings (CHARTER Idea 4).

    v0.30.0: derived entities are expensive to compute (regex over every
    finding's content) and this endpoint is polled by the Entities tab.
    Cache the full extraction keyed on a findings fingerprint
    (count + max updated_at) so it auto-invalidates the instant a finding
    changes — no invalidation hooks. ``type``/``q`` filter the cached set
    in-memory (cheap).
    """
    eng = _get_engagement_or_404(session, slug)
    full = _cached_derived_entities(session, redis, eng.id)
    # v2.19.0: annotate outside the cache so scope mutations show up
    # instantly on the next request without invalidating the (heavy) entity
    # extraction cache. Both queries are cheap (typical engagement has
    # ~10-100 scope items and ~0 retired values).
    scope_items = list(
        session.execute(select(ScopeItem).where(ScopeItem.engagement_id == eng.id)).scalars()
    )
    retired_values = {
        v.strip().lower()
        for v in session.execute(
            select(AuditLog.payload["value"].astext).where(
                AuditLog.engagement_id == eng.id,
                AuditLog.event_type == "scope.item.deleted",
            )
        ).scalars()
        if v
    }
    result = annotate_scope_status(
        list(full),
        current_scope_items=scope_items,
        retired_scope_values=retired_values,
    )
    if type:
        result = [e for e in result if e.get("type") == type]
    if q:
        needle = q.lower()
        result = [e for e in result if needle in str(e.get("value") or "").lower()]
    return result


def _cached_derived_entities(
    session: DbSession, redis: RedisClient, engagement_id: uuid.UUID
) -> list[dict[str, Any]]:
    """Full derived-entity list for an engagement, cached on a findings fingerprint."""
    count, max_updated = session.execute(
        select(func.count(Finding.id), func.max(Finding.updated_at)).where(
            Finding.engagement_id == engagement_id,
            Finding.deleted_at.is_(None),
        )
    ).one()
    fingerprint = f"{count or 0}:{max_updated.isoformat() if max_updated else 'none'}"
    key = f"entities:{engagement_id}:{fingerprint}"
    cached = redis.get(key)
    if cached:
        try:
            return json.loads(cached)
        except (TypeError, ValueError):
            pass  # corrupt entry — recompute below
    findings = list(
        session.execute(
            select(Finding)
            .where(
                Finding.engagement_id == engagement_id,
                Finding.deleted_at.is_(None),
            )
            .order_by(Finding.created_at)
        ).scalars()
    )
    full = extract_entities(findings)
    with contextlib.suppress(Exception):
        redis.set(key, json.dumps(full, default=str), ex=300)
    return full


@router.post(
    "/findings/{finding_id}/validate",
    response_model=FindingRead,
)
def validate_finding(
    finding_id: uuid.UUID,
    body: FindingValidate,
    session: DbSession,
    user: CurrentNonGuestUser,
) -> dict[str, Any]:
    """Promote/reject a pending finding. ``validated`` makes it report-eligible;
    ``rejected`` / ``false_positive`` keep it for audit but exclude it."""
    finding = _lock_active_finding_for_mutation(session, finding_id)

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
            engagement_id=finding.engagement_id,
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
# Observations
# ---------------------------------------------------------------------------


def _observation_to_read(
    obs: Observation, finding_ids: list[uuid.UUID] | None = None
) -> dict[str, Any]:
    """Project an Observation ORM row into the wire shape, attaching the
    findings it references. ``finding_ids`` is passed in (batched at the
    list layer) to avoid an N+1 per observation."""
    return {
        "id": obs.id,
        "content": obs.content,
        "phase": obs.phase,
        "created_by": obs.created_by,
        "created_at": obs.created_at,
        "finding_ids": list(finding_ids or []),
    }


def _observation_finding_ids(
    session: DbSession, observation_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[uuid.UUID]]:
    """Batched lookup: observation_id -> [finding_id, ...] it references."""
    if not observation_ids:
        return {}
    rows = session.execute(
        select(ObservationFindingLink.observation_id, ObservationFindingLink.finding_id)
        .where(ObservationFindingLink.observation_id.in_(observation_ids))
        .order_by(ObservationFindingLink.created_at)
    ).all()
    out: dict[uuid.UUID, list[uuid.UUID]] = {}
    for obs_id, finding_id in rows:
        out.setdefault(obs_id, []).append(finding_id)
    return out


@router.get("/engagements/{slug}/observations", response_model=list[ObservationRead])
def list_observations(slug: str, session: DbSession, _user: CurrentUser) -> list[dict[str, Any]]:
    eng = _get_engagement_or_404(session, slug)
    rows = list(
        session.execute(
            select(Observation)
            .where(Observation.engagement_id == eng.id)
            .order_by(Observation.created_at)
        ).scalars()
    )
    finding_ids = _observation_finding_ids(session, [o.id for o in rows])
    return [_observation_to_read(o, finding_ids.get(o.id, [])) for o in rows]


@router.post(
    "/engagements/{slug}/observations",
    response_model=ObservationRead,
    status_code=status.HTTP_201_CREATED,
)
def create_observation(
    slug: str,
    body: ObservationCreate,
    session: DbSession,
    user: CurrentNonGuestUser,
) -> dict[str, Any]:
    eng = _get_engagement_or_404(session, slug)
    _reject_flushed(eng)
    obs = Observation(
        engagement_id=eng.id,
        content=body.content,
        phase=body.phase,
        created_by=user.id,
    )
    session.add(obs)
    session.commit()
    session.refresh(obs)
    return _observation_to_read(obs, [])


@router.delete("/observations/{observation_id}", status_code=204)
def delete_observation(
    observation_id: uuid.UUID,
    session: DbSession,
    _user: CurrentNonGuestUser,
) -> Response:
    obs = session.get(Observation, observation_id)
    if obs is None:
        raise HTTPException(status_code=404, detail="observation not found")
    _reject_engagement_id(session, obs.engagement_id)
    session.delete(obs)
    session.commit()
    return Response(status_code=204)


# ── observation ↔ finding links (v1.4.8) ──────────────────────────────


def _engagement_for_observation(session: DbSession, observation_id: uuid.UUID) -> uuid.UUID:
    eng_id = session.execute(
        select(Observation.engagement_id).where(Observation.id == observation_id)
    ).scalar_one_or_none()
    if eng_id is None:
        raise HTTPException(status_code=404, detail="observation not found")
    return eng_id


@router.post(
    "/observations/{observation_id}/findings/{finding_id}",
    response_model=ObservationRead,
    status_code=status.HTTP_201_CREATED,
)
def link_observation_to_finding(
    observation_id: uuid.UUID,
    finding_id: uuid.UUID,
    session: DbSession,
    _user: CurrentNonGuestUser,
) -> dict[str, Any]:
    """Attach an observation to a finding it supports. Idempotent — a
    repeat call returns the observation with the link already present."""
    obs_eng = _engagement_for_observation(session, observation_id)
    _reject_engagement_id(session, obs_eng)
    finding = lock_active_finding_or_404(session, finding_id)
    # Cross-engagement links make no sense and would confuse the report.
    if finding.engagement_id != obs_eng:
        raise HTTPException(
            status_code=400,
            detail="observation and finding belong to different engagements",
        )
    existing = session.get(
        ObservationFindingLink,
        {"observation_id": observation_id, "finding_id": finding_id},
    )
    if existing is None:
        session.add(ObservationFindingLink(observation_id=observation_id, finding_id=finding_id))
        session.commit()
    obs = session.get(Observation, observation_id)
    assert obs is not None
    return _observation_to_read(
        obs, _observation_finding_ids(session, [observation_id]).get(observation_id, [])
    )


@router.delete(
    "/observations/{observation_id}/findings/{finding_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def unlink_observation_from_finding(
    observation_id: uuid.UUID,
    finding_id: uuid.UUID,
    session: DbSession,
    _user: CurrentNonGuestUser,
) -> Response:
    """Remove an observation→finding reference. Idempotent — 204 whether or
    not the link existed."""
    obs_eng = _engagement_for_observation(session, observation_id)
    _reject_engagement_id(session, obs_eng)
    finding = lock_active_finding_or_404(session, finding_id)
    if finding.engagement_id != obs_eng:
        raise HTTPException(
            status_code=400,
            detail="observation and finding belong to different engagements",
        )
    link = session.get(
        ObservationFindingLink,
        {"observation_id": observation_id, "finding_id": finding_id},
    )
    if link is not None:
        session.delete(link)
        session.commit()
    return Response(status_code=204)


@router.get("/findings/{finding_id}/observations", response_model=list[ObservationRead])
def list_observations_for_finding(
    finding_id: uuid.UUID, session: DbSession, _user: CurrentUser
) -> list[dict[str, Any]]:
    """Back-references for the finding slide-over — the observations that
    reference this finding. Fetched on open (like attachments) so the
    findings list stays N+1-free."""
    get_active_finding_or_404(session, finding_id)
    obs_ids = [
        row[0]
        for row in session.execute(
            select(ObservationFindingLink.observation_id).where(
                ObservationFindingLink.finding_id == finding_id
            )
        ).all()
    ]
    if not obs_ids:
        return []
    rows = list(
        session.execute(
            select(Observation).where(Observation.id.in_(obs_ids)).order_by(Observation.created_at)
        ).scalars()
    )
    finding_ids = _observation_finding_ids(session, [o.id for o in rows])
    return [_observation_to_read(o, finding_ids.get(o.id, [])) for o in rows]


# ---------------------------------------------------------------------------
# Findings import
# ---------------------------------------------------------------------------


MAX_FINDING_IMPORT_BATCH = 500
MAX_FINDING_IMPORT_DETAILS_BYTES = 256 * 1024
MAX_FINDING_IMPORT_BATCH_DETAILS_BYTES = 5 * 1024 * 1024


class FindingImport(BaseModel):
    """Single bounded finding in a generic bulk import payload."""

    title: str = Field(min_length=1, max_length=300)
    severity: Severity = Severity.info
    phase: FindingPhase = FindingPhase.general
    summary: str | None = Field(default=None, max_length=MAX_FINDING_SUMMARY_CHARS)
    target: str | None = Field(default=None, min_length=1, max_length=500)
    source_tool: str | None = Field(default="import", min_length=1, max_length=120)
    details: dict[str, Any] = Field(default_factory=dict)
    observed_at: datetime | None = None
    burp_serial_number: str | None = Field(default=None, min_length=1, max_length=64)
    # v1.4.0: optional grouping. Callers that want Nessus-style rows
    # (multiple hits under one parent) stamp their own key, e.g.
    # "csv:cve-2024-1234" or "sca:log4j". Null = per-hit row, the old
    # behavior. See docs/FINDINGS_GROUPING.md.
    group_key: str | None = Field(default=None, min_length=1, max_length=200)
    tags: list[str] = Field(default_factory=list, max_length=MAX_FINDING_TAGS)

    @field_validator("title", "target", "source_tool", "burp_serial_number", "group_key")
    @classmethod
    def _strip_nonblank_strings(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("value cannot be blank")
        return normalized

    @field_validator("tags")
    @classmethod
    def _normalize_import_tags(cls, value: list[str]) -> list[str]:
        return _normalize_tags(value)

    @model_validator(mode="after")
    def _bound_details(self) -> FindingImport:
        encoded_size = len(json.dumps(self.details, separators=(",", ":"), default=str).encode())
        if encoded_size > MAX_FINDING_IMPORT_DETAILS_BYTES:
            raise ValueError(
                f"details exceeds the {MAX_FINDING_IMPORT_DETAILS_BYTES:,}-byte per-finding limit"
            )
        return self


class FindingImportBatch(RootModel[list[FindingImport]]):
    root: list[FindingImport] = Field(max_length=MAX_FINDING_IMPORT_BATCH)

    @model_validator(mode="after")
    def _bound_total_details(self) -> FindingImportBatch:
        total = sum(
            len(json.dumps(item.details, separators=(",", ":"), default=str).encode())
            for item in self.root
        )
        if total > MAX_FINDING_IMPORT_BATCH_DETAILS_BYTES:
            raise ValueError(
                f"details exceed the {MAX_FINDING_IMPORT_BATCH_DETAILS_BYTES:,}-byte batch limit"
            )
        return self


class NessusImportResult(BaseModel):
    """Response shape for the .nessus XML importer.

    ``total_items`` is every ReportItem the parser saw; ``imported`` is
    the subset that survived the Info filter + scope filter and now has
    a Finding row. The ``skipped_*`` counters let the analyst sanity-
    check the filters dropped what they expected.
    """

    imported: list[FindingRead]
    skipped_info: int
    skipped_out_of_scope: int
    total_items: int


class NmapImportResult(BaseModel):
    """Response shape for an Nmap ``-oX`` import."""

    imported: list[FindingRead]
    total_ports: int
    skipped_closed: int
    skipped_out_of_scope: int
    observed_at: datetime | None = None


class BurpImportResult(BaseModel):
    """Response shape for the Burp Pro Issue Export XML importer.

    ``skipped_duplicate`` counts <issue> rows whose <serialNumber> was
    already present in this engagement — re-importing the same export
    or a re-scan that emits the same serials is a no-op for those rows.
    """

    imported: list[FindingRead]
    skipped_info: int
    skipped_out_of_scope: int
    skipped_duplicate: int
    total_items: int
    export_time: datetime | None = None


class ScannerScopeReasonRead(BaseModel):
    code: str
    count: int
    message: str


class ScannerPreviewGroupRead(BaseModel):
    selection_key: str
    title: str
    severity: Severity
    phase: FindingPhase
    item_count: int
    target_count: int
    targets: list[str]
    targets_truncated: bool
    scope_decision: str
    scope_reasons: list[ScannerScopeReasonRead]
    in_scope_item_count: int
    out_of_scope_item_count: int
    duplicate_state: Literal["new", "partial", "existing"]
    duplicate_item_count: int
    default_selected: bool


class ScannerImportPreviewRead(BaseModel):
    source: Literal["nessus", "burp", "nmap"]
    file_sha256: str
    total_source_rows: int
    groups: list[ScannerPreviewGroupRead]
    counts: dict[str, int]
    parser_counts: dict[str, int]
    # v2.7.0: false for Burp — the commit accepts every selected row
    # regardless of scope so third-party assets Burp trips over end up
    # in the analyst's queue for manual review.
    scope_enforced: bool = True


class ScannerImportCommitResult(BaseModel):
    source: Literal["nessus", "burp", "nmap"]
    file_sha256: str
    selected_group_count: int
    selected_item_count: int
    skipped_out_of_scope: int
    skipped_duplicate: int
    imported: list[FindingRead]
    parser_counts: dict[str, int]


def _create_findings_from_imports(
    session: Any,
    eng: Engagement,
    items: list[Any],
    user: Any,
    *,
    source: str,
) -> tuple[list[Finding], int]:  # noqa: PLR0912, C901 — dispatch mixing grouped + per-hit + Burp dedup
    """Persist a list of import-shaped items as Findings + write the audit row.

    ``items`` is duck-typed: each must expose ``title``, ``severity``,
    ``phase``, ``summary``, ``target``, ``source_tool``, ``details``.
    Optional: ``observed_at`` (scan-side timestamp) and
    ``burp_serial_number`` (Burp dedup key). ``FindingImport`` (Phase 11
    JSON/CSV importer), ``nessus_import.ParsedItem`` (Phase 10 .nessus
    parser), and ``burp_import.ParsedItem`` (v0.7 Burp Pro parser) all
    satisfy this protocol.

    Dedup: when an item carries a ``burp_serial_number``, the helper
    skips it if the same (engagement_id, burp_serial_number) pair
    already exists. Returns ``(created, skipped_duplicate_count)`` so
    importers can surface the dedup count on the response.

    Phase-based validation gate (refined 2026-06-26): ``osint``-phase
    imports auto-validate at creation because the results are factual.
    Non-osint phases (``vuln_scan``, ``exploit``, ``phishing``, ``general``)
    stay ``pending_validation`` for analyst review before the report
    includes them. See ``default_status_for_phase`` for the rule. Caller
    commits the session.
    """
    from datetime import UTC, datetime

    from app.models.finding import default_status_for_phase

    # Pre-load existing Burp serials for this engagement so we dedup in
    # one query instead of one per row.
    incoming_serials = {
        getattr(item, "burp_serial_number", None)
        for item in items
        if getattr(item, "burp_serial_number", None)
    }
    existing_serials: set[str] = set()
    if incoming_serials:
        existing_serials = {
            row[0]
            for row in session.execute(
                select(Finding.burp_serial_number).where(
                    Finding.engagement_id == eng.id,
                    Finding.burp_serial_number.in_(incoming_serials),
                    Finding.deleted_at.is_(None),
                )
            ).all()
            if row[0]
        }

    from app.services.finding_grouping import (
        canonical_import_group_key,
        upsert_grouped_import_item,
    )

    created: list[Finding] = []
    feedback_candidates: list[tuple[Finding, str]] = []
    seen_created_ids: set[uuid.UUID] = set()
    skipped_duplicate = 0
    now = datetime.now(tz=UTC)
    for item in items:
        serial = getattr(item, "burp_serial_number", None)
        if serial and serial in existing_serials:
            skipped_duplicate += 1
            continue
        status = default_status_for_phase(item.phase)

        # v1.4.0: Nessus-style grouping — if the parser stamped a
        # group_key on the ParsedItem, fold this row into the shared
        # parent for its plugin_id / issue-type instead of creating a
        # separate Finding.
        group_key = getattr(item, "group_key", None)
        if group_key:
            group_key = canonical_import_group_key(item.source_tool or source, group_key)
            row, added = upsert_grouped_import_item(
                session,
                engagement_id=eng.id,
                group_key=group_key,
                source_tool=item.source_tool or "import",
                item_title=item.title,
                item_severity=item.severity,
                item_target=item.target,
                item_details=item.details,
                phase=item.phase,
                status=status,
                validated_by=user.id if status == FindingStatus.validated else None,
                burp_serial_number=serial,
                item_tags=getattr(item, "tags", None),
            )
            if not added:
                skipped_duplicate += 1
            else:
                feedback_candidates.append((row, "finding.updated"))
            if row.id not in seen_created_ids:
                created.append(row)
                seen_created_ids.add(row.id)
            if serial:
                existing_serials.add(serial)
            continue

        f = Finding(
            engagement_id=eng.id,
            title=item.title,
            severity=item.severity,
            phase=item.phase,
            summary=item.summary,
            target=item.target,
            source_tool=item.source_tool or "import",
            details=item.details,
            status=status,
            validated_at=now if status == FindingStatus.validated else None,
            validated_by=user.id if status == FindingStatus.validated else None,
            observed_at=getattr(item, "observed_at", None),
            burp_serial_number=serial,
            tags=list(getattr(item, "tags", None) or []),
        )
        session.add(f)
        created.append(f)
        feedback_candidates.append((f, "finding.created"))
        if serial:
            existing_serials.add(serial)  # dedup within the same batch too
    if created:
        session.add(
            AuditLog(
                engagement_id=eng.id,
                actor_type=ActorType.user,
                actor_id=str(user.id),
                event_type="findings.imported",
                payload={
                    "count": len(created),
                    "source": source,
                    "skipped_duplicate": skipped_duplicate,
                },
            )
        )
        from app.services.finding_feedback import stage_finding_feedback

        operation_id = uuid.uuid4()
        session.flush()
        by_id = {row.id: (row, event_type) for row, event_type in feedback_candidates}
        for finding, event_type in by_id.values():
            entry = stage_finding_feedback(
                session,
                finding=finding,
                acting_user_id=user.id,
                operation_id=operation_id,
                source=source,
                event_type=event_type,
                tool=finding.source_tool,
            )
            finding._feedback_outbox_id = entry.id  # type: ignore[attr-defined]
    return created, skipped_duplicate


def _publish_import_feedback(
    session: Any,
    redis_client: Any,
    findings: list[Finding],
) -> None:
    """Best-effort immediate delivery; committed SQL outbox owns retry."""
    from app.services.finding_feedback import publish_feedback_entries

    publish_feedback_entries(
        session,
        redis_client,
        [entry_id for row in findings if (entry_id := getattr(row, "_feedback_outbox_id", None))],
    )


@router.post(
    "/engagements/{slug}/findings/import",
    response_model=list[FindingRead],
    status_code=status.HTTP_201_CREATED,
)
def import_findings(
    slug: str,
    body: FindingImportBatch,
    session: DbSession,
    redis_client: RedisClient,
    user: CurrentNonGuestUser,
) -> list[dict[str, Any]]:
    """Bulk-import findings from an external source (scanner output, prior report, etc.).

    All imported findings land as ``pending_validation`` so the analyst can
    review before they become report-eligible. ``source_tool`` defaults to
    ``'import'`` if omitted.
    """
    if not body.root:
        return []

    eng = _get_engagement_or_404(session, slug)
    _reject_flushed(eng)

    from app.services.finding_grouping import FindingTagCapacityError

    try:
        created, _skipped = _create_findings_from_imports(
            session, eng, body.root, user, source="bulk_import"
        )
    except FindingTagCapacityError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    for f in created:
        session.refresh(f)
    _publish_import_feedback(session, redis_client, created)
    return [_finding_to_read(f) for f in created]


@router.post(
    "/engagements/{slug}/findings/import/nessus",
    response_model=NessusImportResult,
    status_code=status.HTTP_201_CREATED,
)
def import_nessus(
    slug: str,
    session: DbSession,
    redis_client: RedisClient,
    user: CurrentNonGuestUser,
    file: Annotated[UploadFile, File(..., description="Nessus .nessus v2 XML export.")],
    include_info: Annotated[
        bool,
        Query(description="Import Severity=Info findings. Default False."),
    ] = False,
) -> dict[str, Any]:
    """Import a Tenable Nessus .nessus v2 XML export.

    Each ReportItem becomes a Finding with ``phase=vuln_scan`` and
    ``status=pending_validation`` (analyst must approve before report).
    ``include_info=true`` opts in to Severity=Info rows; default off.
    Out-of-scope hosts are dropped silently and counted on the response.
    """
    from app.services.nessus_import import parse_nessus_xml

    eng = _get_engagement_or_404(session, slug)
    _reject_flushed(eng)

    raw = _read_scanner_upload(file)
    scope_items = list(
        session.execute(select(ScopeItem).where(ScopeItem.engagement_id == eng.id)).scalars()
    )
    try:
        result = parse_nessus_xml(raw, include_info=include_info, scope_items=scope_items)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    created, _skipped = _create_findings_from_imports(
        session, eng, result.items, user, source="nessus_import"
    )
    session.commit()
    for f in created:
        session.refresh(f)
    _publish_import_feedback(session, redis_client, created)

    return {
        "imported": [_finding_to_read(f) for f in created],
        "skipped_info": result.skipped_info,
        "skipped_out_of_scope": result.skipped_out_of_scope,
        "total_items": result.total_items,
    }


@router.post(
    "/engagements/{slug}/findings/import/nmap",
    response_model=NmapImportResult,
    status_code=status.HTTP_201_CREATED,
)
def import_nmap(
    slug: str,
    session: DbSession,
    redis_client: RedisClient,
    user: CurrentNonGuestUser,
    file: Annotated[UploadFile, File(..., description="Nmap -oX XML export.")],
) -> dict[str, Any]:
    """Import open services from an analyst-supplied Nmap XML export."""
    from app.services.nmap_import import parse_nmap_xml

    eng = _get_engagement_or_404(session, slug)
    _reject_flushed(eng)
    raw = _read_scanner_upload(file)
    scope_items = list(
        session.execute(select(ScopeItem).where(ScopeItem.engagement_id == eng.id)).scalars()
    )
    try:
        result = parse_nmap_xml(raw, scope_items=scope_items)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    created, _skipped = _create_findings_from_imports(
        session, eng, result.items, user, source="nmap_import"
    )
    session.commit()
    for finding in created:
        session.refresh(finding)
    _publish_import_feedback(session, redis_client, created)
    return {
        "imported": [_finding_to_read(finding) for finding in created],
        "total_ports": result.total_ports,
        "skipped_closed": result.skipped_closed,
        "skipped_out_of_scope": result.skipped_out_of_scope,
        "observed_at": result.observed_at,
    }


@router.post(
    "/engagements/{slug}/findings/import/burp",
    response_model=BurpImportResult,
    status_code=status.HTTP_201_CREATED,
)
def import_burp(
    slug: str,
    session: DbSession,
    redis_client: RedisClient,
    user: CurrentNonGuestUser,
    file: Annotated[
        UploadFile,
        File(..., description="Burp Suite Pro Issue Export XML."),
    ],
    include_info: Annotated[
        bool,
        Query(description="Import Severity=Information issues. Default False."),
    ] = False,
) -> dict[str, Any]:
    """Import a Burp Pro Issue Export XML file.

    Each ``<issue>`` becomes a Finding with ``phase=vuln_scan`` and
    ``status=pending_validation`` (analyst must approve before report).
    Dedup is by ``<serialNumber>`` against this engagement's existing
    findings — re-importing the same XML is a no-op for already-imported
    issues. Out-of-scope hosts are dropped silently and counted.
    """
    from app.services.burp_import import parse_burp_xml

    eng = _get_engagement_or_404(session, slug)
    _reject_flushed(eng)

    raw = _read_scanner_upload(file)
    scope_items = list(
        session.execute(select(ScopeItem).where(ScopeItem.engagement_id == eng.id)).scalars()
    )
    try:
        result = parse_burp_xml(raw, include_info=include_info, scope_items=scope_items)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    created, skipped_duplicate = _create_findings_from_imports(
        session, eng, result.items, user, source="burp_import"
    )
    session.commit()
    for f in created:
        session.refresh(f)
    _publish_import_feedback(session, redis_client, created)

    return {
        "imported": [_finding_to_read(f) for f in created],
        "skipped_info": result.skipped_info,
        "skipped_out_of_scope": result.skipped_out_of_scope,
        "skipped_duplicate": skipped_duplicate,
        "total_items": result.total_items,
        "export_time": result.export_time,
    }


ScannerSourceParam = Literal["nessus", "burp", "nmap"]


def _read_scanner_upload(file: UploadFile) -> bytes:
    from app.services.scanner_import import MAX_SCANNER_EXPORT_BYTES

    raw = file.file.read(MAX_SCANNER_EXPORT_BYTES + 1)
    if len(raw) > MAX_SCANNER_EXPORT_BYTES:
        mb = MAX_SCANNER_EXPORT_BYTES // (1024 * 1024)
        raise HTTPException(
            status_code=413,
            detail=f"scanner export exceeds the {mb} MB limit",
        )
    return raw


def _scanner_duplicate_index(
    session: Any,
    engagement_id: uuid.UUID,
    source: ScannerSourceParam,
) -> Any:
    from app.services.finding_grouping import import_item_dedup_key
    from app.services.scanner_import import DuplicateIndex

    rows = session.execute(
        select(
            Finding.group_key,
            Finding.details,
            Finding.burp_serial_number,
            Finding.deleted_at,
        ).where(
            Finding.engagement_id == engagement_id,
            Finding.source_tool == f"{source}_import",
        )
    ).all()
    group_dedup_keys: dict[str, set[str]] = {}
    burp_serials: set[str] = set()
    for group_key, details, serial, deleted_at in rows:
        # Persistence revives a soft-deleted grouped parent. Treat its old
        # observations as importable so a selected commit reaches that path.
        if deleted_at is not None:
            continue
        if serial:
            burp_serials.add(serial)
        if not group_key:
            continue
        dedup_keys = group_dedup_keys.setdefault(group_key, set())
        payload = details if isinstance(details, dict) else {}
        for item in payload.get("items") or []:
            if not isinstance(item, dict):
                continue
            dedup_keys.add(import_item_dedup_key(f"{source}_import", item))
            item_serial = item.get("burp_serial_number")
            if source == "burp" and item_serial:
                burp_serials.add(str(item_serial))
    return DuplicateIndex(
        group_dedup_keys={key: frozenset(values) for key, values in group_dedup_keys.items()},
        burp_serials=frozenset(burp_serials),
    )


def _scanner_preview_to_dict(preview: Any) -> dict[str, Any]:
    return {
        "source": preview.source,
        "file_sha256": preview.file_sha256,
        "total_source_rows": preview.total_source_rows,
        "groups": [
            {
                "selection_key": group.selection_key,
                "title": group.title,
                "severity": group.severity,
                "phase": group.phase,
                "item_count": group.item_count,
                "target_count": group.target_count,
                "targets": list(group.targets),
                "targets_truncated": group.targets_truncated,
                "scope_decision": group.scope_decision,
                "scope_reasons": [
                    {"code": reason.code, "count": reason.count, "message": reason.message}
                    for reason in group.scope_reasons
                ],
                "in_scope_item_count": group.in_scope_item_count,
                "out_of_scope_item_count": group.out_of_scope_item_count,
                "duplicate_state": group.duplicate_state,
                "duplicate_item_count": group.duplicate_item_count,
                "default_selected": group.default_selected,
            }
            for group in preview.groups
        ],
        "counts": dict(preview.counts),
        "parser_counts": dict(preview.parser_counts),
        "scope_enforced": preview.scope_enforced,
    }


@router.post(
    "/engagements/{slug}/findings/import/{source}/preview",
    response_model=ScannerImportPreviewRead,
)
def preview_scanner_import(
    slug: str,
    source: ScannerSourceParam,
    session: DbSession,
    user: CurrentNonGuestUser,
    file: Annotated[UploadFile, File(..., description="Scanner XML export to preview.")],
    include_info: Annotated[
        bool,
        Query(description="Select informational groups by default."),
    ] = False,
) -> dict[str, Any]:
    """Safely parse a scanner export and return selectable groups without writes."""
    from app.services.scanner_import import build_scanner_preview

    eng = _get_engagement_or_404(session, slug)
    _reject_only_flushed(eng)
    raw = _read_scanner_upload(file)
    scope_items = list(
        session.execute(select(ScopeItem).where(ScopeItem.engagement_id == eng.id)).scalars()
    )
    duplicate_index = _scanner_duplicate_index(session, eng.id, source)
    try:
        preview = build_scanner_preview(
            source,
            raw,
            scope_items=scope_items,
            duplicate_index=duplicate_index,
            include_info_by_default=include_info,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _scanner_preview_to_dict(preview)


@router.post(
    "/engagements/{slug}/findings/import/{source}/commit",
    response_model=ScannerImportCommitResult,
    status_code=status.HTTP_201_CREATED,
)
def commit_scanner_import(
    slug: str,
    source: ScannerSourceParam,
    session: DbSession,
    redis_client: RedisClient,
    user: CurrentNonGuestUser,
    file: Annotated[UploadFile, File(..., description="Same scanner XML export used for preview.")],
    file_sha256: Annotated[str, Form(..., description="SHA-256 returned by preview.")],
    selected_group_keys: Annotated[
        str,
        Form(..., description="JSON array of preview selection keys."),
    ],
) -> dict[str, Any]:
    """Reparse a previewed file and persist only explicitly selected groups."""
    from app.services.scanner_import import (
        MAX_SCANNER_GROUPS,
        MAX_SELECTION_FORM_BYTES,
        prepare_scanner_commit,
        scanner_file_sha256,
    )

    eng = _get_engagement_or_404(session, slug)
    _reject_flushed(eng)
    raw = _read_scanner_upload(file)
    if len(selected_group_keys.encode()) > MAX_SELECTION_FORM_BYTES:
        raise HTTPException(status_code=413, detail="scanner selection metadata is too large")
    try:
        decoded_keys = json.loads(selected_group_keys)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=400,
            detail="selected_group_keys must be a JSON array",
        ) from exc
    if not isinstance(decoded_keys, list) or any(not isinstance(key, str) for key in decoded_keys):
        raise HTTPException(
            status_code=400,
            detail="selected_group_keys must be a JSON array of strings",
        )
    if len(decoded_keys) > MAX_SCANNER_GROUPS:
        raise HTTPException(
            status_code=400,
            detail=f"selected_group_keys exceeds the {MAX_SCANNER_GROUPS:,}-group limit",
        )
    selected_keys = set(decoded_keys)
    if len(selected_keys) != len(decoded_keys):
        raise HTTPException(
            status_code=400,
            detail="selected_group_keys must not contain duplicates",
        )

    scope_items = list(
        session.execute(select(ScopeItem).where(ScopeItem.engagement_id == eng.id)).scalars()
    )
    duplicate_index = _scanner_duplicate_index(session, eng.id, source)
    try:
        prepared = prepare_scanner_commit(
            source,
            raw,
            expected_sha256=file_sha256,
            selected_group_keys=selected_keys,
            scope_items=scope_items,
            duplicate_index=duplicate_index,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    created, race_duplicates = _create_findings_from_imports(
        session,
        eng,
        list(prepared.items),
        user,
        source=f"{source}_import",
    )
    if prepared.items:
        sorted_selection = sorted(selected_keys)
        selection_digest = scanner_file_sha256(
            json.dumps(sorted_selection, separators=(",", ":")).encode()
        )
        session.add(
            AuditLog(
                engagement_id=eng.id,
                actor_type=ActorType.user,
                actor_id=str(user.id),
                event_type="scanner_import.committed",
                payload={
                    "source": source,
                    "file_sha256": prepared.file_sha256,
                    "selected_group_keys": sorted_selection[:100],
                    "selected_group_keys_truncated": len(sorted_selection) > 100,
                    "selection_sha256": selection_digest,
                    "selected_group_count": prepared.selected_group_count,
                    "selected_item_count": prepared.selected_item_count,
                    "skipped_out_of_scope": prepared.skipped_out_of_scope,
                    "skipped_duplicate": prepared.skipped_duplicate + race_duplicates,
                    "affected_finding_count": len(created),
                },
            )
        )
    session.commit()
    for finding in created:
        session.refresh(finding)
    _publish_import_feedback(session, redis_client, created)
    return {
        "source": source,
        "file_sha256": prepared.file_sha256,
        "selected_group_count": prepared.selected_group_count,
        "selected_item_count": prepared.selected_item_count,
        "skipped_out_of_scope": prepared.skipped_out_of_scope,
        "skipped_duplicate": prepared.skipped_duplicate + race_duplicates,
        "imported": [_finding_to_read(finding) for finding in created],
        "parser_counts": dict(prepared.parser_counts),
    }


# ---------------------------------------------------------------------------
# Finding update (title / summary / severity / phase)
# ---------------------------------------------------------------------------


def _record_finding_summary(
    session: Any,
    finding: Finding,
    body_text: str,
    author_user_id: uuid.UUID,
) -> FindingSummary:
    """Append a summary entry to history AND refresh the cached body.

    Insert path used by both PATCH /findings/{id} (back-compat) and
    POST /findings/{id}/summaries (the v0.7.0 slide-over). Caller commits.
    """
    entry = FindingSummary(
        finding_id=finding.id,
        body=body_text,
        author_user_id=author_user_id,
    )
    session.add(entry)
    # Keep findings.summary as the denormalized cache of the latest body
    # so downstream consumers (Report tab, JSON export, MCP server)
    # don't need to join.
    finding.summary = body_text
    return entry


@router.patch(
    "/findings/{finding_id}",
    response_model=FindingRead,
)
def update_finding(
    finding_id: uuid.UUID,
    body: FindingUpdate,
    session: DbSession,
    user: CurrentNonGuestUser,
) -> dict[str, Any]:
    """Edit analyst-controlled fields on a finding. Only provided fields change;
    omitted fields are left as-is. ``summary`` accepts ``null`` to clear it.

    When ``summary`` is set to a non-empty string, an entry is also
    appended to the finding's summary history. Setting it to ``null``
    or ``""`` just clears the cached body without recording history.
    """
    finding = _lock_active_finding_for_mutation(session, finding_id)

    changed: dict[str, Any] = {}
    if "title" in body.model_fields_set and body.title is not None:
        finding.title = body.title
        changed["title"] = body.title
    if "summary" in body.model_fields_set:
        if body.summary:
            _record_finding_summary(session, finding, body.summary, user.id)
        else:
            finding.summary = body.summary  # None / empty clears the cache only
        changed["summary"] = body.summary
    if "severity" in body.model_fields_set and body.severity is not None:
        finding.severity = body.severity
        changed["severity"] = body.severity.value
    if "phase" in body.model_fields_set and body.phase is not None:
        finding.phase = body.phase
        changed["phase"] = body.phase.value
    if "exclusion" in body.model_fields_set:
        # Passing null clears the exclusion; passing a value sets it.
        finding.exclusion = body.exclusion
        changed["exclusion"] = body.exclusion.value if body.exclusion else None
    if "tags" in body.model_fields_set:
        # Replace the whole list. body.tags is None only if the client
        # sent ``null``; treat that the same as [] (clear).
        new_tags = body.tags or []
        finding.tags = new_tags
        changed["tags"] = new_tags

    if changed:
        session.add(
            AuditLog(
                engagement_id=finding.engagement_id,
                actor_type=ActorType.user,
                actor_id=str(user.id),
                event_type="finding.updated",
                payload={"finding_id": str(finding.id), "changes": changed},
            )
        )
        session.commit()
        session.refresh(finding)

    return _finding_to_read(finding)


@router.post(
    "/engagements/{slug}/findings/bulk-update",
    response_model=FindingBulkUpdateResult,
)
def bulk_update_findings(
    slug: str,
    body: FindingBulkUpdate,
    session: DbSession,
    user: CurrentNonGuestUser,
) -> FindingBulkUpdateResult:
    """Apply one analyst-controlled operation to up to 500 findings atomically.

    Missing, cross-engagement, or already-deleted IDs reject the whole request;
    bulk triage never silently applies to only part of the analyst's selection.
    Group parents are updated as selected records — child/source rows are not
    implicitly mutated.
    """
    eng = _get_engagement_or_404(session, slug)
    _reject_flushed(eng)
    ids = list(dict.fromkeys(body.finding_ids))
    rows = list(
        session.execute(
            select(Finding)
            .where(
                Finding.engagement_id == eng.id,
                Finding.id.in_(ids),
                Finding.deleted_at.is_(None),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalars()
    )
    found_ids = {row.id for row in rows}
    missing = [str(finding_id) for finding_id in ids if finding_id not in found_ids]
    if missing:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "bulk update requires active findings from one engagement",
                "missing_or_unavailable_ids": missing,
            },
        )

    now = datetime.now(tz=UTC)
    operation = body.operation
    audit_value: Any = None
    for finding in rows:
        if operation == "set_status":
            assert body.status is not None  # schema validates matching value
            finding.status = body.status
            if body.status is FindingStatus.validated:
                finding.validated_at = now
                finding.validated_by = user.id
            else:
                finding.validated_at = None
                finding.validated_by = None
            audit_value = body.status.value
        elif operation == "set_exclusion":
            finding.exclusion = body.exclusion
            audit_value = body.exclusion.value if body.exclusion else None
        elif operation == "set_severity":
            assert body.severity is not None
            finding.severity = body.severity
            audit_value = body.severity.value
        elif operation == "set_phase":
            assert body.phase is not None
            finding.phase = body.phase
            audit_value = body.phase.value
        elif operation == "add_tags":
            incoming = body.tags or []
            finding.tags = list(dict.fromkeys([*(finding.tags or []), *incoming]))[:20]
            audit_value = incoming
        elif operation == "remove_tags":
            removed = set(body.tags or [])
            finding.tags = [tag for tag in (finding.tags or []) if tag not in removed]
            audit_value = list(removed)

    session.add(
        AuditLog(
            engagement_id=eng.id,
            actor_type=ActorType.user,
            actor_id=str(user.id),
            event_type="findings.bulk_updated",
            payload={
                "operation": operation,
                "value": audit_value,
                "count": len(rows),
                "finding_ids": [str(row.id) for row in rows],
                **({"reason": body.reason} if body.reason else {}),
            },
        )
    )
    session.commit()
    for finding in rows:
        session.refresh(finding)
    by_id = {finding.id: finding for finding in rows}
    ordered = [by_id[finding_id] for finding_id in ids]
    return FindingBulkUpdateResult(
        operation=operation,
        affected=len(ordered),
        findings=[FindingRead.model_validate(_finding_to_read(row)) for row in ordered],
    )


# ---------------------------------------------------------------------------
# Manual finding create (v1.4.0)
# ---------------------------------------------------------------------------


@router.post(
    "/engagements/{slug}/findings",
    response_model=FindingRead,
    status_code=status.HTTP_201_CREATED,
)
def create_finding(
    slug: str,
    body: FindingCreate,
    session: DbSession,
    redis_client: RedisClient,
    user: CurrentNonGuestUser,
) -> dict[str, Any]:
    """Create a single analyst-drafted finding (v1.4.0).

    Distinct from :func:`import_findings` (bulk-CSV/JSON) and the worker
    path (which writes findings from live tool output). The Findings tab's
    "Add finding" modal posts here for a hand-typed row — title required,
    everything else optional with sensible defaults.

    Status follows the same phase-based rule as importers — ``osint``
    phase auto-validates, everything else lands ``pending_validation``.
    ``source_tool`` is stamped ``manual`` so the row can be visually
    distinguished from tool output and importer rows.
    """
    from app.models.finding import default_status_for_phase

    eng = _get_engagement_or_404(session, slug)
    _reject_flushed(eng)

    now = datetime.now(tz=UTC)
    finding_status = default_status_for_phase(body.phase)
    finding = Finding(
        engagement_id=eng.id,
        title=body.title,
        severity=body.severity,
        phase=body.phase,
        summary=body.summary,
        target=body.target,
        source_tool="manual",
        details={},
        status=finding_status,
        validated_at=now if finding_status == FindingStatus.validated else None,
        validated_by=user.id if finding_status == FindingStatus.validated else None,
        observed_at=body.observed_at,
        tags=body.tags,
    )
    session.add(finding)
    session.flush()

    session.add(
        AuditLog(
            engagement_id=eng.id,
            actor_type=ActorType.user,
            actor_id=str(user.id),
            event_type="finding.created_manual",
            payload={
                "finding_id": str(finding.id),
                "title": finding.title,
                "severity": finding.severity.value,
                "phase": finding.phase.value,
            },
        )
    )
    from app.services.finding_feedback import stage_finding_feedback

    feedback_entry = stage_finding_feedback(
        session,
        finding=finding,
        acting_user_id=user.id,
        operation_id=finding.id,
        source="manual",
    )
    session.commit()
    session.refresh(finding)
    from app.services.finding_feedback import publish_feedback_entries

    publish_feedback_entries(session, redis_client, [feedback_entry])
    return _finding_to_read(finding)


# ---------------------------------------------------------------------------
# Correlate findings (v1.4.0)
# ---------------------------------------------------------------------------


@router.post(
    "/engagements/{slug}/findings/correlate",
    response_model=CorrelateResponse,
)
def correlate_findings(
    slug: str,
    session: DbSession,
    redis_client: RedisClient,
    user: CurrentNonGuestUser,
) -> CorrelateResponse:
    """Ask the CorrelateAgent to propose clusters of related findings.

    Pure observer — nothing merges. The response is a list of proposed
    groups the analyst reviews in the Correlate modal; each group they
    approve triggers a separate ``POST /findings/{parent_id}/merge`` call.
    Uses the calling user's BYO provider key (Redis, sliding TTL); a
    ``NoProviderKeyError`` surfaces as 400 with a pointer to /settings/keys.
    """
    from app.agents.correlate import CorrelateAgent
    from app.services.ephemeral_provider_key import NoProviderKeyError

    eng = _get_engagement_or_404(session, slug)
    _reject_flushed(eng)

    # Only open (validated + pending_validation) non-excluded findings are
    # candidates. Rejected / false-positive / already-excluded rows are
    # skipped — the analyst already made a call on them and merging them
    # would erase that history.
    findings = list(
        session.execute(
            select(Finding)
            .where(
                Finding.engagement_id == eng.id,
                Finding.deleted_at.is_(None),
                Finding.exclusion.is_(None),
                Finding.status.in_([FindingStatus.validated, FindingStatus.pending_validation]),
            )
            .order_by(Finding.created_at.desc())
        ).scalars()
    )

    if len(findings) < 2:
        # Nothing to correlate. Return empty response with the count so
        # the frontend can show a helpful "only 1 open finding" empty
        # state instead of a generic "no groups".
        return CorrelateResponse(groups=[], total_considered=len(findings))

    agent = CorrelateAgent(redis_client=redis_client)
    try:
        execution, groups = agent.propose(
            session,
            engagement=eng,
            findings=findings,
            acting_user_id=user.id,
        )
    except NoProviderKeyError as exc:
        raise HTTPException(
            status_code=400,
            detail=(
                "no provider key cached — upload one at /settings/keys before running Correlate."
            ),
        ) from exc

    session.add(execution)
    session.add(
        AuditLog(
            engagement_id=eng.id,
            actor_type=ActorType.user,
            actor_id=str(user.id),
            event_type="findings.correlate_proposed",
            payload={
                "groups_count": len(groups),
                "considered": len(findings),
            },
        )
    )
    session.commit()

    return CorrelateResponse(
        groups=[CorrelateGroup(rationale=g.rationale, finding_ids=g.finding_ids) for g in groups],
        total_considered=len(findings),
    )


# ---------------------------------------------------------------------------
# Regroup ungrouped findings (v1.4.1)
# ---------------------------------------------------------------------------


@router.post(
    "/engagements/{slug}/findings/regroup/preview",
    response_model=RegroupPreview,
)
def regroup_findings_preview(
    slug: str,
    session: DbSession,
    _user: CurrentNonGuestUser,
) -> RegroupPreview:
    """Dry-run: scan every ungrouped row, compute what its group_key
    WOULD be under the v1.4.0 vocab, and surface groups of size >= 2.

    Deterministic. No LLM. Sibling of the LLM-driven correlate endpoint.
    Nothing changes in the DB until the analyst POSTs to
    ``/regroup/apply`` with the keys they approved.
    """
    from app.services.finding_grouping import (
        compute_group_key,
        extract_items,
        group_title,
    )

    eng = _get_engagement_or_404(session, slug)
    _reject_flushed(eng)

    ungrouped = list(
        session.execute(
            select(Finding).where(
                Finding.engagement_id == eng.id,
                Finding.deleted_at.is_(None),
                Finding.group_key.is_(None),
            )
        ).scalars()
    )

    # Bucket by proposed group_key.
    buckets: dict[str, list[Finding]] = {}
    ungroupable = 0
    for f in ungrouped:
        # Reconstruct the (args, data) split from details — the worker
        # persists `details = {thread_id, args, ...data}` so we unpack the
        # same way _finding_to_read does.
        details = dict(f.details or {})
        details.pop("thread_id", None)
        args = details.pop("args", {})
        if not isinstance(args, dict):
            args = {}
        data = details
        key = compute_group_key(f.source_tool, args, data)
        if not key:
            ungroupable += 1
            continue
        buckets.setdefault(key, []).append(f)

    # Look up any existing parents whose group_key matches — we'd absorb
    # into them rather than mint a fresh row.
    existing_parents: dict[str, Finding] = {}
    if buckets:
        rows = session.execute(
            select(Finding).where(
                Finding.engagement_id == eng.id,
                Finding.deleted_at.is_(None),
                Finding.group_key.in_(list(buckets.keys())),
            )
        ).scalars()
        for row in rows:
            if row.group_key:
                existing_parents[row.group_key] = row

    proposals: list[RegroupProposal] = []
    for key, members in buckets.items():
        # A group of 1 is meaningless — unless it would absorb into an
        # existing parent, in which case the size >= 1 rule still gives
        # value (fold this stray into the pre-existing grouped row).
        if len(members) < 2 and key not in existing_parents:
            continue

        tool = members[0].source_tool or "unknown"
        first_details = dict(members[0].details or {})
        first_details.pop("thread_id", None)
        first_details.pop("args", None)
        title = group_title(tool, key, first_details) or f"{tool}: {key}"

        # Projected item_count = sum of each member's extractable items,
        # plus any items already sitting on the existing parent.
        projected_items = 0
        for f in members:
            details = dict(f.details or {})
            details.pop("thread_id", None)
            details.pop("args", None)
            projected_items += max(1, len(extract_items(tool, details)))
        parent = existing_parents.get(key)
        if parent:
            existing_items = (parent.details or {}).get("items")
            if isinstance(existing_items, list):
                projected_items += len(existing_items)

        # Projected severity: max across members and the existing parent.
        projected_severity = members[0].severity
        for f in members:
            if _SEVERITY_ORDER[f.severity] > _SEVERITY_ORDER[projected_severity]:
                projected_severity = f.severity
        if parent and _SEVERITY_ORDER[parent.severity] > _SEVERITY_ORDER[projected_severity]:
            projected_severity = parent.severity

        proposals.append(
            RegroupProposal(
                group_key=key,
                tool=tool,
                proposed_title=title,
                member_ids=[f.id for f in members],
                projected_severity=projected_severity,
                projected_item_count=projected_items,
                absorbs_into_existing_parent_id=parent.id if parent else None,
            )
        )

    # Sort largest-first so the analyst sees big wins at the top.
    proposals.sort(key=lambda p: -p.projected_item_count)

    return RegroupPreview(
        proposals=proposals,
        scanned_row_count=len(ungrouped),
        ungroupable_count=ungroupable,
    )


_EXCLUSION_PRIORITY: dict[Any, int] = {
    None: 0,
    "out_of_scope": 1,
    "outside_roe": 2,
}


@router.post(
    "/engagements/{slug}/findings/regroup/apply",
    response_model=list[RegroupApplyResult],
)
def regroup_findings_apply(
    slug: str,
    body: RegroupApplyRequest,
    session: DbSession,
    user: CurrentNonGuestUser,
) -> list[RegroupApplyResult]:
    """Fold ungrouped rows into grouped rows, one approved group_key
    at a time.

    Same-tool only. Source rows are soft-deleted with
    ``details['regrouped_into']`` pointing at the parent. Exclusion
    marks propagate to the parent (outside_roe > out_of_scope > null).
    One ``findings.regrouped`` audit_log row per apply summarises the
    batch.
    """
    from app.services.finding_grouping import (
        compute_group_key,
        upsert_grouped_finding,
    )

    eng = _get_engagement_or_404(session, slug)
    _reject_flushed(eng)

    approved_keys = {k for k in body.group_keys if k}
    if not approved_keys:
        raise HTTPException(status_code=400, detail="no group_keys supplied")

    # Load candidate rows — same query the preview endpoint uses.
    ungrouped = list(
        session.execute(
            select(Finding).where(
                Finding.engagement_id == eng.id,
                Finding.deleted_at.is_(None),
                Finding.group_key.is_(None),
            )
        ).scalars()
    )

    # Bucket into per-key groups matching the analyst's approvals.
    buckets: dict[str, list[Finding]] = {}
    for f in ungrouped:
        details = dict(f.details or {})
        details.pop("thread_id", None)
        args = details.pop("args", {})
        if not isinstance(args, dict):
            args = {}
        key = compute_group_key(f.source_tool, args, details)
        if key and key in approved_keys:
            buckets.setdefault(key, []).append(f)

    results: list[RegroupApplyResult] = []
    now = datetime.now(tz=UTC)
    total_absorbed = 0

    for key, members in buckets.items():
        if not members:
            continue

        # Winning exclusion across the group (outside_roe > out_of_scope
        # > null). Also read the existing parent's exclusion in case it
        # already has one.
        winning_exclusion: Any = None
        for f in members:
            cur = f.exclusion.value if f.exclusion else None
            if _EXCLUSION_PRIORITY[cur] > _EXCLUSION_PRIORITY[winning_exclusion]:
                winning_exclusion = cur

        parent: Finding | None = None
        for f in members:
            details = dict(f.details or {})
            details.pop("thread_id", None)
            args = details.pop("args", {})
            if not isinstance(args, dict):
                args = {}
            # v1.4.3: use EACH member's own source_tool so extract_items
            # projects the right shape. Different tools (subfinder,
            # crt_sh, dns_lookup) can now share one group_key under the
            # unified subdomains:{apex} vocab — the first member's tool
            # isn't necessarily the tool of the row we're merging.
            member_tool = f.source_tool or "unknown"
            parent, _added = upsert_grouped_finding(
                session,
                engagement_id=eng.id,
                group_key=key,
                tool=member_tool,
                thread_id=None,
                args=args,
                data=details,
                incoming_severity=f.severity,
                default_title=f.title,
                phase=f.phase,
                status=f.status,
                validated_by=f.validated_by,
            )

        if parent is None:
            continue

        # Winning exclusion beats whatever the parent already had if the
        # new winner is more restrictive.
        parent_ex = parent.exclusion.value if parent.exclusion else None
        if _EXCLUSION_PRIORITY[winning_exclusion] > _EXCLUSION_PRIORITY[parent_ex]:
            from app.models import FindingExclusion

            parent.exclusion = FindingExclusion(winning_exclusion) if winning_exclusion else None

        # Soft-delete every source row with a pointer back to the parent.
        for f in members:
            if f.id == parent.id:
                # Shouldn't happen — sources have group_key IS NULL and
                # parent has group_key set — but guard anyway.
                continue
            source_details = dict(f.details or {})
            source_details["regrouped_into"] = str(parent.id)
            f.details = source_details
            f.deleted_at = now
            f.deleted_by_user_id = user.id
            total_absorbed += 1

        session.flush()
        session.refresh(parent)

        final_items = (parent.details or {}).get("items")
        results.append(
            RegroupApplyResult(
                group_key=key,
                parent_id=parent.id,
                absorbed_member_count=len(members),
                final_item_count=len(final_items) if isinstance(final_items, list) else 0,
                final_severity=parent.severity,
            )
        )

    if results:
        session.add(
            AuditLog(
                engagement_id=eng.id,
                actor_type=ActorType.user,
                actor_id=str(user.id),
                event_type="findings.regrouped",
                payload={
                    "groups_applied": [r.group_key for r in results],
                    "total_absorbed": total_absorbed,
                },
            )
        )
    session.commit()
    return results


# ---------------------------------------------------------------------------
# Repair groups (v1.4.3) — items backfill + legacy-key migration
# ---------------------------------------------------------------------------


@router.post(
    "/engagements/{slug}/findings/repair-groups",
    response_model=RepairGroupsResult,
)
def repair_groups(
    slug: str,
    session: DbSession,
    user: CurrentAdminUser,
) -> RepairGroupsResult:
    """One-time maintenance pass over an engagement's grouped rows.

    Does three things (in order):

    1. **Rekey legacy parents**. Groups created before v1.4.3 used per-tool
       keys (``subfinder:{apex}``, ``crt_sh:{apex}``, ``dns:{domain}``).
       This step re-runs :func:`compute_group_key` against each parent's
       source rows to figure out what its key would be TODAY. If it
       differs and no other parent has that key, we just update in place
       (title + group_key). If another parent already owns the new key,
       we fold this one INTO it (items merged, source rows re-pointed,
       old parent soft-deleted).

    2. **Rebuild items[]**. For every parent, walk its soft-deleted
       source rows (``details.regrouped_into = <parent_id>``), run the
       current :func:`extract_items` over each source's data, dedup, and
       overwrite the parent's ``details['items']`` with the fresh set.
       Also recomputes severity as the max across sources + parent.

    3. **Fold matching ungrouped rows**. Any row where ``group_key IS
       NULL`` and :func:`compute_group_key` yields a key that already
       exists on an active parent gets absorbed into that parent
       (source soft-deleted, items appended).

    Admin-scoped. Non-destructive from an audit standpoint — every
    source row still lives in the DB, soft-deleted, with a pointer
    back to its parent.
    """
    from app.services.finding_grouping import (
        compute_group_key,
        extract_items,
        group_title,
        item_dedup_key,
        upsert_grouped_finding,
    )

    eng = _get_engagement_or_404(session, slug)
    _reject_flushed(eng)

    parents = list(
        session.execute(
            select(Finding).where(
                Finding.engagement_id == eng.id,
                Finding.deleted_at.is_(None),
                Finding.group_key.is_not(None),
            )
        ).scalars()
    )
    now = datetime.now(tz=UTC)

    parents_by_key: dict[str, Finding] = {}
    parents_scanned = len(parents)
    parents_rekeyed = 0
    parents_merged = 0
    parents_items_repaired = 0

    # Preload every soft-deleted row once so we don't re-query per parent.
    all_deleted = list(
        session.execute(
            select(Finding).where(
                Finding.engagement_id == eng.id,
                Finding.deleted_at.is_not(None),
            )
        ).scalars()
    )
    sources_by_parent: dict[str, list[Finding]] = {}
    for row in all_deleted:
        pid = (row.details or {}).get("regrouped_into")
        if pid:
            sources_by_parent.setdefault(str(pid), []).append(row)

    def _regenerate_items(
        parent: Finding, sources: list[Finding]
    ) -> tuple[list[dict[str, Any]], Severity]:
        """Return (fresh items list, max severity) built by re-extracting
        every source row through the current vocab."""
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        top_sev = parent.severity
        for src in sources:
            details = dict(src.details or {})
            details.pop("thread_id", None)
            details.pop("args", None)
            details.pop("regrouped_into", None)
            src_tool = src.source_tool or "unknown"
            src_items = extract_items(src_tool, details)
            for it in src_items:
                key = item_dedup_key(src_tool, it)
                if not key or key in seen:
                    continue
                seen.add(key)
                it_stamped = dict(it)
                it_stamped.setdefault("first_seen_at", now.isoformat())
                items.append(it_stamped)
            if _SEVERITY_ORDER[src.severity] > _SEVERITY_ORDER[top_sev]:
                top_sev = src.severity
        return items, top_sev

    # Step 1 + 2 combined: iterate parents, rekey where needed, rebuild items.
    for parent in parents:
        # v1.4.10: manual-merged rows are analyst-curated. Skip every
        # step of repair for them — no rekey, no items rebuild, no fold
        # into a computed-key parent. Their items[] was set by the merge
        # endpoint and must not be re-derived from sources.
        if parent.group_key and parent.group_key.startswith("manual:"):
            continue

        sources = sources_by_parent.get(str(parent.id), [])
        # Figure out what the new key SHOULD be. Use the first source's
        # (tool, args, data) as the sample — all sources in a group share
        # the same key by construction.
        new_key: str | None = parent.group_key
        if sources:
            first = sources[0]
            details = dict(first.details or {})
            details.pop("thread_id", None)
            args = details.pop("args", {})
            if not isinstance(args, dict):
                args = {}
            new_key = compute_group_key(first.source_tool, args, details) or parent.group_key

        # Rekey path.
        if new_key and new_key != parent.group_key:
            existing = (
                parents_by_key.get(new_key)
                or session.execute(
                    select(Finding).where(
                        Finding.engagement_id == eng.id,
                        Finding.deleted_at.is_(None),
                        Finding.group_key == new_key,
                        Finding.id != parent.id,
                    )
                ).scalar_one_or_none()
            )
            if existing is not None:
                # Fold THIS parent INTO existing: re-point sources,
                # merge items, soft-delete this parent.
                for src in sources:
                    src_details = dict(src.details or {})
                    src_details["regrouped_into"] = str(existing.id)
                    src.details = src_details
                sources_by_parent.setdefault(str(existing.id), []).extend(sources)
                sources_by_parent.pop(str(parent.id), None)

                parent.deleted_at = now
                parent.deleted_by_user_id = user.id
                parents_merged += 1
                continue
            # No collision — just rekey in place.
            parent.group_key = new_key
            new_title = group_title(parent.source_tool, new_key, parent.details or {})
            if new_title:
                parent.title = new_title
            parents_rekeyed += 1

        parents_by_key[parent.group_key or ""] = parent

    session.flush()

    # Now walk parents_by_key (the surviving parents) and rebuild items[].
    total_items_after = 0
    for _key, parent in parents_by_key.items():
        sources = sources_by_parent.get(str(parent.id), [])
        if not sources:
            # Keep the existing items[] — nothing to rebuild from.
            existing_items = (parent.details or {}).get("items")
            total_items_after += len(existing_items) if isinstance(existing_items, list) else 0
            continue

        fresh_items, top_sev = _regenerate_items(parent, sources)
        details = dict(parent.details or {})
        details["items"] = fresh_items
        details["last_seen_at"] = now.isoformat()
        details.setdefault("first_seen_at", now.isoformat())
        details["grouped"] = True
        parent.details = details
        parent.severity = top_sev
        parents_items_repaired += 1
        total_items_after += len(fresh_items)

    session.flush()

    # Step 3: fold ungrouped rows that would match one of our parent keys.
    ungrouped = list(
        session.execute(
            select(Finding).where(
                Finding.engagement_id == eng.id,
                Finding.deleted_at.is_(None),
                Finding.group_key.is_(None),
            )
        ).scalars()
    )
    ungrouped_absorbed = 0
    for row in ungrouped:
        details = dict(row.details or {})
        details.pop("thread_id", None)
        args = details.pop("args", {})
        if not isinstance(args, dict):
            args = {}
        row_key = compute_group_key(row.source_tool, args, details)
        if not row_key or row_key not in parents_by_key:
            continue
        parent = parents_by_key[row_key]
        upsert_grouped_finding(
            session,
            engagement_id=eng.id,
            group_key=row_key,
            tool=row.source_tool or "unknown",
            thread_id=None,
            args=args,
            data=details,
            incoming_severity=row.severity,
            default_title=row.title,
            phase=row.phase,
            status=row.status,
            validated_by=row.validated_by,
        )
        # Soft-delete the source with a pointer at the parent.
        src_details = dict(row.details or {})
        src_details["regrouped_into"] = str(parent.id)
        row.details = src_details
        row.deleted_at = now
        row.deleted_by_user_id = user.id
        ungrouped_absorbed += 1

    if ungrouped_absorbed:
        # Recount items since upserts appended new entries.
        session.flush()
        total_items_after = 0
        for _key, parent in parents_by_key.items():
            session.refresh(parent)
            items = (parent.details or {}).get("items")
            total_items_after += len(items) if isinstance(items, list) else 0

    session.add(
        AuditLog(
            engagement_id=eng.id,
            actor_type=ActorType.user,
            actor_id=str(user.id),
            event_type="findings.groups_repaired",
            payload={
                "parents_scanned": parents_scanned,
                "parents_items_repaired": parents_items_repaired,
                "parents_rekeyed": parents_rekeyed,
                "parents_merged": parents_merged,
                "ungrouped_absorbed": ungrouped_absorbed,
                "total_items_after": total_items_after,
            },
        )
    )
    session.commit()

    return RepairGroupsResult(
        parents_scanned=parents_scanned,
        parents_items_repaired=parents_items_repaired,
        parents_rekeyed=parents_rekeyed,
        parents_merged=parents_merged,
        ungrouped_absorbed=ungrouped_absorbed,
        total_items_after=total_items_after,
    )


# ---------------------------------------------------------------------------
# Merge findings (v1.4.0)
# ---------------------------------------------------------------------------


_SEVERITY_ORDER = {
    Severity.info: 0,
    Severity.low: 1,
    Severity.medium: 2,
    Severity.high: 3,
    Severity.critical: 4,
}


@router.post(
    "/findings/{parent_id}/merge",
    response_model=FindingRead,
)
def merge_findings(
    parent_id: uuid.UUID,
    body: MergeRequest,
    session: DbSession,
    user: CurrentNonGuestUser,
) -> dict[str, Any]:
    """Fold ``child_ids`` into ``parent_id``.

    Parent absorbs the children: the highest severity across the group
    wins, child summaries are appended to the parent's summary (each
    prefixed with "Merged from <short-id>"), and every child is
    soft-deleted with ``deleted_at`` stamped. Children must belong to the
    same engagement as the parent — 400 if any don't.

    A ``findings.merged`` audit_log row captures parent + child IDs so
    the merge can be traced (and eventually undone via a recovery view).
    Returns the updated parent finding.
    """
    parent = _lock_active_finding_for_mutation(session, parent_id)

    child_ids = list({cid for cid in body.child_ids if cid != parent_id})
    if not child_ids:
        raise HTTPException(
            status_code=400,
            detail="child_ids must contain at least one distinct id",
        )

    children = list(
        session.execute(
            select(Finding)
            .where(
                Finding.id.in_(child_ids),
                Finding.deleted_at.is_(None),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalars()
    )
    if len(children) != len(child_ids):
        raise HTTPException(
            status_code=400,
            detail=(
                "one or more child findings not found or already deleted "
                f"(requested {len(child_ids)}, found {len(children)})"
            ),
        )
    for child in children:
        if child.engagement_id != parent.engagement_id:
            raise HTTPException(
                status_code=400,
                detail="all children must belong to the same engagement as the parent",
            )

    # Severity: highest across the group wins.
    top_sev = parent.severity
    for c in children:
        if _SEVERITY_ORDER[c.severity] > _SEVERITY_ORDER[top_sev]:
            top_sev = c.severity
    parent.severity = top_sev

    # Summary: append each child's summary under a "Merged from" header
    # so the merged parent still carries the child's narrative. Skip
    # children with no summary — no need to add an empty section.
    appended_parts: list[str] = []
    for c in children:
        if c.summary and c.summary.strip():
            short = str(c.id).replace("-", "")[:6].upper()
            appended_parts.append(f"\n\n---\n*Merged from {short}:*\n{c.summary}")
    if appended_parts:
        parent.summary = (parent.summary or "") + "".join(appended_parts)
        # Also drop a FindingSummary history row so the merge is visible
        # in the summary-history list, not just as a bumped summary field.
        _record_finding_summary(
            session,
            parent,
            (parent.summary or "").strip(),
            user.id,
        )

    # v1.4.10: Union each child's items[] into the parent (with dedup)
    # and stamp a manual:{...} group_key so the row survives future
    # auto-regroup runs. On the first manual-merge into an ungrouped
    # parent, project the parent's own raw data into items[] first so
    # the parent's finding content isn't orphaned once children start
    # appending.
    from app.services.finding_grouping import extract_items, item_dedup_key

    _RESERVED_DETAIL_KEYS = {"thread_id", "args", "items", "grouped", "merged_into"}

    def _project_row_data(row: Finding) -> dict[str, Any]:
        d = dict(row.details or {})
        return {k: v for k, v in d.items() if k not in _RESERVED_DETAIL_KEYS}

    parent_details = dict(parent.details or {})
    parent_items = list(parent_details.get("items") or [])
    parent_had_items = bool(parent_items)
    if not parent_had_items:
        for it in extract_items(parent.source_tool, _project_row_data(parent)):
            if isinstance(it, dict):
                seeded = dict(it)
                seeded.setdefault("source_tool", parent.source_tool or "unknown")
                parent_items.append(seeded)

    seen_keys: set[str] = set()
    for it in parent_items:
        if isinstance(it, dict):
            k = item_dedup_key(parent.source_tool, it)
            if k:
                seen_keys.add(k)

    items_added = 0
    for c in children:
        c_details = dict(c.details or {})
        c_items_field = c_details.get("items")
        if isinstance(c_items_field, list) and c_items_field:
            c_items = [it for it in c_items_field if isinstance(it, dict)]
        else:
            c_items = extract_items(c.source_tool, _project_row_data(c))
        for it in c_items:
            if not isinstance(it, dict):
                continue
            it_copy = dict(it)
            it_copy.setdefault("source_tool", c.source_tool or "unknown")
            k = item_dedup_key(c.source_tool, it_copy)
            if k and k in seen_keys:
                continue
            if k:
                seen_keys.add(k)
            parent_items.append(it_copy)
            items_added += 1

    parent_details["items"] = parent_items
    parent_details["grouped"] = True

    # Stamp a manual:{...} group_key if the parent isn't already
    # manual-keyed. Overwrites any prior auto-key — the row is now
    # analyst-curated and must not be re-shuffled by regroup.
    if not (parent.group_key and parent.group_key.startswith("manual:")):
        parent_details["original_group_key"] = parent.group_key
        parent.group_key = f"manual:{parent.id.hex[:8]}"

    parent.details = parent_details

    # Preserve the work ledger across merge: transfer every child link to the
    # surviving parent, collapsing only exact (work item, relationship)
    # duplicates. Historical child IDs remain in the immutable merge audit.
    from app.models import WorkItemFinding

    child_id_set = {child.id for child in children}
    work_links = list(
        session.execute(
            select(WorkItemFinding).where(WorkItemFinding.finding_id.in_(child_id_set))
        ).scalars()
    )
    transferred_work_links = 0
    for link in work_links:
        duplicate = session.execute(
            select(WorkItemFinding).where(
                WorkItemFinding.work_item_id == link.work_item_id,
                WorkItemFinding.finding_id == parent.id,
                WorkItemFinding.relationship == link.relationship,
            )
        ).scalar_one_or_none()
        if duplicate is not None:
            session.delete(link)
        else:
            link.finding_id = parent.id
            transferred_work_links += 1

    now = datetime.now(tz=UTC)
    for c in children:
        c.deleted_at = now
        c.deleted_by_user_id = user.id
        # Stash merge attribution in details so the row survives with
        # enough context that a future recovery view can un-hide it and
        # know what happened.
        details = dict(c.details or {})
        details["merged_into"] = str(parent.id)
        c.details = details

    session.add(
        AuditLog(
            engagement_id=parent.engagement_id,
            actor_type=ActorType.user,
            actor_id=str(user.id),
            event_type="findings.merged",
            payload={
                "parent_id": str(parent.id),
                "child_ids": [str(c.id) for c in children],
                "final_severity": top_sev.value,
                "items_added": items_added,
                "transferred_work_links": transferred_work_links,
                "group_key": parent.group_key,
            },
        )
    )
    session.commit()
    session.refresh(parent)
    return _finding_to_read(parent)


class BulkDeleteRequest(BaseModel):
    """Body for POST /engagements/{slug}/findings/bulk-delete (v0.10.0)."""

    finding_ids: list[uuid.UUID] = Field(
        ...,
        min_length=1,
        max_length=500,
        description="IDs to soft-delete. Max 500 per call to keep the "
        "audit_log payload from ballooning.",
    )


class BulkDeleteResult(BaseModel):
    deleted: int
    skipped_missing: int
    skipped_already_deleted: int


@router.post(
    "/engagements/{slug}/findings/bulk-delete",
    response_model=BulkDeleteResult,
)
def bulk_delete_findings(
    slug: str,
    body: BulkDeleteRequest,
    session: DbSession,
    user: CurrentNonGuestUser,
) -> BulkDeleteResult:
    """Soft-delete a batch of findings in one transaction.

    Same rules as the singular DELETE — user + admin allowed, guest
    403, audit_log recorded. One ``findings.bulk_deleted`` row summarises
    the batch (count + IDs) instead of N single-delete rows so the
    audit surface stays scannable.
    """
    eng = _get_engagement_or_404(session, slug)
    _reject_flushed(eng)
    ids = list({fid for fid in body.finding_ids})  # dedup within request
    rows = list(
        session.execute(
            select(Finding)
            .where(
                Finding.engagement_id == eng.id,
                Finding.id.in_(ids),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalars()
    )
    found_ids = {r.id for r in rows}
    skipped_missing = len(ids) - len(found_ids)

    now = datetime.now(tz=UTC)
    deleted_ids: list[str] = []
    already_deleted = 0
    for r in rows:
        if r.deleted_at is not None:
            already_deleted += 1
            continue
        r.deleted_at = now
        r.deleted_by_user_id = user.id
        deleted_ids.append(str(r.id))

    if deleted_ids:
        session.add(
            AuditLog(
                engagement_id=eng.id,
                actor_type=ActorType.user,
                actor_id=str(user.id),
                event_type="findings.bulk_deleted",
                payload={
                    "count": len(deleted_ids),
                    "finding_ids": deleted_ids,
                },
            )
        )
        session.commit()

    return BulkDeleteResult(
        deleted=len(deleted_ids),
        skipped_missing=skipped_missing,
        skipped_already_deleted=already_deleted,
    )


@router.delete(
    "/findings/{finding_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_finding(
    finding_id: uuid.UUID,
    session: DbSession,
    user: CurrentNonGuestUser,
) -> Response:
    """Soft-delete a finding (v0.10.0).

    Stamps ``deleted_at`` + ``deleted_by_user_id`` and writes an audit
    row. Row stays in Postgres so history + attribution survive; every
    read path (list, report, export, MCP, entity extraction, Burp
    dedup) filters ``deleted_at IS NULL``. Guest role denied via
    ``CurrentNonGuestUser``.

    Re-deleting an already-deleted finding is a no-op that still returns
    204 (idempotent from the client's view).
    """
    finding = session.get(Finding, finding_id)
    if finding is None:
        raise HTTPException(status_code=404, detail="finding not found")
    _reject_engagement_id(session, finding.engagement_id)
    session.refresh(finding, with_for_update=True)
    if finding.deleted_at is None:
        finding.deleted_at = datetime.now(tz=UTC)
        finding.deleted_by_user_id = user.id
        session.add(
            AuditLog(
                engagement_id=finding.engagement_id,
                actor_type=ActorType.user,
                actor_id=str(user.id),
                event_type="finding.deleted",
                payload={
                    "finding_id": str(finding.id),
                    "title": finding.title,
                    "severity": finding.severity.value,
                },
            )
        )
        session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Finding summary history (v0.7.0)
# ---------------------------------------------------------------------------


def _finding_summary_to_read(entry: FindingSummary, author: User | None) -> dict[str, Any]:
    return {
        "id": entry.id,
        "finding_id": entry.finding_id,
        "body": entry.body,
        "author_user_id": entry.author_user_id,
        "author_email": author.email if author else None,
        "author_display_name": author.display_name if author else None,
        "created_at": entry.created_at,
    }


@router.post(
    "/findings/{finding_id}/summaries",
    response_model=FindingSummaryRead,
    status_code=status.HTTP_201_CREATED,
)
def create_finding_summary(
    finding_id: uuid.UUID,
    body: FindingSummaryCreate,
    session: DbSession,
    user: CurrentNonGuestUser,
) -> dict[str, Any]:
    """Append a summary entry to a finding's immutable history.

    The frontend uses this from the Findings slide-over: the textarea
    clears on success, and the history list below renders the new entry
    at the top. Also refreshes ``findings.summary`` as the denormalized
    cache so the Report tab / JSON export keep showing the latest.
    """
    finding = _lock_active_finding_for_mutation(session, finding_id)

    entry = _record_finding_summary(session, finding, body.body, user.id)
    session.add(
        AuditLog(
            engagement_id=finding.engagement_id,
            actor_type=ActorType.user,
            actor_id=str(user.id),
            event_type="finding.summary_recorded",
            payload={
                "finding_id": str(finding.id),
                "entry_id": str(entry.id),
                "body_chars": len(body.body),
            },
        )
    )
    session.commit()
    session.refresh(entry)
    return _finding_summary_to_read(entry, user)


@router.get(
    "/findings/{finding_id}/summaries",
    response_model=list[FindingSummaryRead],
)
def list_finding_summaries(
    finding_id: uuid.UUID,
    session: DbSession,
    _user: CurrentUser,
) -> list[dict[str, Any]]:
    """Newest-first list of summary entries for a finding."""
    get_active_finding_or_404(session, finding_id)

    rows = list(
        session.execute(
            select(FindingSummary, User)
            .join(User, User.id == FindingSummary.author_user_id, isouter=True)
            .where(FindingSummary.finding_id == finding_id)
            .order_by(FindingSummary.created_at.desc())
        ).all()
    )
    return [_finding_summary_to_read(entry, author) for entry, author in rows]


# ---------------------------------------------------------------------------
# Engagement JSON export
# ---------------------------------------------------------------------------


@router.get("/engagements/{slug}/export")
def get_engagement_export(
    slug: str,
    session: DbSession,
    _user: CurrentUser,
    omit_excluded: Annotated[
        bool,
        Query(
            description=(
                "Drop findings marked out_of_scope / outside_roe from the "
                "returned payload. Defaults false because this endpoint is a "
                "full-fidelity snapshot; the Report workspace explicitly sends "
                "true for a client-safe download."
            ),
        ),
    ] = False,
) -> dict[str, Any]:
    """Full engagement snapshot as structured JSON — findings, scope, observations,
    and audit summary. Suitable for archiving or importing into another instance."""
    eng = _get_engagement_or_404(session, slug)
    return _build_export_payload(session, eng, omit_excluded=omit_excluded)


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
    user: CurrentNonGuestUser,
) -> Attachment:
    """Upload a screenshot or evidence file and attach it to the finding."""
    data = await file.read()
    if len(data) > _MAX_ATTACHMENT_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"file too large — max {_MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB",
        )

    finding = _lock_active_finding_for_mutation(session, finding_id)
    attachment = Attachment(
        finding_id=finding_id,
        engagement_id=finding.engagement_id,
        filename=file.filename or "attachment",
        content_type=file.content_type or "application/octet-stream",
        size_bytes=len(data),
        data=data,
        created_by=str(user.id),
    )
    session.add(attachment)
    session.add(
        AuditLog(
            engagement_id=finding.engagement_id,
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
    get_active_finding_or_404(session, finding_id)
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
    get_active_finding_or_404(session, attachment.finding_id)
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
    user: CurrentNonGuestUser,
) -> Response:
    """Delete an attachment."""
    attachment = session.get(Attachment, attachment_id)
    if attachment is None:
        raise HTTPException(status_code=404, detail="attachment not found")
    _lock_active_finding_for_mutation(session, attachment.finding_id)
    session.add(
        AuditLog(
            engagement_id=attachment.engagement_id,
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


# ---------------------------------------------------------------------------
# Runs (enqueue run.start to the inbound stream)
# ---------------------------------------------------------------------------


def _require_user_provider_key(
    redis_client: RedisClient, *, user_id: uuid.UUID, provider: str
) -> None:
    """Raise 400 if the acting user has no ephemeral key cached for ``provider``.

    Keys live in Redis with a sliding TTL (locked 2026-06-29) — when the
    analyst's session goes idle, this helper trips. The error message
    points at the Settings page so a fresh-or-stale-session analyst
    knows exactly where to go.
    """
    from app.services.ephemeral_provider_key import (
        NoProviderKeyError,
        resolve_for_user,
    )

    try:
        resolve_for_user(redis_client, user_id=user_id, provider=provider)
    except NoProviderKeyError as exc:
        raise HTTPException(
            status_code=400,
            detail=(
                f"no provider key cached for '{provider}'. "
                "Upload one at /settings/keys before kicking off a run."
            ),
        ) from exc


@router.post(
    "/engagements/{slug}/runs",
    response_model=RunStartResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_run(
    slug: str,
    body: RunStart,
    session: DbSession,
    redis_client: RedisClient,
    user: CurrentNonGuestUser,
) -> RunStartResponse:
    eng = _get_engagement_or_404(session, slug)
    _reject_flushed(eng)
    if eng.status is not EngagementStatus.active:
        raise HTTPException(
            status_code=409,
            detail=(f"engagement is {eng.status.value}; only active engagements accept new runs"),
        )

    # Resolve effective model: body wins, else fall back to env defaults.
    if body.model is not None:
        provider, model_name = body.model.provider, body.model.name
    else:
        provider, model_name = default_provider_model()
    # v1.4.12: if the analyst pinned a specific cached key, validate it
    # belongs to them and matches the provider BEFORE we stash it for the
    # worker. resolve_for_user with key_id does the membership/kind/provider
    # checks and raises NoProviderKeyError on any mismatch.
    chosen_key_id = body.model.key_id if body.model is not None else None
    if chosen_key_id is not None:
        from app.services.ephemeral_provider_key import (
            NoProviderKeyError,
            resolve_for_user,
        )

        try:
            resolve_for_user(
                redis_client,
                user_id=user.id,
                provider=provider,
                key_id=chosen_key_id,
            )
        except NoProviderKeyError as exc:
            raise HTTPException(
                status_code=400,
                detail=(
                    "the selected provider key isn't cached for "
                    f"'{provider}' (or doesn't belong to you). Upload or "
                    "re-select it at /settings/keys."
                ),
            ) from exc
    else:
        _require_user_provider_key(redis_client, user_id=user.id, provider=provider)
    effective_model = RunModel(provider=provider, name=model_name, key_id=chosen_key_id)

    thread_id = uuid.uuid4()
    # Stash the (provider, model) so the approval endpoint can echo it on
    # the resume envelope without redoing the resolution dance.
    store_run_model(
        redis_client,
        thread_id,
        provider=effective_model.provider,
        model_name=effective_model.name,
        acting_user_id=user.id,
        key_id=chosen_key_id,
    )

    # Stage 3+1: every worker run carries an MCP lease — the Stage 1.5
    # local-execution fallback is gone. Direct-run prompts don't have a
    # Task wrapping them, so we mint a "direct-run" lease keyed on the
    # engagement + thread_id with the full non-exploit tool surface.
    # The Strategic policy LLM isn't called here (no task to narrow); the
    # analyst typed a freeform prompt so they get the full agent surface.
    from app.core.config import settings
    from app.orchestrator.tools import all_tools
    from app.services import mcp_lease

    allowed_tools = [spec.name for spec in all_tools() if spec.kind != TaskKind.exploit]
    scope_items_for_lease = list(
        session.execute(select(ScopeItem).where(ScopeItem.engagement_id == eng.id)).scalars()
    )
    lease = mcp_lease.mint_for_engagement(
        session,
        engagement_id=eng.id,
        thread_id=thread_id,
        allowed_tools=allowed_tools,
        context={
            "engagement": {
                "slug": eng.slug,
                "name": eng.name,
                "description": eng.description,
            },
            "scope": [
                {
                    "kind": item.kind.value,
                    "value": item.value,
                    "is_exclusion": item.is_exclusion,
                }
                for item in scope_items_for_lease
            ],
            "direct_run": True,
        },
        prompt_keys=[],
    )
    # The FastMCP server is mounted at /mcp in app/main.py; the SSE
    # endpoint inside it lives at /sse, so the worker's MCP client needs
    # the full /mcp/sse path. Hitting /mcp gets a 404 (no handler at the
    # mount root) once auth passes.
    mcp_url = f"{settings.public_base_url.rstrip('/')}/mcp/sse"

    # v0.8.1: stamp an AgentExecution row for the run itself so the Status
    # tab paints a green "active" box AS SOON AS the analyst clicks Run.
    # Without this the Status tab is empty for the entire duration of the
    # worker scan — only Strategic / Triage rows that fire AFTER findings
    # land become visible. Uses AgentName.tactical (the closest semantic
    # fit — Tactical dispatches; Strategic plans) and stashes the
    # thread_id in input.thread_id so a future commit can lazily mark it
    # completed/failed by matching against run.completed/run.errored
    # events on the outbound stream. For v0.8.1 the row stays in
    # 'running' status until that lazy update lands — analyst sees
    # activity, not a stale-but-correct state.
    from app.models import AgentExecution, AgentExecutionStatus, AgentName, AgentTrigger

    run_execution = AgentExecution(
        engagement_id=eng.id,
        agent=AgentName.tactical,
        trigger=AgentTrigger.manual,
        input={
            "thread_id": str(thread_id),
            "prompt_len": len(body.prompt),
            "model": {
                "provider": effective_model.provider,
                "name": effective_model.name,
            },
        },
        model_provider=effective_model.provider,
        model_name=effective_model.name,
        status=AgentExecutionStatus.running,
        started_at=datetime.now(tz=UTC),
    )
    session.add(run_execution)

    session.add(
        AuditLog(
            engagement_id=eng.id,
            actor_type=ActorType.user,
            actor_id=str(user.id),
            event_type="run.requested",
            payload={
                "thread_id": str(thread_id),
                "prompt_len": len(body.prompt),
                "model": {
                    "provider": effective_model.provider,
                    "name": effective_model.name,
                },
            },
        )
    )
    # Lease, audit, execution row, and durable command commit atomically.
    # The envelope carries actor identity, never the plaintext provider key.
    outbox = enqueue_command(
        session,
        idempotency_key=f"run.start:{thread_id}",
        engagement_id=eng.id,
        stream_name=inbound_stream(eng.id),
        payload={
            "type": "run.start",
            "thread_id": str(thread_id),
            "prompt": body.prompt,
            "model": {
                "provider": effective_model.provider,
                "name": effective_model.name,
            },
            "acting_user_id": str(user.id),
            "mcp_url": mcp_url,
            "lease_token": str(lease.id),
        },
    )
    session.commit()
    publish_entry(session, redis_client, outbox.id)

    return RunStartResponse(
        engagement_id=eng.id,
        thread_id=thread_id,
        events_stream=outbound_stream(eng.id),
        model=effective_model,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Diagnostics dump — copy-pastable markdown of everything that happened on an
# engagement. Built for runtime triage: paste into an agent prompt to see the
# full state (runs, errors, audit, counts) without running queries by hand.
# ─────────────────────────────────────────────────────────────────────────────
def _diag_v(x: object) -> str:
    """Stringify a StrEnum or scalar for the dump."""
    return x.value if hasattr(x, "value") else str(x)


def _diag_iso(dt: datetime | None) -> str:
    return dt.isoformat(timespec="seconds") if dt else "—"


def _diag_dur(start: datetime | None, end: datetime | None) -> str:
    if not start or not end:
        return "—"
    secs = (end - start).total_seconds()
    if secs < 60:
        return f"{secs:.0f}s"
    if secs < 3600:
        return f"{secs / 60:.1f}m"
    return f"{secs / 3600:.1f}h"


def _diag_money(centsish: object) -> str:
    # cost_usd is Numeric(10,6); render as $x.xxxx or —
    if centsish is None:
        return "—"
    try:
        return f"${float(centsish):.4f}"
    except (TypeError, ValueError):
        return str(centsish)


def _diagnostics_markdown(session: DbSession, eng: Engagement) -> str:
    from collections import Counter

    from app.models import (
        AgentExecution,
        AuditLog,
        EngagementObjective,
        Entity,
        Finding,
        Suggestion,
        Task,
        WorkItem,
    )

    eid = eng.id
    out: list[str] = []
    w = out.append

    w(f"# Diagnostics — {eng.name}")
    w("")
    w(f"- **slug**: `{eng.slug}`  **id**: `{eid}`")
    w(f"- **status**: {_diag_v(eng.status)}  **work_state**: {_diag_v(eng.work_state)}")
    w(f"- **time_frame**: {_diag_v(eng.time_frame)}  **created**: {_diag_iso(eng.created_at)}")

    # ── Objectives ──────────────────────────────────────────────────────────
    objs = (
        session.execute(
            select(EngagementObjective)
            .where(EngagementObjective.engagement_id == eid)
            .order_by(EngagementObjective.display_order, EngagementObjective.created_at)
        )
        .scalars()
        .all()
    )
    w("")
    w(f"## Objectives ({len(objs)})")
    for o in objs:
        w(f"- [{_diag_v(o.status)}] {o.title}")

    # ── Work items ──────────────────────────────────────────────────────────
    wi_status = Counter(
        _diag_v(r)
        for r in session.execute(
            select(WorkItem.status).where(WorkItem.engagement_id == eid)
        ).scalars()
    )
    w("")
    w(f"## Work items — {sum(wi_status.values())} total")
    w("by status: " + (" | ".join(f"{k}: {v}" for k, v in sorted(wi_status.items())) or "—"))
    wi_recent = (
        session.execute(
            select(WorkItem)
            .where(WorkItem.engagement_id == eid)
            .order_by(WorkItem.created_at.desc())
            .limit(15)
        )
        .scalars()
        .all()
    )
    for wi in wi_recent:
        w(f"- [{_diag_v(wi.status)}] {wi.title}  ({_diag_v(wi.executor_type)})")

    # ── Findings ────────────────────────────────────────────────────────────
    f_sev = Counter(
        _diag_v(r)
        for r in session.execute(
            select(Finding.severity).where(Finding.engagement_id == eid)
        ).scalars()
    )
    f_stat = Counter(
        _diag_v(r)
        for r in session.execute(
            select(Finding.status).where(Finding.engagement_id == eid)
        ).scalars()
    )
    f_phase = Counter(
        _diag_v(r)
        for r in session.execute(
            select(Finding.phase).where(Finding.engagement_id == eid)
        ).scalars()
    )
    w("")
    w(f"## Findings — {sum(f_sev.values())} total")
    w("by severity: " + (" | ".join(f"{k}: {v}" for k, v in sorted(f_sev.items())) or "—"))
    w("by status: " + (" | ".join(f"{k}: {v}" for k, v in sorted(f_stat.items())) or "—"))
    w("by phase: " + (" | ".join(f"{k}: {v}" for k, v in sorted(f_phase.items())) or "—"))

    # ── Suggestions ─────────────────────────────────────────────────────────
    s_stat = Counter(
        _diag_v(r)
        for r in session.execute(
            select(Suggestion.status).where(Suggestion.engagement_id == eid)
        ).scalars()
    )
    s_kind = Counter(
        _diag_v(r)
        for r in session.execute(
            select(Suggestion.kind).where(Suggestion.engagement_id == eid)
        ).scalars()
    )
    w("")
    w(f"## Suggestions — {sum(s_stat.values())} total")
    w("by status: " + (" | ".join(f"{k}: {v}" for k, v in sorted(s_stat.items())) or "—"))
    w("by kind: " + (" | ".join(f"{k}: {v}" for k, v in sorted(s_kind.items())) or "—"))

    # ── Tasks ───────────────────────────────────────────────────────────────
    t_stat = Counter(
        _diag_v(r)
        for r in session.execute(select(Task.status).where(Task.engagement_id == eid)).scalars()
    )
    w("")
    w(f"## Tasks — {sum(t_stat.values())} total")
    w("by status: " + (" | ".join(f"{k}: {v}" for k, v in sorted(t_stat.items())) or "—"))

    # ── Entities ────────────────────────────────────────────────────────────
    ent_n = session.execute(
        select(func.count()).select_from(Entity).where(Entity.engagement_id == eid)
    ).scalar_one()
    w("")
    w(f"## Entities — {ent_n}")

    # ── Runs (agent_executions) ─────────────────────────────────────────────
    run_status = Counter(
        _diag_v(r)
        for r in session.execute(
            select(AgentExecution.status).where(AgentExecution.engagement_id == eid)
        ).scalars()
    )
    w("")
    w(f"## Runs (agent_executions) — {sum(run_status.values())} total")
    w("by status: " + (" | ".join(f"{k}: {v}" for k, v in sorted(run_status.items())) or "—"))
    runs = (
        session.execute(
            select(AgentExecution)
            .where(AgentExecution.engagement_id == eid)
            .order_by(AgentExecution.started_at.desc())
            .limit(40)
        )
        .scalars()
        .all()
    )
    if runs:
        w("")
        w("### recent runs")
        w("| # | agent | trigger | status | model | tok(in/out) | cost | dur |")
        w("|---|---|---|---|---|---|---|---|")
        for i, run in enumerate(runs, 1):
            model = (
                f"{run.model_provider}/{run.model_name}" if run.model_name else _diag_v(run.agent)
            )
            tok = (
                f"{run.tokens_in}/{run.tokens_out}"
                if (run.tokens_in is not None or run.tokens_out is not None)
                else "—"
            )
            w(
                f"| {i} | {_diag_v(run.agent)} | {_diag_v(run.trigger)} | "
                f"**{_diag_v(run.status)}** | {model} | {tok} | "
                f"{_diag_money(run.cost_usd)} | {_diag_dur(run.started_at, run.completed_at)} |"
            )
    failed = [r for r in runs if _diag_v(r.status) == "failed" and r.error]
    if failed:
        w("")
        w(f"### failed-run errors ({len(failed)})")
        for r in failed:
            w(f"- **{_diag_v(r.agent)}** ({_diag_v(r.trigger)}):")
            w("  ```")
            w((r.error or "").strip()[:1500])
            w("```")

    # ── Audit log ───────────────────────────────────────────────────────────
    audit = (
        session.execute(
            select(AuditLog)
            .where(AuditLog.engagement_id == eid)
            .order_by(AuditLog.created_at.desc())
            .limit(60)
        )
        .scalars()
        .all()
    )
    w("")
    w(f"## Audit log — {len(audit)} most recent")
    for a in audit:
        payload = json.dumps(a.payload, default=str, separators=(",", ":"))
        if len(payload) > 220:
            payload = payload[:220] + "…"
        head = (
            f"- {_diag_iso(a.created_at)} "
            f"[{_diag_v(a.actor_type)}:{a.actor_id or '—'}] `{a.event_type}`"
        )
        w(f"{head} {payload}")

    return "\n".join(out)


@router.get("/engagements/{slug}/diagnostics")
def get_engagement_diagnostics(
    slug: str,
    session: DbSession,
    _user: CurrentUser,
) -> dict[str, str]:
    """Copy-pastable markdown dump of everything that happened on this engagement.

    Surfaces runs (with errors), audit log, and record counts so an agent (or
    analyst) can diagnose what's going on behind the scenes at runtime.
    """
    eng = _get_engagement_or_404(session, slug)
    return {
        "engagement_id": str(eng.id),
        "engagement_slug": eng.slug,
        "generated_at": datetime.now(tz=UTC).isoformat(timespec="seconds"),
        "markdown": _diagnostics_markdown(session, eng),
    }
