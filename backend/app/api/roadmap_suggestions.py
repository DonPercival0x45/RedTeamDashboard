"""HTTP surface for the tenant-global "suggestion box" feature.

Endpoints::

    POST   /roadmap-suggestions               -> create + agent evaluate
    GET    /roadmap-suggestions               -> list (optional ?status= filter)
    GET    /roadmap-suggestions/export        -> ROADMAP.md of approved items
    GET    /roadmap-suggestions/{id}          -> read one
    PATCH  /roadmap-suggestions/{id}/decision -> approve/reject (admin-only)
    DELETE /roadmap-suggestions/{id}          -> author-while-pending or admin

Read-side is open to any authenticated user. Decision and admin-delete are
gated by ``users.is_admin``.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Query, Response, status
from fastapi.responses import PlainTextResponse
from sqlalchemy import select

from app.agents.planner import PlanningAgent, render_approved_roadmap
from app.api.deps import CurrentAdminUser, CurrentUser, DbSession, RedisClient
from app.models import (
    ActorType,
    AuditLog,
    RoadmapSuggestion,
    RoadmapSuggestionStatus,
)
from app.schemas.roadmap_suggestion import (
    RoadmapSuggestionCreate,
    RoadmapSuggestionDecision,
    RoadmapSuggestionRead,
)

logger = structlog.get_logger(__name__)

router = APIRouter()


def _audit(
    session: DbSession,
    user_id: uuid.UUID,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    session.add(
        AuditLog(
            engagement_id=None,
            actor_type=ActorType.user,
            actor_id=str(user_id),
            event_type=event_type,
            payload=payload,
        )
    )


def _load_all(session: DbSession) -> list[RoadmapSuggestion]:
    return list(
        session.execute(
            select(RoadmapSuggestion).order_by(RoadmapSuggestion.created_at)
        ).scalars()
    )


# ── create + agent evaluate ──────────────────────────────────────────────


@router.post(
    "/roadmap-suggestions",
    response_model=RoadmapSuggestionRead,
    status_code=status.HTTP_201_CREATED,
)
def create_suggestion(
    body: RoadmapSuggestionCreate,
    session: DbSession,
    redis_client: RedisClient,
    user: CurrentUser,
) -> RoadmapSuggestion:
    approved_md = render_approved_roadmap(_load_all(session))

    row = RoadmapSuggestion(
        author_user_id=user.id,
        body=body.body.strip(),
        status=RoadmapSuggestionStatus.pending_review,
    )
    session.add(row)
    session.flush()

    agent = PlanningAgent(redis_client=redis_client)
    execution = agent.evaluate(
        session, suggestion=row, approved_roadmap=approved_md
    )

    _audit(
        session,
        user.id,
        "roadmap_suggestion.created",
        {
            "id": str(row.id),
            "execution_id": str(execution.id),
            "execution_status": execution.status.value,
        },
    )
    session.commit()
    session.refresh(row)
    return row


# ── read ─────────────────────────────────────────────────────────────────


@router.get(
    "/roadmap-suggestions",
    response_model=list[RoadmapSuggestionRead],
)
def list_suggestions(
    session: DbSession,
    user: CurrentUser,
    status_filter: RoadmapSuggestionStatus | None = Query(
        default=None, alias="status"
    ),
) -> list[RoadmapSuggestion]:
    q = select(RoadmapSuggestion).order_by(RoadmapSuggestion.created_at.desc())
    if status_filter is not None:
        q = q.where(RoadmapSuggestion.status == status_filter)
    return list(session.execute(q).scalars())


# ``/export`` must be declared BEFORE ``/{suggestion_id}`` so FastAPI doesn't
# try to parse the literal string "export" as a UUID.
@router.get(
    "/roadmap-suggestions/export",
    response_class=PlainTextResponse,
)
def export_roadmap(session: DbSession, user: CurrentUser) -> PlainTextResponse:
    rendered = render_approved_roadmap(_load_all(session))
    body = rendered or (
        "# Red Team Dashboard — Approved Roadmap\n\n"
        "(no approved suggestions yet)\n"
    )
    return PlainTextResponse(
        body,
        media_type="text/markdown",
        headers={"Content-Disposition": 'attachment; filename="ROADMAP.md"'},
    )


@router.get(
    "/roadmap-suggestions/{suggestion_id}",
    response_model=RoadmapSuggestionRead,
)
def get_suggestion(
    suggestion_id: uuid.UUID, session: DbSession, user: CurrentUser
) -> RoadmapSuggestion:
    row = session.get(RoadmapSuggestion, suggestion_id)
    if row is None:
        raise HTTPException(status_code=404, detail="suggestion not found")
    return row


# ── decide (admin only) ──────────────────────────────────────────────────


@router.patch(
    "/roadmap-suggestions/{suggestion_id}/decision",
    response_model=RoadmapSuggestionRead,
)
def decide_suggestion(
    suggestion_id: uuid.UUID,
    body: RoadmapSuggestionDecision,
    session: DbSession,
    admin: CurrentAdminUser,
) -> RoadmapSuggestion:
    if body.status not in (
        RoadmapSuggestionStatus.approved,
        RoadmapSuggestionStatus.rejected,
    ):
        raise HTTPException(
            status_code=400,
            detail="decision must be 'approved' or 'rejected'",
        )
    row = session.get(RoadmapSuggestion, suggestion_id)
    if row is None:
        raise HTTPException(status_code=404, detail="suggestion not found")

    row.status = body.status
    row.review_note = body.note
    row.reviewed_by_user_id = admin.id
    row.reviewed_at = datetime.now(tz=UTC)

    _audit(
        session,
        admin.id,
        "roadmap_suggestion.decided",
        {
            "id": str(row.id),
            "decision": body.status.value,
            "note": body.note,
        },
    )
    session.commit()
    session.refresh(row)
    return row


# ── delete ───────────────────────────────────────────────────────────────


@router.delete(
    "/roadmap-suggestions/{suggestion_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_suggestion(
    suggestion_id: uuid.UUID, session: DbSession, user: CurrentUser
) -> Response:
    row = session.get(RoadmapSuggestion, suggestion_id)
    if row is None:
        raise HTTPException(status_code=404, detail="suggestion not found")

    # Admins can delete any row. Non-admins can only delete their own,
    # and only while still pending — once an admin has reviewed it, the
    # row is part of the audit history and the author can't yank it.
    if not user.is_admin:
        if row.author_user_id != user.id:
            raise HTTPException(
                status_code=403,
                detail="not your suggestion — ask an admin to remove it",
            )
        if row.status != RoadmapSuggestionStatus.pending_review:
            raise HTTPException(
                status_code=403,
                detail=(
                    "this suggestion has already been reviewed; ask an admin "
                    "to remove it"
                ),
            )

    session.delete(row)
    _audit(
        session,
        user.id,
        "roadmap_suggestion.deleted",
        {"id": str(suggestion_id)},
    )
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
