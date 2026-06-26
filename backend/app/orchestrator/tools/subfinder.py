"""Subdomain enumeration — passive aggregate via ProjectDiscovery subfinder.

Shells out to the ``subfinder`` Go binary baked into the backend image at
``/usr/local/bin/subfinder`` (Dockerfile pins the release). Subfinder
aggregates 30+ passive sources (crt.sh, hackertarget, alienvault, wayback,
dnsdumpster, …); sources that need API keys are skipped silently when no
keys are configured.

Phase 0 of this module used to delegate to ``crt_sh_impl`` directly, which
duplicated the standalone ``crt_sh`` tool and surfaced crt.sh's frequent
502s as run errors. The real binary handles per-source flakiness
internally — when crt.sh 502s, subfinder just records the failure for
that source and returns the unique hits from every other source.
"""
from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping
from typing import Any

from app.orchestrator.tools.runtime import ToolResult

# Hard ceiling on a single call. Subfinder's slowest sources can hang on
# rate-limited or DNS-stuck queries; bound the wall-clock so a single tool
# call doesn't stall the worker run for tens of minutes.
_TIMEOUT_SECONDS = 90

# Per-second request ceiling subfinder enforces across sources. Defensive
# against accidentally hammering a public API and getting the source's IP
# banned mid-engagement.
_RATE_LIMIT_PER_SEC = 50


def subfinder_impl(args: Mapping[str, Any]) -> ToolResult:
    domain = (args.get("domain") or "").strip()
    if not domain:
        return ToolResult(ok=False, error="domain is required")

    try:
        proc = subprocess.run(
            [
                "subfinder",
                "-d", domain,
                "-silent",
                "-json",
                "-nW",  # exclude wildcard subdomains
                "-rl", str(_RATE_LIMIT_PER_SEC),
            ],
            capture_output=True,
            timeout=_TIMEOUT_SECONDS,
            text=True,
        )
    except FileNotFoundError:
        return ToolResult(
            ok=False,
            error=(
                "subfinder binary not found at /usr/local/bin/subfinder. "
                "The backend image needs subfinder installed — rebuild "
                "with the current Dockerfile."
            ),
        )
    except subprocess.TimeoutExpired:
        return ToolResult(
            ok=False,
            error=(
                f"subfinder timed out after {_TIMEOUT_SECONDS}s on "
                f"{domain!r} — one or more sources hung."
            ),
        )

    # Subfinder exits 0 even when individual sources fail (it just notes the
    # failure and moves on). A non-zero exit with no stdout means the call
    # itself failed (bad domain, missing config dir, etc.) — surface stderr.
    if proc.returncode != 0 and not proc.stdout.strip():
        return ToolResult(
            ok=False,
            error=(
                f"subfinder exited {proc.returncode}: "
                f"{proc.stderr.strip()[:500]}"
            ),
        )

    subdomains: list[str] = []
    seen: set[str] = set()
    sources_seen: set[str] = set()
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record: dict[str, Any] = json.loads(line)
        except ValueError:
            continue
        host = (record.get("host") or "").strip()
        if host and host not in seen:
            seen.add(host)
            subdomains.append(host)
        src = record.get("source")
        if isinstance(src, str) and src:
            sources_seen.add(src)

    return ToolResult(
        ok=True,
        data={
            "domain": domain,
            "source": "subfinder",
            "sources_used": sorted(sources_seen),
            "count": len(subdomains),
            "subdomains": subdomains,
        },
    )
