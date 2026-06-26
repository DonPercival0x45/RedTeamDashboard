"""Scope gate + approval gate behavior.

Pure unit tests — no DB. ScopeItem and Project are SQLAlchemy ORM classes
but they can be instantiated unattached; we just pass concrete values into the
constructor and feed the resulting objects straight to the gate.
"""
from __future__ import annotations

import uuid

import pytest

from app.models import RiskLevel, ScopeKind
from app.orchestrator import (
    Action,
    ToolSpec,
    approval_check,
    evaluate,
    scope_check,
)
from app.orchestrator.scope import ScopeSnapshot
from app.orchestrator.tools import get_tool


def _item(
    kind: ScopeKind,
    value: str,
    *,
    is_exclusion: bool = False,
) -> ScopeSnapshot:
    return ScopeSnapshot(
        id=uuid.uuid4(),
        kind=kind,
        value=value,
        is_exclusion=is_exclusion,
    )


SUBFINDER = ToolSpec(
    name="subfinder",
    risk=RiskLevel.passive,
    target_arg="domain",
    kind=ScopeKind.domain,
)

HTTPX = ToolSpec(
    name="httpx_probe",
    risk=RiskLevel.passive,
    target_arg="url",
    kind=ScopeKind.url,
)

REVERSE_DNS = ToolSpec(
    name="reverse_dns",
    risk=RiskLevel.passive,
    target_arg="ip",
    kind=ScopeKind.ip,
)

PORTSCAN = ToolSpec(
    name="portscan",
    risk=RiskLevel.active,
    target_arg="ip",
    kind=ScopeKind.ip,
)


# ---------------------------------------------------------------------------
# Session authorizations (authorization_id)
# ---------------------------------------------------------------------------

_PORTSCAN_REGISTRY = {"portscan": PORTSCAN}


def test_active_tool_auto_approves_with_authorization() -> None:
    auth_id = uuid.uuid4()
    decision = evaluate(
        "portscan",
        {"ip": "10.0.0.5"},
        [_item(ScopeKind.cidr, "10.0.0.0/24")],
        authorization_id=auth_id,
        registry=_PORTSCAN_REGISTRY,
    )
    assert decision.action is Action.auto
    assert decision.authorization_id == auth_id


def test_active_tool_interrupts_without_authorization() -> None:
    decision = evaluate(
        "portscan",
        {"ip": "10.0.0.5"},
        [_item(ScopeKind.cidr, "10.0.0.0/24")],
        registry=_PORTSCAN_REGISTRY,
    )
    assert decision.action is Action.interrupt


def test_authorization_never_overrides_scope() -> None:
    # A session grant only skips the human prompt; out-of-scope is still denied.
    decision = evaluate(
        "portscan",
        {"ip": "8.8.8.8"},
        [_item(ScopeKind.cidr, "10.0.0.0/24")],
        authorization_id=uuid.uuid4(),
        registry=_PORTSCAN_REGISTRY,
    )
    assert decision.action is Action.deny


# ---------------------------------------------------------------------------
# Domain matching
# ---------------------------------------------------------------------------


def test_domain_exact_match_is_in_scope() -> None:
    scope = [_item(ScopeKind.domain, "acme.com")]
    decision = scope_check(SUBFINDER, {"domain": "acme.com"}, scope)
    assert decision.ok
    assert decision.matched_include_id == scope[0].id


def test_domain_subdomain_is_in_scope_by_default() -> None:
    scope = [_item(ScopeKind.domain, "acme.com")]
    decision = scope_check(SUBFINDER, {"domain": "mail.acme.com"}, scope)
    assert decision.ok


def test_domain_label_boundary_blocks_lookalike() -> None:
    # `evilacme.com` must NOT match `acme.com` — the label-boundary dot guards
    # exactly this case.
    scope = [_item(ScopeKind.domain, "acme.com")]
    decision = scope_check(SUBFINDER, {"domain": "evilacme.com"}, scope)
    assert not decision.ok


def test_domain_different_tld_is_out_of_scope() -> None:
    scope = [_item(ScopeKind.domain, "acme.com")]
    decision = scope_check(SUBFINDER, {"domain": "acme.co"}, scope)
    assert not decision.ok


