"""Status-event Discord notifier (v0.8.0).

Single entry point :func:`notify_status_event` that:
  1. Loads the configured ``discord`` integration row.
  2. Skips silently if the integration is missing, disabled, or has no
     webhook URL (operator may have set up only the bot, not the webhook).
  3. POSTs a Status-event embed via :mod:`app.services.discord`.

Called from agent failure paths (Triage, Strategic via the orchestrator
API) and task retry/dispatch transitions. Never raises — Discord
misconfiguration must not block the analyst's work.

Scope per the v0.8 brief: "failures + run-level completions only".
Skips routine agent completions and task completions; pings on any
failure and on run-level terminals (run = a thread spawned by
``POST /engagements/{slug}/runs``; the worker emits the relevant
terminal event today). v0.8 wires failures explicitly at known call
sites; run-level completions land in a follow-up commit.
"""

from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Integration, IntegrationType
from app.services.discord import post_status_notification

logger = structlog.get_logger(__name__)


def _resolve_webhook(session: Session) -> str | None:
    integ = session.execute(
        select(Integration).where(Integration.type == IntegrationType.discord)
    ).scalar_one_or_none()
    if integ is None or not integ.enabled:
        return None
    cfg: dict[str, Any] = integ.config or {}
    url = cfg.get("webhook_url")
    if not isinstance(url, str) or not url.strip():
        return None
    return url


def notify_status_event(
    session: Session,
    *,
    kind: str,
    title: str,
    status: str,
    detail: str | None = None,
    engagement_slug: str | None = None,
) -> None:
    """Fire a Status-event ping to Discord if a webhook is configured.

    All args are passed through to :func:`post_status_notification`. This
    function never raises; failures are logged at warning. Skip calling
    on routine completions per the v0.8 'failures + run-level
    completions only' policy.
    """
    webhook_url = _resolve_webhook(session)
    if not webhook_url:
        return
    post_status_notification(
        webhook_url=webhook_url,
        kind=kind,
        title=title,
        status=status,
        detail=detail,
        engagement_slug=engagement_slug,
    )
