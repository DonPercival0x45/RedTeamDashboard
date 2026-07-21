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
# v2.24.6: ipinfo has two API surfaces the analyst-visible tokens
# split across:
#   Standard  https://ipinfo.io/{ip}/json           — city + region + lat/lon
#   Lite      https://api.ipinfo.io/lite/{ip}       — country + ASN only
# A Lite-only token gets 403 "Unknown token" on the Standard endpoint
# and vice versa. Try Standard first (richer data); on 401/403 retry
# against Lite. Response parser normalizes both shapes.
_STANDARD_ENDPOINT = "https://ipinfo.io/{ip}/json"
_LITE_ENDPOINT = "https://api.ipinfo.io/lite/{ip}"


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
        "User-Agent": "redteam-dashboard/2.24.6 (dossier)",
    }
    params = {"token": str(api_key)}

    # Try Standard first — it's the richer response (city + lat/lon).
    # On 401/403 the token is likely a Lite-tier key; fall through to
    # the Lite endpoint before giving up.
    try:
        response = httpx.get(
            _STANDARD_ENDPOINT.format(ip=raw_ip),
            timeout=TIMEOUT_S,
            headers=headers,
            params=params,
        )
    except httpx.HTTPError as exc:
        return ToolResult(ok=False, error=f"ipinfo request failed: {exc}")

    if response.status_code in (401, 403):
        try:
            response = httpx.get(
                _LITE_ENDPOINT.format(ip=raw_ip),
                timeout=TIMEOUT_S,
                headers=headers,
                params=params,
            )
        except httpx.HTTPError as exc:
            return ToolResult(
                ok=False,
                error=f"ipinfo Lite request failed after Standard rejected: {exc}",
            )

    if response.status_code == 401:
        return ToolResult(
            ok=False,
            error="ipinfo rejected the api_key on both Standard and Lite (401) — check the token",
        )
    if response.status_code == 403:
        snippet = response.text[:200] if response.text else ""
        return ToolResult(
            ok=False,
            error=f"ipinfo rejected the api_key on both Standard and Lite (403): {snippet}",
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
    """Extract Dossier-relevant fields, normalizing across ipinfo tiers.

    Three response shapes handled:
    - Standard (free/basic): ``loc`` = "lat,lon" string, ``org`` =
      "AS15169 Google LLC", ``city`` / ``region`` / ``timezone`` present.
    - Paid Business/Premium: adds ``asn`` object and ``privacy`` object.
    - Lite (``api.ipinfo.io/lite/{ip}``): flat ``asn`` string + ``as_name``
      + ``as_domain``; ``country`` is the full name and ``country_code`` is
      the 2-letter code; no city / lat / lon / timezone / privacy fields.
    """
    lat, lon = _split_loc(body.get("loc"))

    # ASN: paid ``asn`` object > Lite flat ``asn`` string > parsed ``org``.
    asn_number: str | None = None
    asn_name: str | None = None
    asn = body.get("asn")
    if isinstance(asn, Mapping):
        asn_number = _str_or_none(asn.get("asn"))
        asn_name = _str_or_none(asn.get("name"))
    elif isinstance(asn, str):
        # Lite tier — ``asn`` is a bare string like "AS15169".
        asn_number = _str_or_none(asn)
        asn_name = _str_or_none(body.get("as_name"))
    if asn_number is None:
        asn_number, org_name = _split_org(body.get("org"))
        if asn_name is None:
            asn_name = org_name

    # org_type: paid ``company.type`` > paid ``asn.type`` > Lite ``as_domain``.
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

    # country field disambiguation:
    # - Standard: ``country`` = 2-letter code (US), no country_name field.
    # - Lite:     ``country`` = full name ("United States"), ``country_code`` = code.
    country_raw = _str_or_none(body.get("country"))
    country_code_raw = _str_or_none(body.get("country_code"))
    if country_code_raw:
        # Lite shape — country is the full name.
        country_name = country_raw
        country_code = country_code_raw
    elif country_raw and len(country_raw) == 2 and country_raw.isupper():
        # Standard shape — country is the 2-letter code.
        country_name = None
        country_code = country_raw
    else:
        country_name = country_raw
        country_code = None

    return {
        "ip": ip,
        "source_tool": "ipinfo",
        "hostname": _str_or_none(body.get("hostname")),
        "country_name": country_name,
        "country_code": country_code,
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