def test_domain_case_and_trailing_dot_normalized() -> None:
    scope = [_item(ScopeKind.domain, "Acme.COM.")]
    decision = scope_check(SUBFINDER, {"domain": "MAIL.acme.com"}, scope)
    assert decision.ok


# ---------------------------------------------------------------------------
# Exclusions
# ---------------------------------------------------------------------------


def test_exclusion_blocks_otherwise_in_scope_subdomain() -> None:
    include = _item(ScopeKind.domain, "acme.com")
    exclude = _item(ScopeKind.domain, "www.acme.com", is_exclusion=True)
    decision = scope_check(SUBFINDER, {"domain": "www.acme.com"}, [include, exclude])
    assert not decision.ok
    assert decision.matched_exclusion_id == exclude.id


def test_exclusion_does_not_affect_sibling_subdomains() -> None:
    include = _item(ScopeKind.domain, "acme.com")
    exclude = _item(ScopeKind.domain, "www.acme.com", is_exclusion=True)
    decision = scope_check(SUBFINDER, {"domain": "mail.acme.com"}, [include, exclude])
    assert decision.ok


def test_exclusion_evaluated_before_includes() -> None:
    # Order-independent: even if exclusion appears after the matching include
    # in the list, it still wins.
    include = _item(ScopeKind.domain, "acme.com")
    exclude = _item(ScopeKind.domain, "acme.com", is_exclusion=True)
    decision = scope_check(SUBFINDER, {"domain": "acme.com"}, [include, exclude])
    assert not decision.ok


# ---------------------------------------------------------------------------
# URL / host extraction
# ---------------------------------------------------------------------------


def test_url_target_matches_domain_scope_via_host() -> None:
    scope = [_item(ScopeKind.domain, "acme.com")]
    decision = scope_check(
        HTTPX, {"url": "https://mail.acme.com/admin?x=1"}, scope
    )
    assert decision.ok


def test_url_target_matches_exact_url_scope() -> None:
    scope = [_item(ScopeKind.url, "https://acme.com/login")]
    decision = scope_check(HTTPX, {"url": "https://acme.com/login"}, scope)
    assert decision.ok


def test_url_target_out_of_scope_when_host_unscoped() -> None:
    scope = [_item(ScopeKind.domain, "acme.com")]
    decision = scope_check(HTTPX, {"url": "https://evil.com/foo"}, scope)
    assert not decision.ok


# ---------------------------------------------------------------------------
# IP / CIDR
# ---------------------------------------------------------------------------


def test_ip_exact_match() -> None:
    scope = [_item(ScopeKind.ip, "10.0.0.5")]
    decision = scope_check(REVERSE_DNS, {"ip": "10.0.0.5"}, scope)
    assert decision.ok


def test_ip_contained_in_cidr_scope() -> None:
    scope = [_item(ScopeKind.cidr, "10.0.0.0/24")]
    decision = scope_check(REVERSE_DNS, {"ip": "10.0.0.42"}, scope)
    assert decision.ok


def test_ip_outside_cidr_is_out_of_scope() -> None:
    scope = [_item(ScopeKind.cidr, "10.0.0.0/24")]
    decision = scope_check(REVERSE_DNS, {"ip": "10.0.1.1"}, scope)
    assert not decision.ok


def test_domain_target_does_not_match_ip_scope() -> None:
    # Kind compatibility: a domain target only matches domain-kind scope items.
    scope = [_item(ScopeKind.ip, "10.0.0.5")]
    decision = scope_check(SUBFINDER, {"domain": "acme.com"}, scope)
    assert not decision.ok


# ---------------------------------------------------------------------------
# Argument hygiene
# ---------------------------------------------------------------------------


def test_missing_target_arg_denies() -> None:
    scope = [_item(ScopeKind.domain, "acme.com")]
    decision = scope_check(SUBFINDER, {}, scope)
    assert not decision.ok
    assert "missing" in decision.reason.lower()


def test_empty_target_arg_denies() -> None:
    scope = [_item(ScopeKind.domain, "acme.com")]
    decision = scope_check(SUBFINDER, {"domain": "   "}, scope)
    assert not decision.ok


def test_non_string_target_arg_denies() -> None:
    scope = [_item(ScopeKind.domain, "acme.com")]
    decision = scope_check(SUBFINDER, {"domain": ["acme.com"]}, scope)
    assert not decision.ok


