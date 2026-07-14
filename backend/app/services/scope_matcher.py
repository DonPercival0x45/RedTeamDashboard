"""Canonical scope matching shared by execution gates and import workflows."""
from __future__ import annotations

import ipaddress
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit

from app.models import ScopeKind


class ScopeItemLike(Protocol):
    id: uuid.UUID
    kind: ScopeKind
    value: str
    is_exclusion: bool


@dataclass(frozen=True, slots=True)
class ScopeMatch:
    allowed: bool
    reason_code: str
    reason: str
    target: str | None = None
    matched_include_id: uuid.UUID | None = None
    matched_exclusion_id: uuid.UUID | None = None


def normalize_domain(value: str) -> str:
    value = value.strip().lower().rstrip(".")
    if value.startswith("*."):
        value = value[2:]
    try:
        return value.encode("idna").decode("ascii")
    except UnicodeError:
        return value


def domain_matches(target: str, scope_value: str) -> bool:
    target_normalized = normalize_domain(target)
    scope_normalized = normalize_domain(scope_value)
    if not target_normalized or not scope_normalized:
        return False
    return (
        target_normalized == scope_normalized
        or target_normalized.endswith("." + scope_normalized)
    )


def _parse_url(value: str):
    candidate = value.strip()
    if "://" not in candidate:
        candidate = "http://" + candidate
    try:
        return urlsplit(candidate)
    except ValueError:
        return None


def extract_host(value: str) -> str | None:
    parsed = _parse_url(value)
    return parsed.hostname if parsed else None


def normalize_url(value: str) -> str | None:
    parsed = _parse_url(value)
    if parsed is None or not parsed.hostname:
        return None
    scheme = parsed.scheme.lower()
    host = normalize_domain(parsed.hostname)
    try:
        port = parsed.port
    except ValueError:
        return None
    default_port = (scheme == "http" and port == 80) or (
        scheme == "https" and port == 443
    )
    host_for_netloc = f"[{host}]" if ":" in host else host
    netloc = host_for_netloc if port is None or default_port else f"{host_for_netloc}:{port}"
    path = parsed.path or "/"
    return urlunsplit((scheme, netloc, path, parsed.query, ""))


def infer_scope_kind(value: str) -> ScopeKind:
    candidate = value.strip()
    if "://" in candidate:
        return ScopeKind.url
    try:
        ipaddress.ip_network(candidate, strict=False)
        return ScopeKind.cidr if "/" in candidate else ScopeKind.ip
    except ValueError:
        pass
    host = extract_host(candidate)
    if host and ("/" in candidate or ":" in candidate):
        try:
            ipaddress.ip_address(host)
            return ScopeKind.ip
        except ValueError:
            return ScopeKind.domain
    return ScopeKind.domain


def _ip_equals(left: str, right: str) -> bool:
    try:
        return ipaddress.ip_address(left.strip()) == ipaddress.ip_address(right.strip())
    except ValueError:
        return False


def _ip_in_cidr(ip_value: str, cidr_value: str) -> bool:
    try:
        address = ipaddress.ip_address(ip_value.strip())
        network = ipaddress.ip_network(cidr_value.strip(), strict=False)
    except ValueError:
        return False
    return address.version == network.version and address in network


def _cidr_subnet_of(target_cidr: str, scope_cidr: str) -> bool:
    try:
        target = ipaddress.ip_network(target_cidr.strip(), strict=False)
        scope = ipaddress.ip_network(scope_cidr.strip(), strict=False)
    except ValueError:
        return False
    return target.version == scope.version and target.subnet_of(scope)


def item_matches(
    target: str,
    target_kind: ScopeKind,
    item: ScopeItemLike,
) -> bool:
    if target_kind is ScopeKind.domain:
        return item.kind is ScopeKind.domain and domain_matches(target, item.value)

    if target_kind is ScopeKind.url:
        host = extract_host(target)
        if host is None:
            return False
        if item.kind is ScopeKind.url:
            left = normalize_url(target)
            right = normalize_url(item.value)
            return left is not None and left == right
        if item.kind is ScopeKind.domain:
            return domain_matches(host, item.value)
        if item.kind is ScopeKind.ip:
            return _ip_equals(host, item.value)
        if item.kind is ScopeKind.cidr:
            return _ip_in_cidr(host, item.value)
        return False

    if target_kind is ScopeKind.ip:
        if item.kind is ScopeKind.ip:
            return _ip_equals(target, item.value)
        if item.kind is ScopeKind.cidr:
            return _ip_in_cidr(target, item.value)
        return False

    if target_kind is ScopeKind.cidr:
        return item.kind is ScopeKind.cidr and _cidr_subnet_of(target, item.value)

    return False


def evaluate_scope_candidates(
    candidates: Iterable[tuple[str, ScopeKind]],
    scope_items: Iterable[ScopeItemLike],
    *,
    empty_scope_allowed: bool = False,
) -> ScopeMatch:
    """Evaluate one or more identities for the same target.

    Scanner hosts commonly carry an IP, hostname, and URL. An exclusion
    matching any identity wins before includes are considered.
    """
    normalized_candidates = [
        (value.strip(), kind)
        for value, kind in candidates
        if isinstance(value, str) and value.strip()
    ]
    items = list(scope_items)
    if not normalized_candidates:
        return ScopeMatch(
            allowed=False,
            reason_code="invalid_candidate",
            reason="target has no usable scope identity",
        )
    primary = normalized_candidates[0][0]
    if not items:
        return ScopeMatch(
            allowed=empty_scope_allowed,
            reason_code="empty_scope_allowed" if empty_scope_allowed else "empty_scope",
            reason=(
                "engagement scope is empty; import policy allows parsing"
                if empty_scope_allowed
                else "engagement scope is empty"
            ),
            target=primary,
        )

    for item in items:
        if not item.is_exclusion:
            continue
        for target, kind in normalized_candidates:
            if item_matches(target, kind, item):
                return ScopeMatch(
                    allowed=False,
                    reason_code=f"excluded_{item.kind.value}",
                    reason=f"target {target!r} matches exclusion {item.value!r}",
                    target=target,
                    matched_exclusion_id=item.id,
                )

    for item in items:
        if item.is_exclusion:
            continue
        for target, kind in normalized_candidates:
            if item_matches(target, kind, item):
                reason_suffix = (
                    "cidr" if item.kind is ScopeKind.cidr
                    else "parent_domain"
                    if (
                        item.kind is ScopeKind.domain
                        and normalize_domain(target) != normalize_domain(item.value)
                    )
                    else "exact"
                )
                return ScopeMatch(
                    allowed=True,
                    reason_code=f"included_{reason_suffix}",
                    reason=f"target {target!r} matches scope item {item.value!r}",
                    target=target,
                    matched_include_id=item.id,
                )

    return ScopeMatch(
        allowed=False,
        reason_code="no_include_match",
        reason=f"target {primary!r} not in any scope item",
        target=primary,
    )


def evaluate_scope(
    target: str,
    target_kind: ScopeKind,
    scope_items: Iterable[ScopeItemLike],
    *,
    empty_scope_allowed: bool = False,
) -> ScopeMatch:
    return evaluate_scope_candidates(
        [(target, target_kind)],
        scope_items,
        empty_scope_allowed=empty_scope_allowed,
    )
