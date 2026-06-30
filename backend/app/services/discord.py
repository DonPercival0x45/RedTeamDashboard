"""Outbound Discord webhook helper.

Posts a small embed to a configured Discord webhook URL whenever a UI-
sourced feedback row lands. Loop prevention is the caller's job: skip
this for rows where ``source`` already starts with ``discord:``.

This module is HTTP-only (no gateway / bot connection). The inbound
direction (Discord → dashboard) lives in :mod:`app.worker.discord_bot`.
"""
from __future__ import annotations

from typing import Any

import httpx
import structlog

from app.models import RoadmapSuggestion

logger = structlog.get_logger(__name__)


def _embed_for(suggestion: RoadmapSuggestion) -> dict[str, Any]:
    title = (suggestion.agent_summary or "New feedback")[:240]
    body = (suggestion.body or "").strip()
    # Discord caps description at 4096 chars; cap ourselves at 1000 so
    # the embed stays readable.
    if len(body) > 1000:
        body = body[:997] + "…"
    fields: list[dict[str, Any]] = []
    if suggestion.agent_pros:
        fields.append(
            {
                "name": "Pros",
                "value": "\n".join(f"• {p}" for p in suggestion.agent_pros[:5])[
                    :1000
                ],
                "inline": False,
            }
        )
    if suggestion.agent_cons:
        fields.append(
            {
                "name": "Cons",
                "value": "\n".join(f"• {c}" for c in suggestion.agent_cons[:5])[
                    :1000
                ],
                "inline": False,
            }
        )
    return {
        "title": title,
        "description": body,
        "color": 0xE85D04,  # ember accent, matches the dashboard chrome
        "footer": {"text": f"source: {suggestion.source}"},
        "fields": fields,
    }


def post_feedback_notification(
    *,
    webhook_url: str,
    suggestion: RoadmapSuggestion,
    timeout_seconds: float = 5.0,
) -> bool:
    """Fire-and-forget Discord webhook POST. Returns True on 2xx.

    Failure is logged but never raised — feedback creation must not
    fail because the operator's webhook is wrong/expired/rate-limited.
    """
    payload: dict[str, Any] = {
        "username": "RTD Feedback",
        "embeds": [_embed_for(suggestion)],
    }
    try:
        with httpx.Client(timeout=timeout_seconds) as client:
            resp = client.post(webhook_url, json=payload)
        if 200 <= resp.status_code < 300:
            return True
        logger.warning(
            "discord.webhook_non_2xx",
            suggestion_id=str(suggestion.id),
            status=resp.status_code,
            body=resp.text[:300],
        )
        return False
    except Exception as exc:  # noqa: BLE001 — never fail caller
        logger.warning(
            "discord.webhook_failed",
            suggestion_id=str(suggestion.id),
            error=str(exc),
        )
        return False


# ── v0.8.0: Status-event webhook (failures + run completions) ─────────────

_STATUS_COLORS = {
    "failed": 0xE11D48,    # rose-600 — matches the Status tab red
    "completed": 0x7C3AED,  # violet-600 — matches purple
}


def _status_embed(
    *,
    kind: str,
    title: str,
    status: str,
    detail: str | None,
    engagement_slug: str | None,
) -> dict[str, Any]:
    desc = (detail or "").strip()
    if len(desc) > 1000:
        desc = desc[:997] + "…"
    fields: list[dict[str, Any]] = []
    if engagement_slug:
        fields.append(
            {"name": "Engagement", "value": engagement_slug, "inline": True}
        )
    fields.append({"name": "Kind", "value": kind, "inline": True})
    fields.append({"name": "Status", "value": status, "inline": True})
    return {
        "title": title[:240],
        "description": desc,
        "color": _STATUS_COLORS.get(status, 0xE85D04),
        "fields": fields,
    }


def post_status_notification(
    *,
    webhook_url: str,
    kind: str,
    title: str,
    status: str,
    detail: str | None = None,
    engagement_slug: str | None = None,
    timeout_seconds: float = 5.0,
) -> bool:
    """POST a Status-event embed to the configured Discord webhook.

    ``kind`` is "agent" | "task" | "run", ``status`` is "failed" | "completed".
    Like ``post_feedback_notification``, this is fire-and-forget and never
    raises — a misconfigured webhook must not block the analyst's work.
    """
    payload: dict[str, Any] = {
        "username": "RTD Status",
        "embeds": [
            _status_embed(
                kind=kind,
                title=title,
                status=status,
                detail=detail,
                engagement_slug=engagement_slug,
            )
        ],
    }
    try:
        with httpx.Client(timeout=timeout_seconds) as client:
            resp = client.post(webhook_url, json=payload)
        if 200 <= resp.status_code < 300:
            return True
        logger.warning(
            "discord.status_non_2xx",
            kind=kind,
            title=title,
            status=resp.status_code,
            body=resp.text[:300],
        )
        return False
    except Exception as exc:  # noqa: BLE001 — never fail caller
        logger.warning(
            "discord.status_failed",
            kind=kind,
            title=title,
            error=str(exc),
        )
        return False
