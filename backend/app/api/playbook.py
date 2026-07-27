"""HTTP surface for playbook catalog + runs — Track A step A3b.

Endpoints:

* ``GET /playbooks`` — list catalog with step counts.
* ``GET /playbooks/{slug}`` — full tree; ``?version=`` pin.
* ``POST /engagements/{slug}/playbook-runs`` — kick a run (non-guest).
  Synchronously executes via ``services.playbook.runner.start_run`` +
  the default ``InternalExecutor``. Returns the completed run row so the
  client sees final status + counts.
* ``GET /engagements/{slug}/playbook-runs`` — list runs, newest first.
* ``GET /playbook-runs/{run_id}`` — detail.

Sync execution is fine for A3b's OSINT playbook (5 steps × dozens of scope
items = seconds, not minutes). The queue + async fan-out for 100k-entity
runs lands in A3c.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import CurrentNonGuestUser, CurrentUser, DbSession
from app.models import (
    ActorType,
    AuditLog,
    Engagement,
    EngagementArchitecture,
    EngagementStatus,
    EngagementWorkState,
    EvidenceArtifact,
    Playbook,
    PlaybookExecutorKind,
    PlaybookRun,
    PlaybookStep,
    PlaybookStepExecution,
    ScopeItem,
    UserRole,
)
from app.schemas.playbook import (
    EvidenceArtifactRead,
    EvidenceArtifactSummaryRead,
    PlaybookApprovalPayload,
    PlaybookCatalogOptionsRead,
    PlaybookCreatePayload,
    PlaybookDetail,
    PlaybookExecutionPlanRead,
    PlaybookNewVersionPayload,
    PlaybookPatchPayload,
    PlaybookPlanPayload,
    PlaybookRead,
    PlaybookRunPayload,
    PlaybookRunRead,
    PlaybookStepCreatePayload,
    PlaybookStepExecutionRead,
    PlaybookStepPatchPayload,
    PlaybookStepRead,
    PlaybookToolRead,
)
from app.services.playbook import (
    PlaybookHasRunsError,
    PlaybookSlugConflictError,
    RunNotAwaitingApprovalError,
    RunNotCancellableError,
    approve_run,
    cancel_run,
    catalog,
    create_authored_playbook,
    create_new_version,
    create_playbook,
    delete_playbook,
    enqueue_run,
    load_seed_playbooks,
    reject_run,
)
from app.services.playbook.executor import (
    executor_for_tool_slug,
    executor_kinds_for_tools,
)
from app.services.playbook.planning import build_execution_plan
from app.services.playbook.policy import (
    ENTITY_TYPES,
    MAX_PLAYBOOK_CALLS,
    PLAYBOOK_CATEGORIES,
    catalog_tool_specs,
    execution_target_kind,
    tool_spec,
)
from app.services.scope_matcher import evaluate_scope_candidates, infer_scope_kind

router = APIRouter()


def _executor_for_tool_slugs(tool_slugs: list[str]) -> PlaybookExecutorKind:
    """Validate the server-owned step plan and return its queue transport.

    Mixed recipes use the MCP queue path while the worker routes each step to
    its allowlisted executor. The persisted run enum stays backwards compatible.
    """
    try:
        kinds = executor_kinds_for_tools(tool_slugs)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return PlaybookExecutorKind.mcp if "mcp" in kinds else PlaybookExecutorKind.internal


def _required_executor(playbook: Playbook) -> PlaybookExecutorKind:
    return _executor_for_tool_slugs([step.tool_slug for step in playbook.steps])


def _validated_executor(
    playbook: Playbook,
    requested_executor: str | None,
) -> PlaybookExecutorKind:
    required = _required_executor(playbook)
    requested = requested_executor or required.value
    try:
        selected = PlaybookExecutorKind(requested)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=(
                f"executor must be one of {sorted(kind.value for kind in PlaybookExecutorKind)}"
            ),
        ) from exc
    if selected is not required:
        raise HTTPException(
            status_code=422,
            detail=(
                f"playbook '{playbook.slug}' requires executor "
                f"'{required.value}' for its configured tools"
            ),
        )
    return selected


def _playbook_for_payload(
    session: Session,
    payload: PlaybookPlanPayload,
) -> Playbook:
    playbook = catalog.get_by_slug(
        session,
        payload.playbook_slug,
        payload.playbook_version,
    )
    if playbook is None:
        version = (
            f" version {payload.playbook_version}" if payload.playbook_version is not None else ""
        )
        raise HTTPException(
            status_code=404,
            detail=f"playbook '{payload.playbook_slug}'{version} not found",
        )
    if not playbook.steps:
        raise HTTPException(
            status_code=409,
            detail=f"playbook '{playbook.slug}' has no executable steps",
        )
    return playbook


def _required_credentials(playbook: Playbook) -> list[str]:
    credential_tools = {"freeipapi", "ipinfo", "dehashed"}
    return sorted(
        {
            step.tool_slug.removeprefix("mcp_")
            for step in playbook.steps
            if step.tool_slug.removeprefix("mcp_") in credential_tools
        }
    )


def _step_preview(playbook: Playbook) -> list[str]:
    return [
        step.description or step.tool_slug.removeprefix("mcp_").replace("_", " ")
        for step in playbook.steps
    ]


def _execution_paths(playbook: Playbook) -> list[str]:
    try:
        kinds = executor_kinds_for_tools(step.tool_slug for step in playbook.steps)
    except ValueError:
        return []
    labels = {"internal": "Built-in", "mcp": "Connected service"}
    return [labels[kind] for kind in ("internal", "mcp") if kind in kinds]


def _expands_targets(playbook: Playbook) -> bool:
    return any(bool((step.args_template or {}).get("__target_source")) for step in playbook.steps)


def _catalog_read(
    session: Session,
    playbook: Playbook,
    *,
    can_write: bool,
    include_steps: bool = False,
    step_count: int | None = None,
    has_runs: bool | None = None,
) -> PlaybookRead | PlaybookDetail:
    if has_runs is None:
        has_runs = (
            session.execute(
                select(PlaybookRun.id).where(PlaybookRun.playbook_id == playbook.id).limit(1)
            ).scalar_one_or_none()
            is not None
        )
    payload = {
        "id": playbook.id,
        "slug": playbook.slug,
        "version": playbook.version,
        "name": playbook.name,
        "description": playbook.description,
        "applies_to_asset_class": playbook.applies_to_asset_class,
        "applicable_entity_types": list(playbook.applicable_entity_types or [])
        or [playbook.applies_to_asset_class],
        "category": playbook.category or "other",
        "origin": playbook.origin or "system",
        "created_by": playbook.created_by,
        "supersedes_id": playbook.supersedes_id,
        "can_edit": can_write,
        "has_runs": has_runs,
        "active": playbook.active,
        "step_count": len(playbook.steps) if step_count is None else step_count,
        "required_executor": _required_executor(playbook).value,
        "required_credentials": _required_credentials(playbook),
        "step_preview": _step_preview(playbook),
        "expands_targets": _expands_targets(playbook),
        "execution_paths": _execution_paths(playbook),
    }
    if include_steps:
        return PlaybookDetail(
            **payload,
            steps=[PlaybookStepRead.model_validate(step) for step in playbook.steps],
        )
    return PlaybookRead(**payload)


def _audit_catalog_change(
    session: Session,
    *,
    actor_id: uuid.UUID,
    event_type: str,
    playbook: Playbook,
    previous_id: uuid.UUID | None = None,
) -> None:
    recipe_steps: list[dict[str, object]] = []
    for step in sorted(playbook.steps, key=lambda item: (item.sort_order, str(item.id))):
        try:
            spec = tool_spec(step.tool_slug)
            risk = spec.risk
            credential = spec.credential
        except ValueError:
            risk = "unknown"
            credential = None
        recipe_steps.append(
            {
                "id": str(step.id),
                "sort_order": step.sort_order,
                "tool_slug": step.tool_slug,
                "transport": (
                    executor_for_tool_slug(step.tool_slug) if risk != "unknown" else "unknown"
                ),
                "risk": risk,
                "credential": credential,
            }
        )
    session.add(
        AuditLog(
            engagement_id=None,
            actor_type=ActorType.user,
            actor_id=str(actor_id),
            event_type=event_type,
            payload={
                "playbook_id": str(playbook.id),
                "slug": playbook.slug,
                "version": playbook.version,
                "origin": playbook.origin,
                "category": playbook.category,
                "applicable_entity_types": list(playbook.applicable_entity_types or []),
                "previous_id": str(previous_id) if previous_id else None,
                "steps": recipe_steps,
            },
        )
    )


def _engagement_by_slug(session: Session, slug: str) -> Engagement:
    eng = session.execute(select(Engagement).where(Engagement.slug == slug)).scalar_one_or_none()
    if eng is None:
        raise HTTPException(status_code=404, detail=f"engagement '{slug}' not found")
    return eng


def _ensure_playbook_engagement_mutable(engagement: Engagement) -> None:
    if engagement.intelligence_architecture != EngagementArchitecture.v3:
        raise HTTPException(status_code=409, detail="playbook runs require a v3 engagement")
    if engagement.status != EngagementStatus.active:
        raise HTTPException(status_code=409, detail="engagement is not active")
    if engagement.work_state == EngagementWorkState.completed:
        raise HTTPException(status_code=409, detail="completed engagement is read-only")


def _dedupe_scope_subset(values: list[str]) -> list[str]:
    """Preserve analyst order while preventing duplicate tool invocations."""
    return list(dict.fromkeys(str(value).strip() for value in values))


def _enforce_call_budget(playbook: Playbook, targets: list[str]) -> None:
    minimum_calls = len(playbook.steps) * len(targets)
    if minimum_calls > MAX_PLAYBOOK_CALLS:
        raise HTTPException(
            status_code=422,
            detail=(
                f"playbook plan requires {minimum_calls} calls; "
                f"the per-run limit is {MAX_PLAYBOOK_CALLS}"
            ),
        )


def _validate_scope_subset(
    session: Session,
    engagement: Engagement,
    scope_subset: list[str],
    *,
    applicable_entity_types: list[str] | None = None,
    asset_class: str | None = None,
) -> None:
    """Enforce the in-scope-only invariant on playbook run targets.

    ``scope_subset`` values are handed straight to playbook tools, so an
    unvalidated free-form string would let an analyst run a playbook against
    an out-of-engagement target. Require every value to be non-empty and to
    evaluate as *in scope* for the engagement (declared include, or a
    subdomain/IP inside one) and not match any exclusion. Reuses the canonical
    ``scope_matcher`` used by every other execution gate.
    """
    if not scope_subset:
        raise HTTPException(
            status_code=422,
            detail="scope_subset is required — pick at least one in-scope target.",
        )
    items = list(
        session.execute(select(ScopeItem).where(ScopeItem.engagement_id == engagement.id)).scalars()
    )
    target_kind = execution_target_kind(
        applicable_entity_types or ([asset_class] if asset_class else [])
    )
    rejected: list[str] = []
    for raw in scope_subset:
        value = str(raw).strip()
        if not value:
            rejected.append("(empty value)")
            continue
        kind = infer_scope_kind(value)
        if target_kind == "scope":
            exact_include = next(
                (
                    item
                    for item in items
                    if not item.is_exclusion and item.kind == kind and item.value == value
                ),
                None,
            )
            if exact_include is None:
                rejected.append(f"{value!r} (scope review requires an exact include row)")
                continue
        elif kind.value != target_kind:
            rejected.append(
                f"{value!r} (kind {kind.value!r} is incompatible with "
                f"playbook target kind {target_kind!r})"
            )
            continue
        match = evaluate_scope_candidates([(value, kind)], items)
        if not match.allowed:
            rejected.append(f"{value!r} ({match.reason})")
    if rejected:
        raise HTTPException(
            status_code=422,
            detail=(
                "scope_subset targets must be in the engagement scope and not "
                f"match an exclusion. Rejected: {'; '.join(rejected)}. "
                "Add the target to Scope first."
            ),
        )


def _step_execution_reads(session: Session, run_id: uuid.UUID) -> list[PlaybookStepExecutionRead]:
    rows = list(
        session.execute(
            select(PlaybookStepExecution)
            .where(PlaybookStepExecution.playbook_run_id == run_id)
            .order_by(
                PlaybookStepExecution.sort_order,
                PlaybookStepExecution.target,
                PlaybookStepExecution.attempt,
                PlaybookStepExecution.started_at,
                PlaybookStepExecution.id,
            )
        ).scalars()
    )
    if not rows:
        return []
    artifacts = {
        artifact.playbook_step_execution_id: artifact
        for artifact in session.execute(
            select(EvidenceArtifact).where(
                EvidenceArtifact.playbook_step_execution_id.in_([row.id for row in rows])
            )
        ).scalars()
        if artifact.playbook_step_execution_id is not None
    }
    return [
        PlaybookStepExecutionRead(
            id=row.id,
            playbook_step_id=row.playbook_step_id,
            sort_order=row.sort_order,
            tool_slug=row.tool_slug,
            target=row.target,
            transport=row.transport,
            attempt=row.attempt,
            status=row.status.value,
            arguments=dict(row.arguments or {}),
            started_at=row.started_at,
            completed_at=row.completed_at,
            duration_ms=row.duration_ms,
            error=row.error,
            evidence=(
                EvidenceArtifactSummaryRead(
                    id=artifact.id,
                    finding_id=artifact.finding_id,
                    sha256=artifact.sha256,
                    size_bytes=artifact.size_bytes,
                    truncated=artifact.truncated,
                    redacted=artifact.redacted,
                )
                if (artifact := artifacts.get(row.id)) is not None
                else None
            ),
        )
        for row in rows
    ]


def _run_read(
    session: Session,
    run: PlaybookRun,
    *,
    include_step_executions: bool = False,
) -> PlaybookRunRead:
    """Assemble the read model — playbook slug/version come from a join."""
    playbook = session.get(Playbook, run.playbook_id)
    engagement = session.get(Engagement, run.engagement_id)
    return PlaybookRunRead(
        id=run.id,
        engagement_id=run.engagement_id,
        engagement_slug=engagement.slug if engagement else "",
        playbook_id=run.playbook_id,
        playbook_slug=playbook.slug if playbook else "",
        playbook_version=playbook.version if playbook else 0,
        status=run.status.value,
        executor=run.executor_kind.value,
        scope_subset=list(run.scope_subset or []),
        started_at=run.started_at,
        completed_at=run.completed_at,
        steps_total=run.steps_total,
        steps_succeeded=run.steps_succeeded,
        steps_failed=run.steps_failed,
        findings_new=run.findings_new,
        findings_unvalidated=run.findings_unvalidated,
        findings_high_severity=run.findings_high_severity,
        findings_total=run.findings_total,
        last_error=run.last_error,
        plan_sha256=run.plan_sha256,
        planned_at=run.planned_at,
        execution_plan=(
            PlaybookExecutionPlanRead.model_validate(run.plan_snapshot)
            if include_step_executions and run.plan_snapshot
            else None
        ),
        requested_by=run.requested_by,
        approved_by=run.approved_by,
        approved_at=run.approved_at,
        approval_reason=run.approval_reason,
        rejected_by=run.rejected_by,
        rejected_at=run.rejected_at,
        rejection_reason=run.rejection_reason,
        step_executions=(_step_execution_reads(session, run.id) if include_step_executions else []),
    )


@router.get("/playbooks", response_model=list[PlaybookRead])
def list_playbooks(
    session: DbSession,
    user: CurrentUser,
) -> list[PlaybookRead]:
    """List every catalog entry with a step count. Auto-installs seeds on
    first call so a fresh deployment surfaces the OSINT + PTES starters
    without a separate provisioning step."""
    load_seed_playbooks(session)
    session.commit()
    counts_stmt = select(
        PlaybookStep.playbook_id,
        func.count(PlaybookStep.id).label("count"),
    ).group_by(PlaybookStep.playbook_id)
    counts = {row[0]: row[1] for row in session.execute(counts_stmt).all()}
    run_counts = {
        row[0]: row[1]
        for row in session.execute(
            select(PlaybookRun.playbook_id, func.count(PlaybookRun.id)).group_by(
                PlaybookRun.playbook_id
            )
        ).all()
    }
    catalog_rows = list(
        session.execute(select(Playbook).order_by(Playbook.slug, Playbook.version.desc())).scalars()
    )
    # The catalog is an action surface, not version history. Show only the
    # newest recipe per slug; pinned historical versions remain readable via
    # GET /playbooks/{slug}?version=N and existing runs retain their version.
    seen_slugs: set[str] = set()
    playbooks: list[Playbook] = []
    for playbook in catalog_rows:
        if playbook.slug in seen_slugs:
            continue
        seen_slugs.add(playbook.slug)
        playbooks.append(playbook)
    return [
        _catalog_read(
            session,
            playbook,
            can_write=user.role != UserRole.guest,
            step_count=counts.get(playbook.id, 0),
            has_runs=run_counts.get(playbook.id, 0) > 0,
        )
        for playbook in playbooks
    ]


@router.get("/playbook-catalog/options", response_model=PlaybookCatalogOptionsRead)
def playbook_catalog_options(_user: CurrentUser) -> PlaybookCatalogOptionsRead:
    """Return the server-owned authoring vocabulary and tool policy."""
    return PlaybookCatalogOptionsRead(
        categories=list(PLAYBOOK_CATEGORIES),
        entity_types=list(ENTITY_TYPES),
        tools=[
            PlaybookToolRead(
                slug=spec.slug,
                name=spec.name,
                description=spec.description,
                target_kinds=list(spec.target_kinds),
                transport=spec.transport,
                risk=spec.risk,
                credential=spec.credential,
            )
            for spec in catalog_tool_specs()
        ],
    )


@router.post("/playbooks", response_model=PlaybookDetail, status_code=201)
def create_playbook_endpoint(
    payload: PlaybookCreatePayload,
    session: DbSession,
    user: CurrentNonGuestUser,
) -> PlaybookDetail:
    """A5b: create a new analyst-authored playbook at version 1.

    The seed loader still owns shipped catalog entries; this endpoint lets
    analysts author their own alongside. Slug uniqueness is enforced at the
    DB and pre-checked in the service so the response is a friendly 409 on
    conflict rather than an IntegrityError leak.
    """
    try:
        if payload.steps:
            pb = create_authored_playbook(
                session,
                slug=payload.slug,
                name=payload.name,
                description=payload.description,
                category=payload.category,
                applicable_entity_types=payload.applicable_entity_types,
                active=payload.active,
                steps=[step.model_dump() for step in payload.steps],
                created_by=user.id,
            )
        else:
            # Compatibility for existing API clients. The in-app authoring
            # flow always sends at least one step atomically.
            pb = create_playbook(
                session,
                slug=payload.slug,
                name=payload.name,
                applies_to_asset_class=payload.applies_to_asset_class or "scope",
                applicable_entity_types=payload.applicable_entity_types,
                category=payload.category,
                description=payload.description,
                active=payload.active,
                created_by=user.id,
            )
    except PlaybookSlugConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _audit_catalog_change(
        session,
        actor_id=user.id,
        event_type="playbook.created",
        playbook=pb,
    )
    session.commit()
    session.refresh(pb)
    return _catalog_read(
        session,
        pb,
        can_write=True,
        include_steps=True,
        has_runs=False,
    )


@router.patch("/playbooks/{slug}", response_model=PlaybookDetail)
def update_playbook_endpoint(
    slug: str,
    _payload: PlaybookPatchPayload,
    session: DbSession,
    _user: CurrentNonGuestUser,
) -> PlaybookDetail:
    """Recipes are immutable; edits publish through the versions endpoint."""
    if catalog.get_by_slug(session, slug) is None:
        raise HTTPException(status_code=404, detail=f"playbook '{slug}' not found")
    raise HTTPException(
        status_code=409,
        detail="playbook versions are immutable; publish a new version instead",
    )


@router.post(
    "/playbooks/{slug}/versions",
    response_model=PlaybookDetail,
    status_code=201,
)
def create_playbook_version_endpoint(
    slug: str,
    payload: PlaybookNewVersionPayload,
    session: DbSession,
    user: CurrentNonGuestUser,
) -> PlaybookDetail:
    """Edit a shipped or executed recipe by publishing an immutable version."""
    try:
        playbook = create_new_version(
            session,
            slug=slug,
            expected_supersedes_id=payload.expected_supersedes_id,
            expected_version=payload.expected_version,
            name=payload.name,
            description=payload.description,
            category=payload.category,
            applicable_entity_types=payload.applicable_entity_types,
            active=payload.active,
            steps=[step.model_dump() for step in payload.steps],
            created_by=user.id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"playbook '{slug}' not found") from exc
    except PlaybookSlugConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail="the catalog changed while this version was being published; reload and retry",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _audit_catalog_change(
        session,
        actor_id=user.id,
        event_type="playbook.version_created",
        playbook=playbook,
        previous_id=payload.expected_supersedes_id,
    )
    session.commit()
    session.refresh(playbook)
    return _catalog_read(
        session,
        playbook,
        can_write=True,
        include_steps=True,
        has_runs=False,
    )


@router.delete("/playbooks/{slug}", status_code=204)
def delete_playbook_endpoint(
    slug: str,
    session: DbSession,
    user: CurrentNonGuestUser,
) -> Response:
    """A5b: delete the latest version. Refuses (409) when runs reference it —
    the FK is RESTRICT so Postgres would reject anyway; we surface it first."""
    playbook = catalog.get_by_slug(session, slug)
    if playbook is None:
        raise HTTPException(status_code=404, detail=f"playbook '{slug}' not found")
    if playbook.origin == "system":
        raise HTTPException(status_code=409, detail="shipped playbooks cannot be deleted")
    try:
        delete_playbook(session, playbook=playbook)
    except PlaybookHasRunsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _audit_catalog_change(
        session,
        actor_id=user.id,
        event_type="playbook.deleted",
        playbook=playbook,
    )
    session.commit()
    return Response(status_code=204)


@router.post(
    "/playbooks/{slug}/steps",
    response_model=PlaybookStepRead,
    status_code=201,
)
def add_step_endpoint(
    slug: str,
    _payload: PlaybookStepCreatePayload,
    session: DbSession,
    _user: CurrentNonGuestUser,
) -> PlaybookStepRead:
    """Step edits publish atomically through a complete new version."""
    if catalog.get_by_slug(session, slug) is None:
        raise HTTPException(status_code=404, detail=f"playbook '{slug}' not found")
    raise HTTPException(
        status_code=409,
        detail="playbook versions are immutable; publish a new version instead",
    )


@router.patch(
    "/playbooks/{slug}/steps/{step_id}",
    response_model=PlaybookStepRead,
)
def update_step_endpoint(
    slug: str,
    step_id: uuid.UUID,
    _payload: PlaybookStepPatchPayload,
    session: DbSession,
    _user: CurrentNonGuestUser,
) -> PlaybookStepRead:
    """Step edits publish atomically through a complete new version."""
    if catalog.get_by_slug(session, slug) is None:
        raise HTTPException(status_code=404, detail=f"playbook '{slug}' not found")
    raise HTTPException(
        status_code=409,
        detail="playbook versions are immutable; publish a new version instead",
    )


@router.delete(
    "/playbooks/{slug}/steps/{step_id}",
    status_code=204,
)
def delete_step_endpoint(
    slug: str,
    step_id: uuid.UUID,
    session: DbSession,
    _user: CurrentNonGuestUser,
) -> Response:
    """Step edits publish atomically through a complete new version."""
    if catalog.get_by_slug(session, slug) is None:
        raise HTTPException(status_code=404, detail=f"playbook '{slug}' not found")
    raise HTTPException(
        status_code=409,
        detail="playbook versions are immutable; publish a new version instead",
    )


@router.get("/playbooks/{slug}", response_model=PlaybookDetail)
def get_playbook(
    slug: str,
    session: DbSession,
    user: CurrentUser,
    version: int | None = None,
) -> PlaybookDetail:
    """One catalog entry with its full step list. Latest version by default."""
    playbook = catalog.get_by_slug(session, slug, version)
    if playbook is None:
        raise HTTPException(status_code=404, detail=f"playbook '{slug}' not found")
    return _catalog_read(
        session,
        playbook,
        can_write=user.role != UserRole.guest,
        include_steps=True,
    )


@router.post(
    "/engagements/{slug}/playbook-runs/plan",
    response_model=PlaybookExecutionPlanRead,
)
def plan_playbook_run(
    slug: str,
    payload: PlaybookPlanPayload,
    session: DbSession,
    _user: CurrentNonGuestUser,
) -> PlaybookExecutionPlanRead:
    """Return the authoritative plan without creating or executing a run."""
    engagement = _engagement_by_slug(session, slug)
    _ensure_playbook_engagement_mutable(engagement)
    playbook = _playbook_for_payload(session, payload)
    executor_kind = _validated_executor(playbook, payload.executor)
    targets = _dedupe_scope_subset(payload.scope_subset)
    _enforce_call_budget(playbook, targets)
    _validate_scope_subset(
        session,
        engagement,
        targets,
        applicable_entity_types=(
            list(playbook.applicable_entity_types or []) or [playbook.applies_to_asset_class]
        ),
    )
    plan = build_execution_plan(
        playbook=playbook,
        scope_subset=targets,
        required_executor=executor_kind.value,
    )
    return PlaybookExecutionPlanRead.model_validate(plan)


@router.post(
    "/engagements/{slug}/playbook-runs",
    response_model=PlaybookRunRead,
    status_code=202,
)
def create_playbook_run(
    slug: str,
    payload: PlaybookRunPayload,
    session: DbSession,
    user: CurrentNonGuestUser,
) -> PlaybookRunRead:
    """v3 A3c: enqueue a playbook run. Returns 202 with the pending row.

    The worker thread (``PlaybookWorkerThread`` in the worker process)
    picks up the row via ``SELECT ... FOR UPDATE SKIP LOCKED`` and drives
    it to completion. Clients poll ``GET /playbook-runs/{id}`` for
    ``status`` transitions or subscribe to the engagement's SSE stream for
    the ``collection.job.completed`` milestone at end-of-run.
    """
    engagement = _engagement_by_slug(session, slug)
    _ensure_playbook_engagement_mutable(engagement)
    playbook = _playbook_for_payload(session, payload)
    # Persist requester identity because execution and milestone delivery happen
    # later in a worker process; never attempt to recover it from another user.
    executor_kind = _validated_executor(playbook, payload.executor)
    targets = _dedupe_scope_subset(payload.scope_subset)
    _enforce_call_budget(playbook, targets)
    # In-scope-only invariant (complaint 4b): every submitted target must be in
    # the engagement's declared scope and not match an exclusion, before we queue
    # anything for the worker to hand to tools.
    _validate_scope_subset(
        session,
        engagement,
        targets,
        applicable_entity_types=(
            list(playbook.applicable_entity_types or []) or [playbook.applies_to_asset_class]
        ),
    )
    plan = build_execution_plan(
        playbook=playbook,
        scope_subset=targets,
        required_executor=executor_kind.value,
    )
    if payload.plan_sha256 is None:
        raise HTTPException(
            status_code=428,
            detail="preview and review the authoritative execution plan before starting",
        )
    if payload.plan_sha256 != plan["plan_sha256"]:
        raise HTTPException(
            status_code=409,
            detail=(
                "The execution plan changed after preview. Review the refreshed "
                "targets, transports, and approvals before starting the run."
            ),
        )
    run = enqueue_run(
        session,
        engagement=engagement,
        playbook=playbook,
        scope_subset=targets,
        executor_kind=executor_kind,
        requested_by=user.id,
        plan_snapshot=plan,
        plan_sha256=str(plan["plan_sha256"]),
    )
    session.commit()
    session.refresh(run)
    return _run_read(session, run)


@router.post("/playbook-runs/{run_id}/approve", response_model=PlaybookRunRead)
def approve_playbook_run(
    run_id: uuid.UUID,
    payload: PlaybookApprovalPayload,
    session: DbSession,
    user: CurrentNonGuestUser,
) -> PlaybookRunRead:
    """v3 A5: release an ``awaiting_approval`` run into ``pending``.

    Any non-guest can approve any awaiting run — the friction is the
    second-touch pause, not the identity check. Four-eyes and admin-only
    gating are open follow-ups if governance ever needs them.
    """
    try:
        run = approve_run(
            session,
            run_id=run_id,
            approver_id=user.id,
            reason=payload.reason,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"playbook run {run_id} not found") from exc
    except RunNotAwaitingApprovalError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if getattr(run, "_approval_transitioned", False):
        session.add(
            AuditLog(
                engagement_id=run.engagement_id,
                actor_type=ActorType.user,
                actor_id=str(user.id),
                event_type="playbook.approved",
                payload={
                    "playbook_run_id": str(run.id),
                    "status": run.status.value,
                    "reason": run.approval_reason,
                },
            )
        )
    session.commit()
    session.refresh(run)
    return _run_read(session, run)


@router.post("/playbook-runs/{run_id}/reject", response_model=PlaybookRunRead)
def reject_playbook_run(
    run_id: uuid.UUID,
    payload: PlaybookApprovalPayload,
    session: DbSession,
    user: CurrentNonGuestUser,
) -> PlaybookRunRead:
    """v3 A5: reject an ``awaiting_approval`` run; flips to ``cancelled``.

    Requires ``reason`` — an analyst-facing rejection needs a why so the
    requestor can act on it.
    """
    if not payload.reason or not payload.reason.strip():
        raise HTTPException(
            status_code=422,
            detail="reason is required when rejecting a playbook run",
        )
    try:
        run = reject_run(
            session,
            run_id=run_id,
            approver_id=user.id,
            reason=payload.reason.strip(),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"playbook run {run_id} not found") from exc
    except RunNotAwaitingApprovalError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if getattr(run, "_rejection_transitioned", False):
        session.add(
            AuditLog(
                engagement_id=run.engagement_id,
                actor_type=ActorType.user,
                actor_id=str(user.id),
                event_type="playbook.rejected",
                payload={
                    "playbook_run_id": str(run.id),
                    "status": run.status.value,
                    "reason": run.rejection_reason,
                },
            )
        )
    session.commit()
    session.refresh(run)
    return _run_read(session, run)


@router.post("/playbook-runs/{run_id}/cancel", response_model=PlaybookRunRead)
def cancel_playbook_run(
    run_id: uuid.UUID,
    session: DbSession,
    user: CurrentNonGuestUser,
) -> PlaybookRunRead:
    """v3 A3c: cancel a pending or running run.

    * Pending → cancelled immediately; the worker's next claim skips it.
    * Running → cancelled; the worker's runner checks status between steps
      and bails cleanly.
    * Terminal → 409 conflict.
    """
    try:
        run = cancel_run(session, run_id=run_id, reason="cancelled by analyst")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"playbook run {run_id} not found") from exc
    except RunNotCancellableError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.add(
        AuditLog(
            engagement_id=run.engagement_id,
            actor_type=ActorType.user,
            actor_id=str(user.id),
            event_type="playbook.cancelled",
            payload={"playbook_run_id": str(run.id), "status": run.status.value},
        )
    )
    session.commit()
    session.refresh(run)
    return _run_read(session, run)


@router.get(
    "/engagements/{slug}/playbook-runs",
    response_model=list[PlaybookRunRead],
)
def list_playbook_runs(
    slug: str,
    session: DbSession,
    _user: CurrentUser,
    limit: int = 50,
    status: str | None = None,
) -> list[PlaybookRunRead]:
    """List runs, newest first. Optional ``?status=`` filter (e.g.
    ``awaiting_approval`` for the approval queue view)."""
    from app.models import PlaybookRunStatus

    engagement = _engagement_by_slug(session, slug)
    stmt = (
        select(PlaybookRun)
        .where(PlaybookRun.engagement_id == engagement.id)
        .order_by(PlaybookRun.created_at.desc())
        .limit(limit)
    )
    if status is not None:
        try:
            stmt = stmt.where(PlaybookRun.status == PlaybookRunStatus(status))
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"unknown status filter: {status!r}",
            ) from exc
    rows = session.execute(stmt).scalars().all()
    return [_run_read(session, r) for r in rows]


@router.get(
    "/evidence-artifacts/{artifact_id}",
    response_model=EvidenceArtifactRead,
)
def get_evidence_artifact(
    artifact_id: uuid.UUID,
    session: DbSession,
    _user: CurrentUser,
) -> EvidenceArtifactRead:
    artifact = session.get(EvidenceArtifact, artifact_id)
    if artifact is None:
        raise HTTPException(
            status_code=404,
            detail=f"evidence artifact {artifact_id} not found",
        )
    return EvidenceArtifactRead(
        id=artifact.id,
        engagement_id=artifact.engagement_id,
        playbook_run_id=artifact.playbook_run_id,
        playbook_step_execution_id=artifact.playbook_step_execution_id,
        finding_id=artifact.finding_id,
        kind=artifact.kind,
        source_tool=artifact.source_tool,
        target=artifact.target,
        payload=dict(artifact.payload or {}),
        sha256=artifact.sha256,
        size_bytes=artifact.size_bytes,
        truncated=artifact.truncated,
        redacted=artifact.redacted,
        captured_at=artifact.captured_at,
    )


@router.get("/playbook-runs/{run_id}", response_model=PlaybookRunRead)
def get_playbook_run(
    run_id: uuid.UUID,
    session: DbSession,
    _user: CurrentUser,
) -> PlaybookRunRead:
    run = session.get(PlaybookRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"playbook run {run_id} not found")
    return _run_read(session, run, include_step_executions=True)
