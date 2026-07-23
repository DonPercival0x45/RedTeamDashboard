"""HTTP surface for the methodology catalog (Track A step A1).

Three endpoints:

* ``GET /methodologies`` — list catalog entries with node counts. Any logged-in
  analyst can browse.
* ``GET /methodologies/{slug}`` — full detail (all nodes). Optional
  ``?version=`` pin; default = latest for that slug.
* ``POST /engagements/{slug}/methodology`` — freeze a catalog entry into the
  engagement's snapshot. Non-guest only — analysts who write scope also pick
  the methodology.

The frontend picker + wizard integration come in a follow-up; this PR is the
backend surface only.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import CurrentNonGuestUser, CurrentUser, DbSession
from app.models import (
    ActorType,
    Engagement,
    MethodologyNode,
    User,
    UserRole,
)
from app.schemas.methodology import (
    EngagementMethodologyRead,
    MethodologyDetail,
    MethodologyNodeRead,
    MethodologyRead,
    MethodologySelectPayload,
)
from app.services import methodology as svc

router = APIRouter()


def _engagement_by_slug(session: Session, slug: str) -> Engagement:
    eng = session.execute(
        select(Engagement).where(Engagement.slug == slug)
    ).scalar_one_or_none()
    if eng is None:
        raise HTTPException(status_code=404, detail=f"engagement '{slug}' not found")
    return eng


def _actor_id(user: User) -> str:
    return str(user.id)


@router.get("/methodologies", response_model=list[MethodologyRead])
def list_methodologies(
    session: DbSession,
    _user: CurrentUser,
) -> list[MethodologyRead]:
    """List every catalog entry with a node count.

    Sorted by ``(slug, -version)`` so a slug's newest version surfaces first.
    Node count comes from a grouped join — one round-trip. Auto-installs the
    seed catalog on first call so a fresh deployment surfaces PTES, MITRE
    ATT&CK, and OSINT-minimal without a separate provisioning step (same
    pattern as the playbook catalog).
    """
    svc.load_seed_catalog(session)
    session.commit()
    counts_stmt = (
        select(
            MethodologyNode.methodology_id,
            func.count(MethodologyNode.id).label("count"),
        ).group_by(MethodologyNode.methodology_id)
    )
    counts = {row[0]: row[1] for row in session.execute(counts_stmt).all()}
    return [
        MethodologyRead(
            id=m.id,
            slug=m.slug,
            version=m.version,
            name=m.name,
            description=m.description,
            source_url=m.source_url,
            node_count=counts.get(m.id, 0),
        )
        for m in svc.list_catalog(session)
    ]


@router.get("/methodologies/{slug}", response_model=MethodologyDetail)
def get_methodology(
    slug: str,
    session: DbSession,
    _user: CurrentUser,
    version: int | None = None,
) -> MethodologyDetail:
    """One catalog entry with its full tree.

    ``version`` omitted → latest for this slug. 404 if absent or the slug
    exists but the requested version doesn't.
    """
    methodology = svc.get_by_slug(session, slug, version)
    if methodology is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"methodology '{slug}'"
                + (f" version {version}" if version is not None else "")
                + " not found"
            ),
        )
    return MethodologyDetail(
        id=methodology.id,
        slug=methodology.slug,
        version=methodology.version,
        name=methodology.name,
        description=methodology.description,
        source_url=methodology.source_url,
        nodes=[MethodologyNodeRead.model_validate(n) for n in methodology.nodes],
    )


@router.post(
    "/engagements/{slug}/methodology",
    response_model=EngagementMethodologyRead,
)
def select_methodology_for_engagement(
    slug: str,
    payload: MethodologySelectPayload,
    session: DbSession,
    user: CurrentNonGuestUser,
) -> EngagementMethodologyRead:
    """Freeze the given catalog entry into the engagement's snapshot.

    Idempotent per (engagement, methodology). Selecting a different
    methodology overwrites the snapshot + stamps a new timestamp + writes an
    audit row. 404 if the engagement or methodology don't exist.
    """
    engagement = _engagement_by_slug(session, slug)
    actor_type = ActorType.user if user.role != UserRole.guest else ActorType.system
    try:
        eng = svc.select_for_engagement(
            session,
            engagement_id=engagement.id,
            slug=payload.slug,
            version=payload.version,
            actor_type=actor_type,
            actor_id=_actor_id(user),
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    session.commit()
    session.refresh(eng)
    snapshot = eng.methodology_snapshot or {}
    return EngagementMethodologyRead(
        methodology_id=eng.methodology_id,
        slug=snapshot.get("slug"),
        version=snapshot.get("version"),
        selected_at=eng.methodology_selected_at,
        snapshot=snapshot or None,
    )


@router.get(
    "/engagements/{slug}/methodology",
    response_model=EngagementMethodologyRead,
)
def get_engagement_methodology(
    slug: str,
    session: DbSession,
    _user: CurrentUser,
) -> EngagementMethodologyRead:
    """Read the engagement's current methodology selection.

    Returns an empty-ish payload when nothing is selected yet — the frontend
    picker treats absence as "pick one." Not a 404: the engagement exists;
    the methodology is just unset.
    """
    engagement = _engagement_by_slug(session, slug)
    snapshot = engagement.methodology_snapshot or {}
    return EngagementMethodologyRead(
        methodology_id=engagement.methodology_id,
        slug=snapshot.get("slug"),
        version=snapshot.get("version"),
        selected_at=engagement.methodology_selected_at,
        snapshot=snapshot or None,
    )
