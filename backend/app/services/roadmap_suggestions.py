"""Shared "create-and-evaluate one feedback row" service.

Both the HTTP endpoint (``POST /roadmap-suggestions``) and the Discord
bot inbound path call this. Centralising it keeps the agent-eval +
Discord-notify hooks in one place and means the bot doesn't have to
duplicate the API's transaction shape.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.planner import PlanningAgent, render_approved_roadmap
from app.core.config import settings
from app.models import (
    AgentExecution,
    IntegrationPurpose,
    RoadmapSuggestion,
    RoadmapSuggestionStatus,
)
from app.services import discord as discord_svc
from app.services import integrations as integration_svc

logger = structlog.get_logger(__name__)


class PlannerRateLimitExceeded(RuntimeError):
    """The analyst exhausted the platform-funded daily evaluation budget."""


def _is_real_key(value: str) -> bool:
    return bool(value and not value.startswith("PLACEHOLDER-"))


def _platform_planner_enabled() -> bool:
    if _is_real_key(settings.planner_api_key):
        return True
    if settings.planner_provider == "openai":
        return _is_real_key(settings.openai_api_key)
    if settings.planner_provider == "anthropic":
        return _is_real_key(settings.anthropic_api_key)
    if settings.planner_provider == "azure":
        return _is_real_key(settings.azure_openai_api_key)
    return False


def enforce_planner_rate_limit(redis_client: Any, *, user_id: uuid.UUID) -> None:
    """Cap platform-funded Planner calls per analyst per UTC day.

    BYO fallback is intentionally not rate limited: no platform credential
    means the submitter still owns that spend. Redis failure is fail-open so
    product feedback remains usable during a cache interruption.
    """
    limit = settings.planner_daily_limit_per_user
    if limit <= 0 or not _platform_planner_enabled():
        return
    day = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    key = f"planner_rate:{user_id}:{day}"
    try:
        count = int(redis_client.incr(key))
        if count == 1:
            redis_client.expire(key, 90_000)
    except Exception as exc:  # noqa: BLE001 - availability beats limiter failure
        logger.warning("planner.rate_limit_unavailable", error=str(exc))
        return
    if count > limit:
        raise PlannerRateLimitExceeded(
            f"Platform Planner daily limit reached ({limit} evaluations). "
            "Try again after 00:00 UTC."
        )


def _all_suggestions(session: Session) -> list[RoadmapSuggestion]:
    return list(
        session.execute(
            select(RoadmapSuggestion).order_by(RoadmapSuggestion.created_at)
        ).scalars()
    )


def create_and_evaluate(
    session: Session,
    redis_client: Any,
    *,
    author_user_id: uuid.UUID,
    body: str,
    source: str = "ui",
) -> tuple[RoadmapSuggestion, AgentExecution]:
    """Persist a new ``RoadmapSuggestion`` and run the planner agent over
    it. Caller commits the session.

    Returns the row + the agent execution row (the execution row may have
    ``status=failed`` if the author has no BYO key cached — the row still
    persists with empty pros/cons in that case).
    """
    approved_md = render_approved_roadmap(_all_suggestions(session))

    row = RoadmapSuggestion(
        author_user_id=author_user_id,
        body=body.strip(),
        status=RoadmapSuggestionStatus.pending_review,
        source=source,
    )
    session.add(row)
    session.flush()

    agent = PlanningAgent(redis_client=redis_client)
    execution = agent.evaluate(
        session, suggestion=row, approved_roadmap=approved_md
    )
    return row, execution


def notify_discord_if_configured(
    session: Session, suggestion: RoadmapSuggestion
) -> None:
    """Best-effort outbound: for every enabled integration wired to
    ``purpose='feedback'`` with a ``webhook_url``, POST the suggestion.
    Skip Discord-originated rows (loop prevention).

    v0.9.0: routes by purpose instead of type. The pre-v0.9 single
    Discord row continues to work because migration 0028 backfilled
    it with ``purpose='feedback'``. A multi-Discord deployment with
    a separate alerts channel keeps the routing clean — only
    feedback-purposed rows fire here.
    """
    if suggestion.source.startswith("discord:"):
        return
    for integ in integration_svc.list_by_purpose(
        session, IntegrationPurpose.feedback, enabled_only=True
    ):
        webhook_url = (integ.config or {}).get("webhook_url")
        if not webhook_url:
            continue
        discord_svc.post_feedback_notification(
            webhook_url=webhook_url, suggestion=suggestion
        )
