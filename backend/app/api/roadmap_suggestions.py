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
    BulkRankApplyRequest,
    BulkRankResponse,
    CombineClusterRead,
    CombineDetectResponse,
    CombineRequest,
    PriorityUpdate,
    RankedRowRead,
    RoadmapSuggestionCreate,
    RoadmapSuggestionDecision,
    RoadmapSuggestionRead,
)
from app.services import roadmap_planner
from app.services import roadmap_suggestions as suggestion_svc
from app.services.ephemeral_provider_key import NoProviderKeyError

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
    # v0.8.0 dedup: don't let two analysts land separate rows for the same
    # idea. Compare on trimmed body against rows that are still live
    # (pending_review or approved). Rejected rows are fair game to re-raise
    # — maybe the analyst is trying again with the same wording after a
    # past rejection.
    trimmed = body.body.strip()
    existing = session.execute(
        select(RoadmapSuggestion).where(
            RoadmapSuggestion.status.in_(
                (
                    RoadmapSuggestionStatus.pending_review,
                    RoadmapSuggestionStatus.approved,
                )
            ),
        )
    ).scalars()
    for prior in existing:
        if (prior.body or "").strip() == trimmed:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": (
                        "A suggestion with this exact body already exists "
                        f"({prior.status.value}). View or edit it instead "
                        "of adding a duplicate."
                    ),
                    "existing_id": str(prior.id),
                    "existing_status": prior.status.value,
                },
            )

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
    priority_min: Annotated[
        int | None, Query(ge=1, le=10, description="Lowest priority to include (1=highest).")
    ] = None,
    priority_max: Annotated[
        int | None, Query(ge=1, le=10, description="Highest priority to include (10=lowest).")
    ] = None,
    include_unranked: Annotated[
        bool, Query(description="Also include rows with no priority set.")
    ] = True,
    show_combined: Annotated[
        bool,
        Query(
            description=(
                "Include rows that were merged into another row "
                "(hidden by default)."
            ),
        ),
    ] = False,
) -> list[RoadmapSuggestion]:
    q = select(RoadmapSuggestion).order_by(RoadmapSuggestion.created_at.desc())
    if status_filter is not None:
        q = q.where(RoadmapSuggestion.status == status_filter)
    if not show_combined:
        q = q.where(RoadmapSuggestion.combined_into_id.is_(None))
    if priority_min is not None or priority_max is not None:
        # v0.16.0: filter by priority range. When include_unranked is
        # true, an OR clause keeps NULL rows visible too — the chip
        # UI shows "Unranked" as a fourth bucket alongside 1-3 / 4-6 / 7-10.
        lo = priority_min or 1
        hi = priority_max or 10
        cond = (RoadmapSuggestion.priority >= lo) & (RoadmapSuggestion.priority <= hi)
        if include_unranked:
            q = q.where(cond | RoadmapSuggestion.priority.is_(None))
        else:
            q = q.where(cond)
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
    from app.models import IntegrationPurpose
    from app.services import integrations as integration_svc
    from app.services.github_push import GitHubPushError, push_roadmap

    # v0.9: route by purpose. The first enabled roadmap_push integration
    # wins (we don't support multi-push here yet — pick one target repo).
    integ = integration_svc.first_by_purpose(
        session, IntegrationPurpose.roadmap_push, enabled_only=True
    )
    if integ is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "GitHub push integration not configured or disabled. "
                "Set it up under /settings/integrations."
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


# ── v0.16.0 prioritization + combine ─────────────────────────────────────
#
# Path-ordering: these literal-path routes MUST declare BEFORE
# ``/{suggestion_id}`` so FastAPI doesn't parse "detect-combines" or
# "rank" as a UUID and 422 the request.


def _open_pool(session: DbSession) -> list[RoadmapSuggestion]:
    return list(
        session.execute(
            select(RoadmapSuggestion)
            .where(
                RoadmapSuggestion.status == RoadmapSuggestionStatus.pending_review,
                RoadmapSuggestion.combined_into_id.is_(None),
            )
            .order_by(RoadmapSuggestion.created_at)
        ).scalars()
    )


@router.post(
    "/roadmap-suggestions/detect-combines",
    response_model=CombineDetectResponse,
)
def detect_combines(
    session: DbSession,
    redis_client: RedisClient,
    user: CurrentNonGuestUser,
) -> CombineDetectResponse:
    """Ask the LLM which pending suggestions describe the same
    underlying problem. Advisory only — analyst confirms each merge
    via POST /roadmap-suggestions/{id}/combine."""
    pool = _open_pool(session)
    try:
        result = roadmap_planner.detect_combine_clusters(
            session, redis_client, pool=pool, acting_user_id=user.id
        )
    except NoProviderKeyError as exc:
        raise HTTPException(
            status_code=400,
            detail=(
                "No provider key configured — set your Anthropic key at "
                "/settings/keys before running an agent operation."
            ),
        ) from exc
    except roadmap_planner.PoolTooLargeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return CombineDetectResponse(
        clusters=[
            CombineClusterRead(**c.to_json()) for c in result.clusters
        ],
        pool_size=len(pool),
        model=result.model,
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        execution_id=result.execution_id,
        error=result.error,
    )


