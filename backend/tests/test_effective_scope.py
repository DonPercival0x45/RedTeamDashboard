from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import pytest

from app.models import ScopeKind
from app.services.effective_scope import (
    EffectiveScopeState,
    exact_scope_rule_ids_for_entity,
    project_entity,
    project_scope_item,
    project_target,
)


@dataclass(frozen=True)
class Rule:
    kind: ScopeKind
    value: str
    is_exclusion: bool = False
    id: uuid.UUID = field(default_factory=uuid.uuid4)


def test_projects_parent_include_and_exclusion_with_matched_rule_ids() -> None:
    include = Rule(ScopeKind.domain, "example.com")
    exclusion = Rule(ScopeKind.domain, "vendor.example.com", True)
    rules = [include, exclusion]

    included = project_entity("subdomain", "app.example.com", rules)
    assert included.state is EffectiveScopeState.included
    assert included.allowed is True
    assert included.reason_code == "included_parent_domain"
    assert included.matched_include_id == include.id
    assert included.target_kind is ScopeKind.domain

    excluded = project_entity("host", "api.vendor.example.com", rules)
    assert excluded.state is EffectiveScopeState.excluded
    assert excluded.allowed is False
    assert excluded.matched_exclusion_id == exclusion.id
    assert excluded.matched_include_id is None


def test_projects_cross_kind_url_and_cidr_using_canonical_matcher() -> None:
    domain = Rule(ScopeKind.domain, "example.com")
    cidr = Rule(ScopeKind.cidr, "192.0.2.0/24")

    url = project_target("https://app.example.com/login", ScopeKind.url, [domain])
    assert url.state is EffectiveScopeState.included
    assert url.matched_include_id == domain.id
    assert url.target_kind is ScopeKind.url

    ip = project_entity("ip", "192.0.2.44", [cidr])
    assert ip.state is EffectiveScopeState.included
    assert ip.reason_code == "included_cidr"
    assert ip.matched_include_id == cidr.id


def test_email_is_exact_and_unsupported_entities_never_authorize() -> None:
    mailbox = Rule(ScopeKind.email, "Analyst@example.com")
    assert project_entity("email", "Analyst@EXAMPLE.com", [mailbox]).allowed is True
    assert project_entity("email", "analyst@example.com", [mailbox]).allowed is False

    unsupported = project_entity("organization", "Example Corp", [mailbox])
    assert unsupported.state is EffectiveScopeState.unsupported
    assert unsupported.allowed is False
    assert unsupported.reason_code == "unsupported_entity_type"


@pytest.mark.parametrize(
    ("entity_type", "entity_value", "kind", "rule_value"),
    [
        ("domain", "example.com", ScopeKind.domain, "*.EXAMPLE.com."),
        ("domain", "xn--tst-qla.example", ScopeKind.domain, "TÄST.example."),
        ("ip", "2001:db8::1", ScopeKind.ip, "2001:0db8:0:0:0:0:0:1"),
        ("cidr", "192.0.2.0/24", ScopeKind.cidr, "192.0.2.5/24"),
        ("url", "https://example.com/", ScopeKind.url, "https://EXAMPLE.com:443/#note"),
        ("email", "Admin@EXAMPLE.com", ScopeKind.email, "Admin@example.com"),
    ],
)
def test_exact_rule_ids_use_canonical_backend_identity(
    entity_type: str,
    entity_value: str,
    kind: ScopeKind,
    rule_value: str,
) -> None:
    include = Rule(kind, rule_value)
    includes, exclusions = exact_scope_rule_ids_for_entity(
        entity_type,
        entity_value,
        [include],
    )
    assert includes == (include.id,)
    assert exclusions == ()


def test_exact_email_rule_preserves_local_part_case() -> None:
    include = Rule(ScopeKind.email, "Admin@example.com")
    includes, _ = exact_scope_rule_ids_for_entity("email", "admin@example.com", [include])
    assert includes == ()


def test_scope_include_projects_as_shadowed_without_mutating_rule() -> None:
    include = Rule(ScopeKind.domain, "example.com")
    exclusion = Rule(ScopeKind.domain, "example.com", True)
    decision = project_scope_item(include, [include, exclusion])

    assert decision.state is EffectiveScopeState.excluded
    assert decision.allowed is False
    assert decision.matched_exclusion_id == exclusion.id
