"""WiGLE.net wifi network lookup (v2.24.0).

Given a lat/lon (typically pulled from a prior freeipapi/ipinfo enrichment
of an in-scope IP), query WiGLE for known wifi networks nearby. First tool
in the built-in catalog that needs TWO credentials — WiGLE uses HTTP Basic
with the analyst's API name as username and API token as password. We store
the pair as a single JSON blob in Redis under ``provider="wigle"`` and
unpack inside the tool so the existing secret injector didn't have to grow
a per-tool shape.

Free tier: 5,000 daily queries + basic result set. Analyst signs up at
https://wigle.net/account and pastes both credentials at
/settings/keys as JSON like ``{"name": "AID...", "token": "..."}``.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import httpx

from app.orchestrator.tools.runtime import ToolResult

TIMEOUT_S = 15.0
_ENDPOINT = "https://api.wigle.net/api/v2/network/search"
_DEFAULT_RADIUS_KM = 0.5  # ~500m — tight enough to be meaningfully "near"
_MAX_RADIUS_KM = 50.0  # cap so a fat-fingered radius doesn't hit rate limits
_MAX_RESULTS = 100  # WiGLE's per-page cap on free tier


def wigle_impl(args: Mapping[str, Any]) -> ToolResult:
    lat = _coerce_float(args.get("lat"))
    lon = _coerce_float(args.get("lon"))
    if lat is None or lon is None:
        return ToolResult(
            ok=False,
            error="wigle needs both 'lat' and 'lon' args (floats) — supply from a prior freeipapi/ipinfo enrichment",
        )
    if not -90.0 <= lat <= 90.0 or not -180.0 <= lon <= 180.0:
        return ToolResult(ok=False, error=f"lat/lon out of range: ({lat}, {lon})")

    radius_km = _coerce_float(args.get("radius_km")) or _DEFAULT_RADIUS_KM
    radius_km = max(0.05, min(radius_km, _MAX_RADIUS_KM))

    creds = _parse_credentials(args.get("api_key"))
    if creds is None:
        return ToolResult(
            ok=False,
            error=(
                "no wigle credentials — upload a JSON blob at /settings/keys "
                "with provider='wigle', value='{\"name\": \"AID...\", \"token\": \"...\"}' "
                "(get both at https://wigle.net/account)"
            ),
        )
    api_name, api_token = creds

    lat_delta = radius_km / 111.0  # 1 deg lat ~= 111km
    # cos(lat) narrows longitude spacing at higher latitudes
    import math
    lon_delta = radius_km / (111.0 * max(0.01, math.cos(math.radians(lat))))

    params = {
        "latrange1": f"{lat - lat_delta:.6f}",
        "latrange2": f"{lat + lat_delta:.6f}",
        "longrange1": f"{lon - lon_delta:.6f}",
        "longrange2": f"{lon + lon_delta:.6f}",
        "resultsPerPage": str(_MAX_RESULTS),
    }
    headers = {
        "Accept": "application/json",
        "User-Agent": "redteam-dashboard/2.24.0 (dossier)",
    }

    try:
        response = httpx.get(
            _ENDPOINT,
            timeout=TIMEOUT_S,
            headers=headers,
            params=params,
            auth=httpx.BasicAuth(api_name, api_token),
        )
    except httpx.HTTPError as exc:
        return ToolResult(ok=False, error=f"wigle request failed: {exc}")

    if response.status_code == 401:
        return ToolResult(
            ok=False,
            error="wigle rejected the credentials (401) — check the API name / token pair",
        )
    if response.status_code == 429:
        return ToolResult(
            ok=False,
            error="wigle rate limit exceeded (429) — back off (5k/day free-tier cap)",
        )
    if response.status_code >= 400:
        snippet = response.text[:200] if response.text else ""
        return ToolResult(
            ok=False,
            error=f"wigle returned HTTP {response.status_code}: {snippet}",
        )

    try:
        body = response.json()
    except ValueError as exc:
        return ToolResult(ok=False, error=f"wigle non-JSON response: {exc}")

    if not isinstance(body, Mapping):
        return ToolResult(
            ok=False,
            error=f"wigle response was not an object: {type(body).__name__}",
        )

    if body.get("success") is False:
        return ToolResult(
            ok=False,
            error=f"wigle success=false: {body.get('message') or body.get('error') or 'no message'}",
        )

    return ToolResult(
        ok=True,
        data=parse_wigle_response(lat, lon, radius_km, body),
    )


def parse_wigle_response(
    lat: float,
    lon: float,
    radius_km: float,
    body: Mapping[str, Any],
) -> dict[str, Any]:
    """Normalize a WiGLE ``/network/search`` response into the shape the
    Dossier tab consumes. One entry per BSSID (``netid``).
    """
    results = body.get("results") or []
    networks: list[dict[str, Any]] = []
    if isinstance(results, list):
        for r in results:
            if not isinstance(r, Mapping):
                continue
            networks.append(
                {
                    "bssid": _str_or_none(r.get("netid")),
                    "ssid": _str_or_none(r.get("ssid")),
                    "encryption": _str_or_none(r.get("encryption")),
                    "channel": _coerce_int(r.get("channel")),
                    "frequency": _coerce_int(r.get("frequency")),
                    "trilat": _coerce_float(r.get("trilat")),
                    "trilong": _coerce_float(r.get("trilong")),
                    "qos": _coerce_int(r.get("qos")),
                    "last_updated": _str_or_none(r.get("lastupdt")),
                    "country": _str_or_none(r.get("country")),
                    "city": _str_or_none(r.get("city")),
                    "postal_code": _str_or_none(r.get("postalcode")),
                }
            )

    return {
        "lat": lat,
        "lon": lon,
        "radius_km": radius_km,
        "network_count": len(networks),
        "total_results": _coerce_int(body.get("totalResults")) or len(networks),
        "networks": networks,
    }


def _parse_credentials(value: Any) -> tuple[str, str] | None:
    """WiGLE needs both API name + token. Analysts paste a single JSON
    blob at /settings/keys; unpack it here so the resolver stays
    single-string-per-provider.
    """
    if not value:
        return None
    if isinstance(value, Mapping):
        blob = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            blob = json.loads(text)
        except json.JSONDecodeError:
            return None
        if not isinstance(blob, Mapping):
            return None
    else:
        return None
    name = str(blob.get("name") or blob.get("api_name") or "").strip()
    token = str(blob.get("token") or blob.get("api_token") or "").strip()
    if not name or not token:
        return None
    return (name, token)


def _coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None
