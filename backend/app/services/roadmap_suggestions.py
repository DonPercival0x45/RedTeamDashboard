"""Shared "create-and-evaluate one feedback row" service.

Both the HTTP endpoint (``POST /roadmap-suggestions``) and the Discord
bot inbound path call this. Centralising it keeps the agent-eval +
Discord-notify hooks in one place and means the bot doesn't have to
duplicate the API's transaction shape.
"""
from __future__ import annotations

import uuid
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.planner import PlanningAgent, render_approved_roadmap
from app.models import (
    AgentExecution,
    IntegrationPurpose,
    RoadmapSuggestion,
    RoadmapSuggestionStatus,
)
from app.services import discord as discord_svc
from app.services import integrations as integration_svc

logger = structlog.get_logger(__name__)


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
