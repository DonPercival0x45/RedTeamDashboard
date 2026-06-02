"""HTTP/HTTPS probe: status, final URL after redirects, page title, basic
server/tech fingerprints. Passive (one GET, no path bruteforce)."""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

import httpx

from app.orchestrator.tools.runtime import ToolResult

TIMEOUT_S = 10.0
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
MAX_TITLE_LEN = 200
MAX_BODY_BYTES = 1_000_000  # cap large pages so probes stay cheap


def _normalize(raw: str) -> str:
    if not raw.lower().startswith(("http://", "https://")):
        return "https://" + raw
    return raw


def httpx_probe_impl(args: Mapping[str, Any]) -> ToolResult:
    raw = str(args.get("url") or "").strip()
    if not raw:
        return ToolResult(ok=False, error="missing or empty 'url' arg")
    url = _normalize(raw)

    try:
        with httpx.Client(
            timeout=TIMEOUT_S,
            follow_redirects=True,
            headers={"User-Agent": "redteam-dashboard/0.0.1 (+phase0)"},
        ) as client:
            response = client.get(url)
    except httpx.HTTPError as exc:
        return ToolResult(ok=False, error=f"probe failed: {exc}")

    content_type = response.headers.get("content-type", "")
    title: str | None = None
    if "text/html" in content_type.lower():
        body = response.text[:MAX_BODY_BYTES]
        match = TITLE_RE.search(body)
        if match:
            title = " ".join(match.group(1).split())[:MAX_TITLE_LEN]

    return ToolResult(
        ok=True,
        data={
            "url": url,
            "final_url": str(response.url),
            "status": response.status_code,
            "title": title,
            "content_type": content_type or None,
            "server": response.headers.get("server"),
            "x_powered_by": response.headers.get("x-powered-by"),
            "content_length": len(response.content),
        },
    )
