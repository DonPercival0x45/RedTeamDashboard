"""crt.sh — Certificate Transparency log query.

crt.sh aggregates the public CT logs. For most modern targets it surfaces the
overwhelming majority of subdomains an analyst would find via active
enumeration, so it's the cheapest, quietest first pass.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx

from app.orchestrator.tools.runtime import ToolResult

CRT_SH_URL = "https://crt.sh/"
TIMEOUT_S = 30.0


def crt_sh_impl(args: Mapping[str, Any]) -> ToolResult:
    domain = str(args.get("domain") or "").strip().lstrip("*.").lower()
    if not domain:
        return ToolResult(ok=False, error="missing or empty 'domain' arg")

    try:
        response = httpx.get(
            CRT_SH_URL,
            params={"q": domain, "output": "json"},
            timeout=TIMEOUT_S,
            headers={"User-Agent": "redteam-dashboard/0.0.1 (+phase0)"},
        )
        response.raise_for_status()
        certs = response.json()
    except httpx.HTTPError as exc:
        return ToolResult(ok=False, error=f"crt.sh request failed: {exc}")
    except ValueError as exc:  # JSON decode
        return ToolResult(ok=False, error=f"crt.sh returned non-JSON: {exc}")

    names: set[str] = set()
    for cert in certs if isinstance(certs, list) else []:
        if not isinstance(cert, dict):
            continue
        cn = str(cert.get("common_name") or "").strip().lower()
        if cn and "*" not in cn:
            names.add(cn)
        for line in str(cert.get("name_value") or "").splitlines():
            entry = line.strip().lower()
            if entry and "*" not in entry:
                names.add(entry)

    suffix = "." + domain
    subdomains = sorted(n for n in names if n == domain or n.endswith(suffix))

    return ToolResult(
        ok=True,
        data={
            "domain": domain,
            "certs_examined": len(certs) if isinstance(certs, list) else 0,
            "subdomains": subdomains,
            "count": len(subdomains),
        },
    )