@router.post(
    "/roadmap-suggestions/rank",
    response_model=BulkRankResponse,
)
def rank_suggestions(
    session: DbSession,
    redis_client: RedisClient,
    user: CurrentNonGuestUser,
) -> BulkRankResponse:
    """Ask the LLM to assign a 1..10 priority to every open suggestion.
    Does NOT apply priorities on its own — client shows a confirm
    dialog and POSTs the returned rankings back to
    /roadmap-suggestions/rank/apply."""
    pool = _open_pool(session)
    try:
        result = roadmap_planner.bulk_rank_suggestions(
            session, redis_client, pool=pool, acting_user_id=user.id
        )
    except NoProviderKeyError as exc:
        raise HTTPException(
            status_code=400,
            detail=(
                "No provider key configured — set your Anthropic key at "
                "/settings/keys before running an agent operation."
            ),
        ) from exc
    except roadmap_planner.PoolTooLargeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return BulkRankResponse(
        rankings=[
            RankedRowRead(id=r.id, priority=r.priority, reasoning=r.reasoning)
            for r in result.rankings
        ],
        pool_size=len(pool),
        applied=False,
        model=result.model,
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        execution_id=result.execution_id,
        error=result.error,
    )


@router.post(
    "/roadmap-suggestions/rank/apply",
    response_model=BulkRankResponse,
)
def apply_rank(
    body: BulkRankApplyRequest,
    session: DbSession,
    user: CurrentNonGuestUser,
) -> BulkRankResponse:
    """Apply a LLM-produced ranking after the admin confirms in the
    dialog. Overwrites ``priority`` on every row named in the ranking;
    rows not named keep their existing priority."""
    ids = [r.id for r in body.rankings]
    rows = {
        r.id: r
        for r in session.execute(
            select(RoadmapSuggestion).where(RoadmapSuggestion.id.in_(ids))
        ).scalars()
    }
    applied: list[RankedRowRead] = []
    for r in body.rankings:
        row = rows.get(r.id)
        if row is None:
            continue
        row.priority = r.priority
        applied.append(r)
    _audit(
        session,
        user.id,
        "roadmap.rank_applied",
        {"applied_count": len(applied), "total_requested": len(body.rankings)},
    )
    session.commit()
    return BulkRankResponse(
        rankings=applied,
        pool_size=len(applied),
        applied=True,
        model="",
        tokens_in=0,
        tokens_out=0,
    )


@router.patch(
    "/roadmap-suggestions/{suggestion_id}/priority",
    response_model=RoadmapSuggestionRead,
)
def set_priority(
    suggestion_id: uuid.UUID,
    body: PriorityUpdate,
    session: DbSession,
    user: CurrentNonGuestUser,
) -> RoadmapSuggestion:
    """Set (or clear via null) an analyst-picked priority for one row."""
    row = session.get(RoadmapSuggestion, suggestion_id)
    if row is None:
        raise HTTPException(status_code=404, detail="suggestion not found")
    row.priority = body.priority
    _audit(
        session,
        user.id,
        "roadmap.priority_set",
        {
            "suggestion_id": str(row.id),
            "priority": body.priority,
        },
    )
    session.commit()
    session.refresh(row)
    return row


@router.post(
    "/roadmap-suggestions/{suggestion_id}/combine",
    response_model=RoadmapSuggestionRead,
)
def combine_into(
    suggestion_id: uuid.UUID,
    body: CombineRequest,
    session: DbSession,
    user: CurrentNonGuestUser,
) -> RoadmapSuggestion:
    """Analyst confirms a merge. ``suggestion_id`` in the URL is the
    surviving row; every id in ``body.member_ids`` gets
    ``combined_into_id = suggestion_id`` (hiding it from the default
    list). Rows not deleted — audit preserved."""
    primary = session.get(RoadmapSuggestion, suggestion_id)
    if primary is None:
        raise HTTPException(status_code=404, detail="primary suggestion not found")
    if suggestion_id in body.member_ids:
        raise HTTPException(
            status_code=400,
            detail="primary_id cannot appear in member_ids",
        )
    members = list(
        session.execute(
            select(RoadmapSuggestion).where(
                RoadmapSuggestion.id.in_(body.member_ids)
            )
        ).scalars()
    )
    found_ids = {m.id for m in members}
    missing = [str(mid) for mid in body.member_ids if mid not in found_ids]
    if missing:
        raise HTTPException(
            status_code=404,
            detail=f"member suggestions not found: {missing}",
        )
    for m in members:
        m.combined_into_id = suggestion_id
    _audit(
        session,
        user.id,
        "roadmap.combined",
        {
            "primary_id": str(suggestion_id),
            "member_ids": [str(mid) for mid in body.member_ids],
        },
    )
    session.commit()
    session.refresh(primary)
    return primary


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
