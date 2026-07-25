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
    Playbook,
    PlaybookExecutorKind,
    PlaybookRun,
    PlaybookStep,
    ScopeItem,
)
from app.schemas.playbook import (
    PlaybookApprovalPayload,
    PlaybookCreatePayload,
    PlaybookDetail,
    PlaybookPatchPayload,
    PlaybookRead,
    PlaybookRunPayload,
    PlaybookRunRead,
    PlaybookStepCreatePayload,
    PlaybookStepPatchPayload,
    PlaybookStepRead,
)
from app.services.playbook import (
    PlaybookHasRunsError,
    PlaybookSlugConflictError,
    RunNotAwaitingApprovalError,
    RunNotCancellableError,
    StepNotFoundError,
    add_step,
    approve_run,
    cancel_run,
    catalog,
    create_playbook,
    delete_playbook,
    delete_step,
    enqueue_run,
    load_seed_playbooks,
    reject_run,
    update_playbook,
    update_step,
)
from app.services.playbook.executor import executor_kinds_for_tools
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
    return _executor_for_tool_slugs(
        [step.tool_slug for step in playbook.steps]
    )


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
    return any(
        bool((step.args_template or {}).get("__target_source"))
        for step in playbook.steps
    )


def _ensure_recipe_mutable(session: Session, playbook: Playbook) -> None:
    has_run = session.execute(
        select(PlaybookRun.id)
        .where(PlaybookRun.playbook_id == playbook.id)
        .limit(1)
    ).scalar_one_or_none()
    if has_run is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                "playbook recipes are immutable after their first run; "
                "create a new version before changing metadata or steps"
            ),
        )


def _engagement_by_slug(session: Session, slug: str) -> Engagement:
    eng = session.execute(
        select(Engagement).where(Engagement.slug == slug)
    ).scalar_one_or_none()
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


