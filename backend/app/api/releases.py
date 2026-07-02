"""v1.3.1: GitHub Releases feed served from the backend.

Before v1.3.1 the What's New surface hit a static ``/releases.json`` file
baked into the frontend Docker image at CI build time — meaning the file
was empty in the repo (`[]`) and the shipped image served an empty
release list. install.sh's categorization enricher only wrote to the
SWA static-export build path; the Container App viewer never picked it
up. See ``project_5qprod_deployment`` memory for the incident context.

This endpoint replaces that flow: the backend fetches from
``api.github.com/repos/<owner>/<repo>/releases`` on demand, runs the
same commit-title categorization logic ``install.sh`` uses, caches the
result in-memory for ``settings.releases_cache_ttl_seconds`` seconds
(default 1h), and returns the enriched payload. Both viewers (SWA and
Container App) can point at this endpoint via the frontend's runtime
API_BASE_URL — the release feed stays fresh without rebuilding images.

Endpoint::

    GET /releases.json -> [{tag_name, name, published_at, body, html_url,
                            categories: {features, fixes, qol, ops}}, …]

No auth: the release feed is also public on GitHub; we're not leaking
anything by exposing it here.
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import Any

import httpx
import structlog
from fastapi import APIRouter

from app.core.config import Settings

logger = structlog.get_logger(__name__)
router = APIRouter()

# ── categorization rules — keep in sync with install.sh ──────────────────
# Commit titles map to a bucket by prefix. If a future contributor
# bypasses the convention, the row lands in ``ops`` by default. The
# ``feedback:`` prefix is dropped entirely because it's the auto-refresh
# of ROADMAP.md — internal noise, not user-facing.

_PR_RE = re.compile(r"\(#(\d+)\)\s*$")
_FEATURE_RE = re.compile(r"^v\d+\.\d+\.\d+(\([^)]*\))?:")
_FIX_RE = re.compile(r"^fix(\([^)]*\))?:")
_QOL_RE = re.compile(r"^(qol|perf|refactor|docs)(\([^)]*\))?:")
_HIDDEN_RE = re.compile(r"^feedback:")


def _bucket(title: str) -> str | None:
    if _HIDDEN_RE.match(title):
        return None
    if _FEATURE_RE.match(title):
        return "features"
    if _FIX_RE.match(title):
        return "fixes"
    if _QOL_RE.match(title):
        return "qol"
    return "ops"


def _empty_categories() -> dict[str, list[Any]]:
    return {"features": [], "fixes": [], "qol": [], "ops": []}


# ── in-memory cache ─────────────────────────────────────────────────────
#
# Module-level because the app runs a single process per replica; a
# Redis-backed cache would be overkill for a payload this small
# (~150KB). If concurrent requests race the cache-miss path, both fetch
# — cheap on GitHub's rate limits since this endpoint is idle for an
# hour at a time.

_cache: dict[str, Any] | None = None
_cache_expires_at: float = 0.0
_cache_lock = asyncio.Lock()


async def _fetch_and_enrich(
    client: httpx.AsyncClient, repo: str
) -> list[dict[str, Any]]:
    """Fetch the 20 most-recent releases and stamp categorization into
    each row. Best-effort per-release — if the compare call fails, the
    row keeps an empty categories block rather than the whole call
    failing."""
    resp = await client.get(
        f"https://api.github.com/repos/{repo}/releases",
        params={"per_page": 20},
        headers={"Accept": "application/vnd.github+json"},
        timeout=15.0,
    )
    resp.raise_for_status()
    releases: list[dict[str, Any]] = resp.json()
    if not releases:
        return []

    for i, rel in enumerate(releases):
        this_tag = rel.get("tag_name")
        if not this_tag:
            continue
        prev_tag = (
            releases[i + 1]["tag_name"] if i + 1 < len(releases) else None
        )
        categories = _empty_categories()
        if prev_tag:
            try:
                cmp_resp = await client.get(
                    f"https://api.github.com/repos/{repo}/compare/{prev_tag}...{this_tag}",
                    headers={"Accept": "application/vnd.github+json"},
                    timeout=15.0,
                )
                cmp_resp.raise_for_status()
                data = cmp_resp.json()
            except Exception as exc:  # noqa: BLE001 — best-effort per row
                logger.warning(
                    "releases.compare_failed",
                    prev=prev_tag,
                    this=this_tag,
                    error=str(exc),
                )
                rel["categories"] = categories
                continue
            for c in data.get("commits") or []:
                raw = (c.get("commit") or {}).get("message") or ""
                title = raw.splitlines()[0].strip()
                if not title:
                    continue
                b = _bucket(title)
                if b is None:
                    continue
                pr_match = _PR_RE.search(title)
                pr = int(pr_match.group(1)) if pr_match else None
                clean = _PR_RE.sub("", title).strip()
                categories[b].append(
                    {
                        "title": clean,
                        "sha": (c.get("sha") or "")[:7],
                        "pr": pr,
                    }
                )
        rel["categories"] = categories
    return releases


@router.get("/releases.json")
async def get_releases() -> list[dict[str, Any]]:
    """Return the categorized What's New feed.

    Cached in-memory for ``settings.releases_cache_ttl_seconds`` (default
    1h). On cache miss + fetch failure, returns an empty list so the
    frontend banner just doesn't render — the What's New surface
    degrades gracefully. Callers get the freshest data every hour
    without paying GitHub API cost per pageview.
    """
    global _cache, _cache_expires_at
    settings = Settings()
    now = time.time()

    if _cache is not None and now < _cache_expires_at:
        return _cache  # type: ignore[return-value]

    async with _cache_lock:
        # Recheck inside the lock — a concurrent caller may have
        # refreshed while we were waiting.
        if _cache is not None and now < _cache_expires_at:
            return _cache  # type: ignore[return-value]

        try:
            async with httpx.AsyncClient() as client:
                releases = await _fetch_and_enrich(
                    client, settings.github_repo
                )
        except Exception as exc:  # noqa: BLE001 — the feed is optional
            logger.warning("releases.fetch_failed", error=str(exc))
            # If a prior successful fetch is in cache, keep serving it
            # rather than blanking. Otherwise return empty and let the
            # frontend render "no releases".
            if _cache is not None:
                return _cache  # type: ignore[return-value]
            return []

        _cache = releases
        _cache_expires_at = now + max(60, settings.releases_cache_ttl_seconds)
        return releases
