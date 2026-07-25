"""Canonical scope matcher behavior shared across gates and imports."""
from __future__ import annotations

import uuid

import pytest

from app.models import ScopeKind
from app.orchestrator.scope import ScopeSnapshot
from app.services.scope_matcher import (
    evaluate_scope,
    evaluate_scope_candidates,
    infer_scope_kind,
    normalize_email,
    normalize_url,
)


def item(kind: ScopeKind, value: str, *, excluded: bool = False) -> ScopeSnapshot:
    return ScopeSnapshot(
        id=uuid.uuid4(),
        kind=kind,
        value=value,
        is_exclusion=excluded,
    )


@pytest.mark.parametrize(
    ("target", "kind", "scope_kind", "scope_value", "reason"),
    [
        ("10.0.0.5", ScopeKind.ip, ScopeKind.cidr, "10.0.0.0/24", "included_cidr"),
        ("2001:db8::5", ScopeKind.ip, ScopeKind.cidr, "2001:db8::/64", "included_cidr"),
        (
            "api.example.com",
            ScopeKind.domain,
            ScopeKind.domain,
            "example.com",
            "included_parent_domain",
        ),
        (
            "api.example.com",
            ScopeKind.domain,
            ScopeKind.domain,
            "*.example.com",
            "included_parent_domain",
        ),
        (
            "https://api.example.com/x",
            ScopeKind.url,
            ScopeKind.domain,
            "example.com",
            "included_parent_domain",
        ),
        ("https://10.0.0.5/x", ScopeKind.url, ScopeKind.cidr, "10.0.0.0/24", "included_cidr"),
        (
            "Analyst@Example.COM",
            ScopeKind.email,
            ScopeKind.email,
            "Analyst@example.com",
            "included_exact",
        ),
    ],
)
def test_supported_scope_relationships(
    target: str,
    kind: ScopeKind,
    scope_kind: ScopeKind,
    scope_value: str,
    reason: str,
) -> None:
    decision = evaluate_scope(target, kind, [item(scope_kind, scope_value)])
    assert decision.allowed
    assert decision.reason_code == reason


def test_exclusion_on_any_identity_wins() -> None:
    include = item(ScopeKind.domain, "example.com")
    exclude = item(ScopeKind.ip, "10.0.0.5", excluded=True)
    decision = evaluate_scope_candidates(
        [("api.example.com", ScopeKind.domain), ("10.0.0.5", ScopeKind.ip)],
        [include, exclude],
    )
    assert not decision.allowed
    assert decision.matched_exclusion_id == exclude.id


def test_email_scope_is_exact_and_does_not_inherit_from_domain() -> None:
    exact = item(ScopeKind.email, "Analyst@example.com")
    domain = item(ScopeKind.domain, "example.com")
    assert evaluate_scope("Analyst@EXAMPLE.COM", ScopeKind.email, [exact]).allowed
    assert not evaluate_scope("analyst@example.com", ScopeKind.email, [exact]).allowed
    assert not evaluate_scope("Analyst@example.com", ScopeKind.email, [domain]).allowed
    assert normalize_email("Analyst@EXAMPLE.COM.") == "Analyst@example.com"


def test_email_exclusion_wins() -> None:
    include = item(ScopeKind.email, "analyst@example.com")
    exclude = item(ScopeKind.email, "analyst@example.com", excluded=True)
    decision = evaluate_scope(
        "analyst@example.com", ScopeKind.email, [include, exclude]
    )
    assert not decision.allowed
    assert decision.reason_code == "excluded_email"


def test_domain_boundary_blocks_lookalike() -> None:
    decision = evaluate_scope(
        "badexample.com",
        ScopeKind.domain,
        [item(ScopeKind.domain, "example.com")],
    )
    assert not decision.allowed
    assert decision.reason_code == "no_include_match"


def test_url_normalizes_default_port_and_fragment() -> None:
    assert normalize_url("HTTPS://Example.COM:443/path#fragment") == "https://example.com/path"


def test_empty_scope_policy_is_explicit() -> None:
    denied = evaluate_scope("10.0.0.5", ScopeKind.ip, [])
    allowed = evaluate_scope(
        "10.0.0.5", ScopeKind.ip, [], empty_scope_allowed=True
    )
    assert not denied.allowed and denied.reason_code == "empty_scope"
    assert allowed.allowed and allowed.reason_code == "empty_scope_allowed"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://example.com/a", ScopeKind.url),
        ("10.0.0.5", ScopeKind.ip),
        ("10.0.0.0/24", ScopeKind.cidr),
        ("api.example.com", ScopeKind.domain),
        ("api.example.com:8443", ScopeKind.domain),
        ("analyst@example.com", ScopeKind.email),
    ],
)
def test_kind_inference(value: str, expected: ScopeKind) -> None:
    assert infer_scope_kind(value) is expected
