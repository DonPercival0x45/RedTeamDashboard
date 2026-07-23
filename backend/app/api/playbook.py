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

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import CurrentNonGuestUser, CurrentUser, DbSession
from app.models import (
    ActorType,
    Engagement,
    Playbook,
    PlaybookRun,
    PlaybookStep,
    User,
    UserRole,
)
from app.schemas.playbook import (
    PlaybookDetail,
    PlaybookRead,
    PlaybookRunPayload,
    PlaybookRunRead,
    PlaybookStepRead,
)
from app.services.playbook import (
    InternalExecutor,
    catalog,
    load_seed_playbooks,
    start_run,
)

router = APIRouter()


def _engagement_by_slug(session: Session, slug: str) -> Engagement:
    eng = session.execute(
        select(Engagement).where(Engagement.slug == slug)
    ).scalar_one_or_none()
    if eng is None:
        raise HTTPException(status_code=404, detail=f"engagement '{slug}' not found")
    return eng


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
    playbooks = session.execute(
        select(Playbook).order_by(Playbook.slug, Playbook.version.desc())
    ).scalars()
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
        )
        for p in playbooks
    ]


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
        steps=[PlaybookStepRead.model_validate(s) for s in playbook.steps],
    )


def _actor_type(user: User) -> ActorType:
    return ActorType.user if user.role != UserRole.guest else ActorType.system


@router.post(
    "/engagements/{slug}/playbook-runs",
    response_model=PlaybookRunRead,
    status_code=201,
)
def create_playbook_run(
    slug: str,
    payload: PlaybookRunPayload,
    session: DbSession,
    user: CurrentNonGuestUser,
) -> PlaybookRunRead:
    """Kick a playbook run and return the completed row."""
    engagement = _engagement_by_slug(session, slug)
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
    run = start_run(
        session,
        engagement=engagement,
        playbook=playbook,
        scope_subset=payload.scope_subset,
        executor=InternalExecutor(),
        actor_type=_actor_type(user),
        actor_id=str(user.id),
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
) -> list[PlaybookRunRead]:
    engagement = _engagement_by_slug(session, slug)
    rows = session.execute(
        select(PlaybookRun)
        .where(PlaybookRun.engagement_id == engagement.id)
        .order_by(PlaybookRun.created_at.desc())
        .limit(limit)
    ).scalars().all()
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