# ---------------------------------------------------------------------------
# Approval gate
# ---------------------------------------------------------------------------


def test_passive_in_scope_auto_approves() -> None:
    scope = [_item(ScopeKind.domain, "acme.com")]
    decision = evaluate("subfinder", {"domain": "acme.com"}, scope)
    assert decision.action is Action.auto
    assert decision.auto
    assert decision.risk is RiskLevel.passive


def test_out_of_scope_denies_regardless_of_risk() -> None:
    scope = [_item(ScopeKind.domain, "acme.com")]
    decision = approval_check(
        PORTSCAN,
        scope_check(PORTSCAN, {"ip": "8.8.8.8"}, scope),
    )
    assert decision.action is Action.deny
    assert decision.denied


def test_active_in_scope_requires_interrupt() -> None:
    scope = [_item(ScopeKind.cidr, "10.0.0.0/24")]
    decision = approval_check(
        PORTSCAN,
        scope_check(PORTSCAN, {"ip": "10.0.0.5"}, scope),
    )
    assert decision.action is Action.interrupt
    assert decision.requires_interrupt


def test_authorization_id_threaded_through_decision() -> None:
    scope = [_item(ScopeKind.cidr, "10.0.0.0/24")]
    auth_id = uuid.uuid4()
    decision = approval_check(
        PORTSCAN,
        scope_check(PORTSCAN, {"ip": "10.0.0.5"}, scope),
        authorization_id=auth_id,
    )
    # Phase 1: honored — a covering session authorization auto-approves the
    # otherwise-interrupting active call, carrying the authorization id through.
    assert decision.action is Action.auto
    assert decision.authorization_id == auth_id


# ---------------------------------------------------------------------------
# Registry + unknown-tool behavior
# ---------------------------------------------------------------------------


def test_unknown_tool_denies_via_evaluate() -> None:
    decision = evaluate("nmap_aggressive", {"ip": "10.0.0.5"}, [])
    assert decision.action is Action.deny
    assert "unknown tool" in decision.reason.lower()
    assert decision.risk is None


def test_registry_seeded_with_phase_0_tools() -> None:
    # Smoke check — if these disappear, downstream graph code would silently
    # start denying calls instead of dispatching, so it's worth pinning.
    for name in ("subfinder", "crt_sh", "dns_lookup", "httpx_probe", "reverse_dns"):
        spec = get_tool(name)
        assert spec is not None, name
        assert spec.risk is RiskLevel.passive


def test_evaluate_accepts_registry_override() -> None:
    fake = ToolSpec(
        name="fake_tool",
        risk=RiskLevel.passive,
        target_arg="domain",
        kind=ScopeKind.domain,
    )
    registry = {fake.name: fake}
    scope = [_item(ScopeKind.domain, "acme.com")]
    decision = evaluate(
        "fake_tool", {"domain": "acme.com"}, scope, registry=registry
    )
    assert decision.action is Action.auto


# ---------------------------------------------------------------------------
# ScopeDecision serialization (used by Approval.scope_check JSONB column)
# ---------------------------------------------------------------------------


def test_scope_decision_to_jsonable_is_serializable() -> None:
    scope = [_item(ScopeKind.domain, "acme.com")]
    decision = scope_check(SUBFINDER, {"domain": "mail.acme.com"}, scope)
    payload = decision.to_jsonable()
    assert payload["ok"] is True
    assert payload["target"] == "mail.acme.com"
    assert payload["matched_include_id"] == str(scope[0].id)
    assert payload["matched_exclusion_id"] is None


@pytest.mark.parametrize(
    ("scope_value", "target", "expected"),
    [
        ("acme.com", "acme.com", True),
        ("acme.com", "a.b.c.acme.com", True),
        ("acme.com", "ACME.com", True),
        ("acme.com", "acme.com.", True),
        ("acme.com", "acme.co", False),
        ("acme.com", "notacme.com", False),
        ("acme.com", "", False),
    ],
)
def test_domain_matrix(scope_value: str, target: str, expected: bool) -> None:
    scope = [_item(ScopeKind.domain, scope_value)]
    decision = scope_check(SUBFINDER, {"domain": target}, scope)
    assert decision.ok is expected
