"""Conservative stored-Entity identity normalization.

Only representations with well-understood equivalence are canonicalized.
Unknown/free-form identities remain case-sensitive. Raw legacy rows are never
rewritten automatically; callers may use :func:`entity_identity_key` to surface
ambiguous existing collisions for analyst grouping.
"""
from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit, urlunsplit

_DOMAIN_TYPES = {"domain", "subdomain", "fqdn"}
_HOST_TYPES = {"host", "hostname"}
_EMAIL_TYPES = {"email", "email_address", "mailbox"}
_IP_TYPES = {"ip", "ip_address", "ipv4", "ipv6"}
_CIDR_TYPES = {"cidr", "network", "netblock"}
_URL_TYPES = {"url", "uri", "website"}
_HASH_LENGTHS = {32, 40, 64, 128}
_HEX_RE = re.compile(r"^[0-9a-fA-F]+$")
_ASN_RE = re.compile(r"^(?:AS)?0*(\d+)$", re.IGNORECASE)
_TYPE_ALIASES = {
    "fqdn": "domain",
    "hostname": "host",
    "email_address": "email",
    "mailbox": "email",
    "ip_address": "ip",
    "ipv4": "ip",
    "ipv6": "ip",
    "network": "cidr",
    "netblock": "cidr",
    "uri": "url",
    "website": "url",
}


def normalize_entity_type(value: object) -> str:
    raw = str(value or "").strip().casefold()
    return _TYPE_ALIASES.get(raw, raw)


def normalize_entity_value(entity_type: object, value: object) -> str:
    """Return a conservative canonical representation for a known type."""
    normalized = str(value or "").strip()
    kind = normalize_entity_type(entity_type).casefold()
    if not normalized:
        return normalized

    if kind in _DOMAIN_TYPES:
        return _normalize_domain(normalized)
    if kind in _HOST_TYPES:
        try:
            return ipaddress.ip_address(normalized).compressed
        except ValueError:
            return _normalize_domain(normalized)
    if kind in _EMAIL_TYPES:
        local, separator, domain = normalized.rpartition("@")
        if not separator or not local or not domain:
            return normalized
        return f"{local}@{_normalize_domain(domain)}"
    if kind in _IP_TYPES:
        try:
            return ipaddress.ip_address(normalized).compressed
        except ValueError:
            return normalized
    if kind in _CIDR_TYPES:
        try:
            return str(ipaddress.ip_network(normalized, strict=False))
        except ValueError:
            return normalized
    if kind in _URL_TYPES:
        return _normalize_url(normalized)
    if kind == "asn":
        match = _ASN_RE.fullmatch(normalized)
        return f"AS{int(match.group(1))}" if match else normalized
    if (
        kind in {"hash", "md5", "sha1", "sha256", "sha512"}
        and len(normalized) in _HASH_LENGTHS
        and _HEX_RE.fullmatch(normalized)
    ):
        return normalized.casefold()
    return normalized


def entity_identity_key(entity_type: object, value: object) -> tuple[str, str]:
    """Return the advisory duplicate key while keeping ontology types distinct."""
    normalized_type = normalize_entity_type(entity_type)
    return normalized_type, normalize_entity_value(normalized_type, value)


def _normalize_domain(value: str) -> str:
    wildcard = value.startswith("*.")
    raw = value[2:] if wildcard else value
    # DNS labels are ASCII case-insensitive. Preserve non-ASCII code points
    # rather than applying IDNA2003/casefold rules that can coalesce distinct
    # internationalized names (for example, sharp-s versus "ss").
    canonical = raw.rstrip(".").lower()
    return f"*.{canonical}" if wildcard else canonical


def _normalize_url(value: str) -> str:
    try:
        parts = urlsplit(value)
    except ValueError:
        return value
    scheme = parts.scheme.casefold()
    if scheme not in {"http", "https"} or not parts.netloc or parts.username is not None:
        return value
    hostname = parts.hostname
    if hostname is None:
        return value
    try:
        host = ipaddress.ip_address(hostname).compressed
        if ":" in host:
            host = f"[{host}]"
    except ValueError:
        host = _normalize_domain(hostname)
    try:
        port = parts.port
    except ValueError:
        return value
    default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    port_suffix = "" if port is None or default_port else f":{port}"
    path = parts.path or "/"
    return urlunsplit((scheme, f"{host}{port_suffix}", path, parts.query, ""))
