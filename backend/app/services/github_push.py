"""Commit ROADMAP.md to a GitHub repo via the Contents API.

Used by the "Push to GitHub" button on /settings/feedback. The integration
row stores ``{pat_token, owner, repo, branch, path}``; this module turns
that plus a rendered markdown body into a single commit on ``branch``.

The flow is two HTTP calls:

1. ``GET  /repos/{owner}/{repo}/contents/{path}?ref={branch}`` to fetch
   the current file SHA (404 means first push — no SHA, file will be
   created).
2. ``PUT  /repos/{owner}/{repo}/contents/{path}`` with
   ``{message, content (base64), sha?, branch}``.

PAT scopes required: ``contents: write`` on the target repo (fine-grained
PAT) or classic ``repo`` scope.
"""
from __future__ import annotations

import base64
from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)

_TIMEOUT_SECONDS = 15.0


class GitHubPushError(Exception):
    """Raised when the GitHub Contents API returns a non-2xx response."""


def _api(owner: str, repo: str, path: str) -> str:
    return f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "rtd-feedback-push",
    }


def push_roadmap(
    *,
    pat_token: str,
    owner: str,
    repo: str,
    path: str,
    branch: str,
    body: str,
    commit_message: str,
) -> dict[str, Any]:
    """Commit ``body`` to ``path`` on ``branch``.

    Returns the GitHub PUT response (``{content: ..., commit: ...}``).
    Raises ``GitHubPushError`` on any non-2xx.
    """
    url = _api(owner, repo, path)
    headers = _headers(pat_token)

    sha: str | None = None
    with httpx.Client(timeout=_TIMEOUT_SECONDS) as client:
        head = client.get(url, params={"ref": branch}, headers=headers)
        if head.status_code == 200:
            sha = head.json().get("sha")
        elif head.status_code != 404:
            raise GitHubPushError(
                f"GET contents failed ({head.status_code}): {head.text[:300]}"
            )

        payload: dict[str, Any] = {
            "message": commit_message,
            "content": base64.b64encode(body.encode("utf-8")).decode("ascii"),
            "branch": branch,
        }
        if sha is not None:
            payload["sha"] = sha

        put = client.put(url, headers=headers, json=payload)
        if put.status_code not in (200, 201):
            raise GitHubPushError(
                f"PUT contents failed ({put.status_code}): {put.text[:300]}"
            )
        logger.info(
            "github_push.committed",
            owner=owner,
            repo=repo,
            branch=branch,
            path=path,
            created=sha is None,
        )
        return put.json()
