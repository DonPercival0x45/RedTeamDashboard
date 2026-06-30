"""Status-event Discord notifier (v0.8.0 → routing rebuild in v0.9.0).

Single entry point :func:`notify_status_event` that:
  1. Loads ALL integrations with ``purpose='status_alerts'``.
  2. For each enabled row with a webhook_url, POSTs an embed.
  3. Skips silently if no row is configured (operator hasn't wired
     status_alerts to anything yet — feedback push still works on its
     own integration row).

v0.9.0: routes by purpose, not by type. A multi-Discord deployment
where feedback goes to one channel and status alerts to another
"just works" because the two rows carry different purposes.

Called from agent failure paths (Triage, Strategic via the orchestrator
API) and task retry/dispatch transitions. Never raises — Discord
misconfiguration must not block the analyst's work.
"""

from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy.orm import Session

from app.models import IntegrationPurpose
from app.services import integrations as integration_svc
from app.services.discord import post_status_notification

logger = structlog.get_logger(__name__)


def _resolve_webhook_urls(session: Session) -> list[str]:
    """All enabled status_alerts integrations that have a webhook_url."""
    urls: list[str] = []
    for integ in integration_svc.list_by_purpose(
        session, IntegrationPurpose.status_alerts, enabled_only=True
    ):
        cfg: dict[str, Any] = integ.config or {}
        url = cfg.get("webhook_url")
        if isinstance(url, str) and url.strip():
            urls.append(url)
    return urls


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
    for webhook_url in _resolve_webhook_urls(session):
        post_status_notification(
            webhook_url=webhook_url,
            kind=kind,
            title=title,
            status=status,
            detail=detail,
            engagement_slug=engagement_slug,
        )