def _validate_scope_subset(
    session: Session,
    engagement: Engagement,
    scope_subset: list[str],
    *,
    asset_class: str,
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
        session.execute(
            select(ScopeItem).where(ScopeItem.engagement_id == engagement.id)
        ).scalars()
    )
    rejected: list[str] = []
    for raw in scope_subset:
        value = str(raw).strip()
        if not value:
            rejected.append("(empty value)")
            continue
        kind = infer_scope_kind(value)
        if asset_class == "scope":
            exact_include = next(
                (
                    item
                    for item in items
                    if not item.is_exclusion
                    and item.kind == kind
                    and item.value == value
                ),
                None,
            )
            if exact_include is None:
                rejected.append(
                    f"{value!r} (scope review requires an exact include row)"
                )
                continue
        elif kind.value != asset_class:
            rejected.append(
                f"{value!r} (kind {kind.value!r} is incompatible with "
                f"playbook asset class {asset_class!r})"
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


def _run_read(session: Session, run: PlaybookRun) -> PlaybookRunRead:
    """Assemble the read model — playbook slug/version come from a join."""
    playbook = session.get(Playbook, run.playbook_id)
    return PlaybookRunRead(
        id=run.id,
        engagement_id=run.engagement_id,
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
        requested_by=run.requested_by,
        approved_by=run.approved_by,
        approved_at=run.approved_at,
        approval_reason=run.approval_reason,
        rejected_by=run.rejected_by,
        rejected_at=run.rejected_at,
        rejection_reason=run.rejection_reason,
    )


@router.get("/playbooks", response_model=list[PlaybookRead])
def list_playbooks(
    session: DbSession,
    _user: CurrentUser,
) -> list[PlaybookRead]:
    """List every catalog entry with a step count. Auto-installs seeds on
    first call so a fresh deployment surfaces the OSINT + PTES starters
    without a separate provisioning step."""
    load_seed_playbooks(session)
    session.commit()
    counts_stmt = (
        select(
            PlaybookStep.playbook_id,
            func.count(PlaybookStep.id).label("count"),
        ).group_by(PlaybookStep.playbook_id)
    )
    counts = {row[0]: row[1] for row in session.execute(counts_stmt).all()}
    catalog_rows = list(
        session.execute(
            select(Playbook).order_by(Playbook.slug, Playbook.version.desc())
        ).scalars()
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
        PlaybookRead(
            id=p.id,
            slug=p.slug,
            version=p.version,
            name=p.name,
            description=p.description,
            applies_to_asset_class=p.applies_to_asset_class,
            active=p.active,
            step_count=counts.get(p.id, 0),
            required_executor=_required_executor(p).value,
            required_credentials=_required_credentials(p),
            step_preview=_step_preview(p),
            expands_targets=_expands_targets(p),
            execution_paths=_execution_paths(p),
        )
        for p in playbooks
    ]


@router.post("/playbooks", response_model=PlaybookDetail, status_code=201)
def create_playbook_endpoint(
    payload: PlaybookCreatePayload,
    session: DbSession,
    _user: CurrentNonGuestUser,
) -> PlaybookDetail:
    """A5b: create a new analyst-authored playbook at version 1.

    The seed loader still owns shipped catalog entries; this endpoint lets
    analysts author their own alongside. Slug uniqueness is enforced at the
    DB and pre-checked in the service so the response is a friendly 409 on
    conflict rather than an IntegrityError leak.
    """
    try:
        pb = create_playbook(
            session,
            slug=payload.slug,
            name=payload.name,
            applies_to_asset_class=payload.applies_to_asset_class,
            description=payload.description,
            active=payload.active,
        )
    except PlaybookSlugConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.commit()
    session.refresh(pb)
    return PlaybookDetail(
        id=pb.id,
        slug=pb.slug,
        version=pb.version,
        name=pb.name,
        description=pb.description,
        applies_to_asset_class=pb.applies_to_asset_class,
        active=pb.active,
        step_count=0,
        required_executor=_required_executor(pb).value,
        required_credentials=_required_credentials(pb),
        step_preview=_step_preview(pb),
        expands_targets=_expands_targets(pb),
        execution_paths=_execution_paths(pb),
        steps=[],
    )


@router.patch("/playbooks/{slug}", response_model=PlaybookDetail)
def update_playbook_endpoint(
    slug: str,
    payload: PlaybookPatchPayload,
    session: DbSession,
    _user: CurrentNonGuestUser,
) -> PlaybookDetail:
    """A5b: patch metadata in place. Targets the latest version of the slug."""
    playbook = catalog.get_by_slug(session, slug)
    if playbook is None:
        raise HTTPException(status_code=404, detail=f"playbook '{slug}' not found")
    _ensure_recipe_mutable(session, playbook)
    update_playbook(
        session,
        playbook=playbook,
        name=payload.name,
        description=payload.description,
        active=payload.active,
        applies_to_asset_class=payload.applies_to_asset_class,
    )
    session.commit()
    session.refresh(playbook)
    return PlaybookDetail(
        id=playbook.id,
        slug=playbook.slug,
        version=playbook.version,
        name=playbook.name,
        description=playbook.description,
        applies_to_asset_class=playbook.applies_to_asset_class,
        active=playbook.active,
        step_count=len(playbook.steps),
        required_executor=_required_executor(playbook).value,
        required_credentials=_required_credentials(playbook),
        step_preview=_step_preview(playbook),
        expands_targets=_expands_targets(playbook),
        execution_paths=_execution_paths(playbook),
        steps=[PlaybookStepRead.model_validate(s) for s in playbook.steps],
    )


@router.delete("/playbooks/{slug}", status_code=204)
def delete_playbook_endpoint(
    slug: str,
    session: DbSession,
    _user: CurrentNonGuestUser,
) -> Response:
    """A5b: delete the latest version. Refuses (409) when runs reference it —
    the FK is RESTRICT so Postgres would reject anyway; we surface it first."""
    playbook = catalog.get_by_slug(session, slug)
    if playbook is None:
        raise HTTPException(status_code=404, detail=f"playbook '{slug}' not found")
    try:
        delete_playbook(session, playbook=playbook)
    except PlaybookHasRunsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.commit()
    return Response(status_code=204)


@router.post(
    "/playbooks/{slug}/steps",
    response_model=PlaybookStepRead,
    status_code=201,
)
def add_step_endpoint(
    slug: str,
    payload: PlaybookStepCreatePayload,
    session: DbSession,
    _user: CurrentNonGuestUser,
) -> PlaybookStepRead:
    """A5b: append a step. Omit ``sort_order`` to auto-place after the
    current highest — the service adds +10 gap so future inserts have room."""
    playbook = catalog.get_by_slug(session, slug)
    if playbook is None:
        raise HTTPException(status_code=404, detail=f"playbook '{slug}' not found")
    _ensure_recipe_mutable(session, playbook)
    _executor_for_tool_slugs(
        [step.tool_slug for step in playbook.steps] + [payload.tool_slug]
    )
    step = add_step(
        session,
        playbook=playbook,
        tool_slug=payload.tool_slug,
        args_template=payload.args_template,
        satisfies_node_ids=payload.satisfies_node_ids,
        sort_order=payload.sort_order,
        description=payload.description,
    )
    session.commit()
    session.refresh(step)
    return PlaybookStepRead.model_validate(step)


@router.patch(
    "/playbooks/{slug}/steps/{step_id}",
    response_model=PlaybookStepRead,
)
def update_step_endpoint(
    slug: str,
    step_id: uuid.UUID,
    payload: PlaybookStepPatchPayload,
    session: DbSession,
    _user: CurrentNonGuestUser,
) -> PlaybookStepRead:
    """A5b: patch a step in place. Change ``sort_order`` to reorder within
    the playbook."""
    playbook = catalog.get_by_slug(session, slug)
    if playbook is None:
        raise HTTPException(status_code=404, detail=f"playbook '{slug}' not found")
    _ensure_recipe_mutable(session, playbook)
    try:
        current_step = next(
            step for step in playbook.steps if step.id == step_id
        )
    except StopIteration as exc:
        raise HTTPException(status_code=404, detail=f"step {step_id} not found") from exc
    candidate_slug = payload.tool_slug or current_step.tool_slug
    _executor_for_tool_slugs(
        [
            candidate_slug if step.id == step_id else step.tool_slug
            for step in playbook.steps
        ]
    )
    try:
        step = update_step(
            session,
            playbook=playbook,
            step_id=step_id,
            tool_slug=payload.tool_slug,
            args_template=payload.args_template,
            satisfies_node_ids=payload.satisfies_node_ids,
            sort_order=payload.sort_order,
            description=payload.description,
        )
    except StepNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    session.commit()
    session.refresh(step)
    return PlaybookStepRead.model_validate(step)


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
    """A5b: remove a step. Adjacent steps keep their sort_order; the runner
    iterates by ORDER BY sort_order so gaps don't matter."""
    playbook = catalog.get_by_slug(session, slug)
    if playbook is None:
        raise HTTPException(status_code=404, detail=f"playbook '{slug}' not found")
    _ensure_recipe_mutable(session, playbook)
    try:
        delete_step(session, playbook=playbook, step_id=step_id)
    except StepNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    session.commit()
    return Response(status_code=204)


@router.get("/playbooks/{slug}", response_model=PlaybookDetail)
def get_playbook(
    slug: str,
    session: DbSession,
    _user: CurrentUser,
    version: int | None = None,
) -> PlaybookDetail:
    """One catalog entry with its full step list. Latest version by default."""
    playbook = catalog.get_by_slug(session, slug, version)
    if playbook is None:
        raise HTTPException(
            status_code=404, detail=f"playbook '{slug}' not found"
        )
    return PlaybookDetail(
        id=playbook.id,
        slug=playbook.slug,
        version=playbook.version,
        name=playbook.name,
        description=playbook.description,
        applies_to_asset_class=playbook.applies_to_asset_class,
        active=playbook.active,
        step_count=len(playbook.steps),
        required_executor=_required_executor(playbook).value,
        required_credentials=_required_credentials(playbook),
        step_preview=_step_preview(playbook),
        expands_targets=_expands_targets(playbook),
        execution_paths=_execution_paths(playbook),
        steps=[PlaybookStepRead.model_validate(s) for s in playbook.steps],
    )


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
    playbook = catalog.get_by_slug(session, payload.playbook_slug, payload.playbook_version)
    if playbook is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"playbook '{payload.playbook_slug}'"
                + (
                    f" version {payload.playbook_version}"
                    if payload.playbook_version is not None
                    else ""
                )
                + " not found"
            ),
        )
    # Persist requester identity because execution and milestone delivery happen
    # later in a worker process; never attempt to recover it from another user.
    required_executor = _required_executor(playbook)
    requested_executor = payload.executor or required_executor.value
    try:
        executor_kind = PlaybookExecutorKind(requested_executor)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=(
                f"executor must be one of "
                f"{sorted(k.value for k in PlaybookExecutorKind)}"
            ),
        ) from exc
    if executor_kind is not required_executor:
        raise HTTPException(
            status_code=422,
            detail=(
                f"playbook '{playbook.slug}' requires executor "
                f"'{required_executor.value}' for its configured tools"
            ),
        )
    # In-scope-only invariant (complaint 4b): every submitted target must be in
    # the engagement's declared scope and not match an exclusion, before we queue
    # anything for the worker to hand to tools.
    _validate_scope_subset(
        session,
        engagement,
        payload.scope_subset,
        asset_class=playbook.applies_to_asset_class,
    )
    run = enqueue_run(
        session,
        engagement=engagement,
        playbook=playbook,
        scope_subset=payload.scope_subset,
        executor_kind=executor_kind,
        requested_by=user.id,
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


@router.get("/playbook-runs/{run_id}", response_model=PlaybookRunRead)
def get_playbook_run(
    run_id: uuid.UUID,
    session: DbSession,
    _user: CurrentUser,
) -> PlaybookRunRead:
    run = session.get(PlaybookRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"playbook run {run_id} not found")
    return _run_read(session, run)
