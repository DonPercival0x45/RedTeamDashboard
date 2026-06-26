"""Observation endpoints for Project X-Ray.

Endpoints::

    GET    /projects/{slug}/observations              -> list observations
    POST   /projects/{slug}/observations              -> create observation
    DELETE /observations/{observation_id}             -> delete observation
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.models import Observation
from app.observations.schemas import ObservationCreate, ObservationRead
from app.projects.routes import _get_project_or_404, _reject_flushed

router = APIRouter()


# ---------------------------------------------------------------------------
# Observations
# ---------------------------------------------------------------------------


@router.get("/projects/{slug}/observations", response_model=list[ObservationRead])
def list_observations(slug: str, session: DbSession) -> list[Observation]:
    eng = _get_project_or_404(session, slug)
    return list(
        session.execute(
            select(Observation)
            .where(Observation.project_id == eng.id)
            .order_by(Observation.created_at)
        ).scalars()
    )


@router.post(
    "/projects/{slug}/observations",
    response_model=ObservationRead,
    status_code=status.HTTP_201_CREATED,
)
def create_observation(
    slug: str,
    body: ObservationCreate,
    session: DbSession,
    user: CurrentUser,
) -> Observation:
    eng = _get_project_or_404(session, slug)
    _reject_flushed(eng)
    obs = Observation(
        project_id=eng.id,
        content=body.content,
        phase=body.phase,
        created_by=user.id,
    )
    session.add(obs)
    session.commit()
    session.refresh(obs)
    return obs


@router.delete("/observations/{observation_id}", status_code=204)
def delete_observation(
    observation_id: uuid.UUID,
    session: DbSession,
    _user: CurrentUser,
) -> Response:
    obs = session.get(Observation, observation_id)
    if obs is None:
        raise HTTPException(status_code=404, detail="observation not found")
    session.delete(obs)
    session.commit()
    return Response(status_code=204)
