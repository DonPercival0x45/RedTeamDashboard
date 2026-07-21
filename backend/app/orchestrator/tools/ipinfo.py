"""IP intel enrichment via ipinfo.io (v2.22.0).

Complements freeipapi with ASN / netblock / hosting-vs-residential
signal that freeipapi doesn't return. Free-tier response has geo +
`org` (which encodes ASN as `"AS15169 Google LLC"`); paid tier adds a
richer `asn` object, `company`, and `privacy` (vpn/proxy/tor/hosting)
flags. Parser normalizes both shapes so the Dossier tab renders the
same fields regardless of the caller's plan.
"""
from __future__ import annotations

import ipaddress
from collections.abc import Mapping
from typing import Any

import httpx

from app.orchestrator.tools.runtime import ToolResult

TIMEOUT_S = 10.0
_ENDPOINT_TEMPLATE = "https://ipinfo.io/{ip}/json"


def ipinfo_impl(args: Mapping[str, Any]) -> ToolResult:
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
                "no ipinfo api_key — upload one at /settings/keys with "
                "provider='ipinfo' so the run can auth to ipinfo.io"
            ),
        )

    headers = {
        "Accept": "application/json",
        "User-Agent": "redteam-dashboard/2.22.0 (dossier)",
    }
    try:
        response = httpx.get(
            _ENDPOINT_TEMPLATE.format(ip=raw_ip),
            timeout=TIMEOUT_S,
            headers=headers,
            params={"token": str(api_key)},
        )
    except httpx.HTTPError as exc:
        return ToolResult(ok=False, error=f"ipinfo request failed: {exc}")

    if response.status_code == 401:
        return ToolResult(
            ok=False,
            error="ipinfo rejected the api_key (401) — check the token",
        )
    if response.status_code == 429:
        return ToolResult(
            ok=False,
            error="ipinfo rate limit exceeded (429) — back off",
        )
    if response.status_code >= 400:
        snippet = response.text[:200] if response.text else ""
        return ToolResult(
            ok=False,
            error=f"ipinfo returned HTTP {response.status_code}: {snippet}",
        )

    try:
        body = response.json()
    except ValueError as exc:
        return ToolResult(ok=False, error=f"ipinfo non-JSON response: {exc}")

    if not isinstance(body, dict):
        return ToolResult(
            ok=False,
            error=f"ipinfo response was not an object: {type(body).__name__}",
        )

    normalized = parse_ipinfo_response(raw_ip, body)
    return ToolResult(ok=True, data=normalized)


def parse_ipinfo_response(ip: str, body: Mapping[str, Any]) -> dict[str, Any]:
    """Extract Dossier-relevant fields, normalizing free vs paid shapes.

    - ``loc`` is a ``"lat,lon"`` string on all tiers; we split it.
    - ``org`` is ``"AS15169 Google LLC"`` on free tier; we split ASN off.
    - Paid ``asn`` object (when present) overrides the parsed ``org``.
    - Paid ``privacy`` object flattens into per-flag booleans.
    """
    lat, lon = _split_loc(body.get("loc"))

    asn_number: str | None = None
    asn_name: str | None = None
    asn = body.get("asn")
    if isinstance(asn, Mapping):
        asn_number = _str_or_none(asn.get("asn"))
        asn_name = _str_or_none(asn.get("name"))
    if asn_number is None:
        asn_number, org_name = _split_org(body.get("org"))
        if asn_name is None:
            asn_name = org_name

    org_type: str | None = None
    company = body.get("company")
    if isinstance(company, Mapping):
        org_type = _str_or_none(company.get("type"))
    if org_type is None and isinstance(asn, Mapping):
        org_type = _str_or_none(asn.get("type"))

    privacy = body.get("privacy") if isinstance(body.get("privacy"), Mapping) else {}
    is_hosting = _as_bool(privacy.get("hosting")) if privacy else None
    is_vpn = _as_bool(privacy.get("vpn")) if privacy else None
    is_proxy = _as_bool(privacy.get("proxy")) if privacy else None
    is_tor = _as_bool(privacy.get("tor")) if privacy else None
    is_relay = _as_bool(privacy.get("relay")) if privacy else None

    return {
        "ip": ip,
        "source_tool": "ipinfo",
        "hostname": _str_or_none(body.get("hostname")),
        "country_code": _str_or_none(body.get("country")),
        "region_name": _str_or_none(body.get("region")),
        "city_name": _str_or_none(body.get("city")),
        "zip_code": _str_or_none(body.get("postal")),
        "time_zone": _str_or_none(body.get("timezone")),
        "latitude": lat,
        "longitude": lon,
        "asn": asn_number,
        "asn_name": asn_name,
        "org_type": org_type,
        "is_hosting": is_hosting,
        "is_vpn": is_vpn,
        "is_proxy": is_proxy,
        "is_tor": is_tor,
        "is_relay": is_relay,
    }


def _split_loc(loc: Any) -> tuple[float | None, float | None]:
    if not isinstance(loc, str) or "," not in loc:
        return (None, None)
    lat_s, _, lon_s = loc.partition(",")
    return (_as_float(lat_s.strip()), _as_float(lon_s.strip()))


def _split_org(org: Any) -> tuple[str | None, str | None]:
    """Free-tier ``org`` is ``"AS15169 Google LLC"``; split ASN off the front.

    Returns ``(asn, name)``. If the string doesn't start with ``AS<digits>``
    we treat the whole thing as the name and leave ASN None.
    """
    if not isinstance(org, str) or not org.strip():
        return (None, None)
    head, sep, tail = org.strip().partition(" ")
    if sep and head.startswith("AS") and head[2:].isdigit():
        return (head, tail.strip() or None)
    return (None, org.strip() or None)


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("true", "1", "yes"):
            return True
        if v in ("false", "0", "no"):
            return False
    return None


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None
