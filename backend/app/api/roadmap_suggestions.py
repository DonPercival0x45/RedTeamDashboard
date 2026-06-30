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
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, HTTPException, Query, Response, status
from fastapi.responses import PlainTextResponse
from sqlalchemy import select

from app.agents.planner import render_approved_roadmap  # noqa: F401 — re-exported via service
from app.api.deps import (
    CurrentAdminUser,
    CurrentNonGuestUser,
    CurrentUser,
    DbSession,
    RedisClient,
)
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
from app.services import roadmap_suggestions as suggestion_svc

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
    user: CurrentNonGuestUser,
) -> RoadmapSuggestion:
    row, execution = suggestion_svc.create_and_evaluate(
        session,
        redis_client,
        author_user_id=user.id,
        body=body.body,
        source="ui",
    )

    _audit(
        session,
        user.id,
        "roadmap_suggestion.created",
        {
            "id": str(row.id),
            "execution_id": str(execution.id),
            "execution_status": execution.status.value,
            "source": row.source,
        },
    )
    session.commit()
    session.refresh(row)
    # Fire-and-forget Discord notification (skips Discord-sourced rows).
    suggestion_svc.notify_discord_if_configured(session, row)
    return row


# ── read ─────────────────────────────────────────────────────────────────


@router.get(
    "/roadmap-suggestions",
    response_model=list[RoadmapSuggestionRead],
)
def list_suggestions(
    session: DbSession,
    user: CurrentUser,
    status_filter: Annotated[
        RoadmapSuggestionStatus | None, Query(alias="status")
    ] = None,
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


# Same path-ordering caveat applies — declare /push before /{suggestion_id}.
@router.post("/roadmap-suggestions/push", status_code=status.HTTP_200_OK)
def push_roadmap_to_github(
    session: DbSession,
    admin: CurrentAdminUser,
) -> dict[str, Any]:
    """Render the approved roadmap and commit it to GitHub.

    Reads the ``github_push`` integration row for ``{pat_token, owner,
    repo, branch, path}``, asks the Contents API for the file's current
    SHA, then PUTs the rendered markdown body. Returns the resulting
    commit SHA + html_url so the UI can link the analyst straight to the
    new commit.
    """
    from app.models import Integration, IntegrationType
    from app.services.github_push import GitHubPushError, push_roadmap

    integ = session.execute(
        select(Integration).where(Integration.type == IntegrationType.github_push)
    ).scalar_one_or_none()
    if integ is None or not integ.enabled:
        raise HTTPException(
            status_code=400,
            detail=(
                "GitHub push integration not configured or disabled. "
                "Set it up under /settings/feedback."
            ),
        )
    cfg = integ.config or {}
    missing = [
        k for k in ("pat_token", "owner", "repo", "branch", "path") if not cfg.get(k)
    ]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"GitHub integration missing fields: {', '.join(missing)}",
        )

    body = render_approved_roadmap(_load_all(session)) or (
        "# Red Team Dashboard — Approved Roadmap\n\n"
        "(no approved suggestions yet)\n"
    )

    try:
        commit = push_roadmap(
            pat_token=cfg["pat_token"],
            owner=cfg["owner"],
            repo=cfg["repo"],
            path=cfg["path"],
            branch=cfg["branch"],
            body=body,
            commit_message=(
                f"feedback: refresh ROADMAP.md ({len(body.splitlines())} lines)"
            ),
        )
    except GitHubPushError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    commit_obj = commit.get("commit") or {}
    content_obj = commit.get("content") or {}
    commit_sha = commit_obj.get("sha") or content_obj.get("sha")
    html_url = commit_obj.get("html_url") or content_obj.get("html_url")

    _audit(
        session,
        admin.id,
        "roadmap.pushed_to_github",
        {
            "owner": cfg["owner"],
            "repo": cfg["repo"],
            "branch": cfg["branch"],
            "path": cfg["path"],
            "commit_sha": commit_sha,
            "body_bytes": len(body.encode("utf-8")),
        },
    )
    session.commit()
    return {
        "commit_sha": commit_sha,
        "html_url": html_url,
        "owner": cfg["owner"],
        "repo": cfg["repo"],
        "branch": cfg["branch"],
        "path": cfg["path"],
    }


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


# ── re-evaluate (re-run planner agent on an existing row) ────────────────


@router.post(
    "/roadmap-suggestions/{suggestion_id}/re-evaluate",
    response_model=RoadmapSuggestionRead,
)
def re_evaluate_suggestion(
    suggestion_id: uuid.UUID,
    session: DbSession,
    redis_client: RedisClient,
    user: CurrentNonGuestUser,
) -> RoadmapSuggestion:
    """Re-run the planner agent on an existing feedback row.

    Used when the first eval failed (no BYO key cached at the time),
    when the project context has shifted since the row was submitted,
    or when the analyst wants a fresh take. The kicker's BYO key drives
    the call — not the original author's — so a teammate with an active
    key can fix a stale row even if the author is offline.

    The original body is preserved verbatim; only agent_summary /
    agent_pros / agent_cons get replaced.
    """
    row = session.get(RoadmapSuggestion, suggestion_id)
    if row is None:
        raise HTTPException(status_code=404, detail="suggestion not found")

    from app.agents.planner import PlanningAgent, render_approved_roadmap

    approved_md = render_approved_roadmap(_load_all(session))
    agent = PlanningAgent(redis_client=redis_client)
    # Temporarily swap the row's author into the kicker's id so the
    # planner's _resolve_llm picks up the right BYO key. Restore after.
    original_author = row.author_user_id
    row.author_user_id = user.id
    try:
        execution = agent.evaluate(
            session, suggestion=row, approved_roadmap=approved_md
        )
    finally:
        row.author_user_id = original_author

    _audit(
        session,
        user.id,
        "roadmap_suggestion.re_evaluated",
        {
            "id": str(row.id),
            "execution_id": str(execution.id),
            "execution_status": execution.status.value,
        },
    )
    session.commit()
    session.refresh(row)
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
