"""IP geolocation enrichment via freeipapi.com (v2.20.0).

Free tier: 60 req/min without an API key; paid tier: higher limits with
a Bearer token. We always require an analyst-supplied key (BYO in Redis)
so per-analyst attribution + rate isolation is preserved. The dispatch
node injects ``api_key`` from the acting user's ephemeral key store
before this impl runs; missing key → clean refusal.
"""
from __future__ import annotations

import ipaddress
from collections.abc import Mapping
from typing import Any

import httpx

from app.orchestrator.tools.runtime import ToolResult

TIMEOUT_S = 10.0
# v2.24.5: hostname corrected. `api.freeipapi.com` (v2.20 → v2.24.4)
# never resolved — freeipapi.com redirects to `free.freeipapi.com`,
# which is the real host. Every prior freeipapi call in prod failed
# silently with "Name or service not known".
_ENDPOINT_TEMPLATE = "https://free.freeipapi.com/api/json/{ip}"


def freeipapi_impl(args: Mapping[str, Any]) -> ToolResult:
    raw_ip = str(args.get("ip") or "").strip()
    if not raw_ip:
        return ToolResult(ok=False, error="missing or empty 'ip' arg")
    try:
        ipaddress.ip_address(raw_ip)
    except ValueError:
        return ToolResult(ok=False, error=f"invalid ip: {raw_ip!r}")

    api_key = args.get("api_key")
    if not api_key:
        return ToolResult(
            ok=False,
            error=(
                "no freeipapi api_key — upload one at /settings/keys with "
                "provider='freeipapi' so the run can auth to freeipapi.com"
            ),
        )

    headers = {
        "Authorization": f"Bearer {api_key}",
        "User-Agent": "redteam-dashboard/2.20.0 (dossier)",
    }
    try:
        response = httpx.get(
            _ENDPOINT_TEMPLATE.format(ip=raw_ip),
            timeout=TIMEOUT_S,
            headers=headers,
        )
    except httpx.HTTPError as exc:
        return ToolResult(ok=False, error=f"freeipapi request failed: {exc}")

    if response.status_code == 401:
        return ToolResult(
            ok=False,
            error="freeipapi rejected the api_key (401) — check the key",
        )
    if response.status_code == 429:
        return ToolResult(
            ok=False,
            error="freeipapi rate limit exceeded (429) — back off",
        )
    if response.status_code >= 400:
        snippet = response.text[:200] if response.text else ""
        return ToolResult(
            ok=False,
            error=f"freeipapi returned HTTP {response.status_code}: {snippet}",
        )

    try:
        body = response.json()
    except ValueError as exc:
        return ToolResult(ok=False, error=f"freeipapi non-JSON response: {exc}")

    if not isinstance(body, dict):
        return ToolResult(
            ok=False,
            error=f"freeipapi response was not an object: {type(body).__name__}",
        )

    normalized = parse_freeipapi_response(raw_ip, body)
    return ToolResult(ok=True, data=normalized)


def parse_freeipapi_response(ip: str, body: Mapping[str, Any]) -> dict[str, Any]:
    """Extract the fields the Dossier tab + entity extractor consume.

    freeipapi's response evolves; we cherry-pick the fields we care about
    and leave unknowns alone. Missing fields → None (JSON-serializable and
    survives the JSONB round trip).
    """
    latitude = body.get("latitude")
    longitude = body.get("longitude")
    # Some plans nest lat/lon under a `location` object; fall back to that.
    location = body.get("location")
    if latitude is None and isinstance(location, Mapping):
        latitude = location.get("latitude")
    if longitude is None and isinstance(location, Mapping):
        longitude = location.get("longitude")

    return {
        "ip": ip,
        "source_tool": "freeipapi",
        "country_name": body.get("countryName") or body.get("country_name"),
        "country_code": body.get("countryCode") or body.get("country_code"),
        "region_name": body.get("regionName") or body.get("region_name"),
        "city_name": body.get("cityName") or body.get("city_name"),
        "zip_code": body.get("zipCode") or body.get("zip_code"),
        "continent": body.get("continent") or body.get("continentCode"),
        "latitude": _as_float(latitude),
        "longitude": _as_float(longitude),
        "time_zone": _extract_timezone(body),
        "is_proxy": body.get("isProxy") or body.get("is_proxy"),
        "is_mobile": body.get("isMobile") or body.get("is_mobile"),
    }


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_timezone(body: Mapping[str, Any]) -> str | None:
    """Handle both string (``"America/New_York"``) and object
    (``{"timeZone": "..."}``) shapes freeipapi has returned across plans.
    """
    tz = body.get("timeZone") or body.get("time_zone") or body.get("timezone")
    if isinstance(tz, Mapping):
        return str(tz.get("name") or tz.get("timeZone") or "") or None
    if tz is None:
        return None
    return str(tz)
