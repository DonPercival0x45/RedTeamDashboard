from __future__ import annotations

import pytest

from app.services.entity_identity import normalize_entity_type, normalize_entity_value


@pytest.mark.parametrize(
    ("entity_type", "raw", "expected"),
    [
        ("Domain", " ExAmPle.COM. ", "example.com"),
        ("subdomain", "API.Example.COM.", "api.example.com"),
        ("host", "Example.COM.", "example.com"),
        ("ip", "2001:0DB8:0:0:0:0:0:1", "2001:db8::1"),
        ("cidr", "192.0.2.42/24", "192.0.2.0/24"),
        ("url", "HTTPS://Example.COM:443/a?b=1", "https://example.com/a?b=1"),
        ("email", "Alice@Example.COM", "Alice@example.com"),
        ("asn", "as00123", "AS123"),
        ("sha256", "A" * 64, "a" * 64),
        ("username", "CaseSensitiveUser", "CaseSensitiveUser"),
        ("person", "Alice Smith", "Alice Smith"),
    ],
)
def test_conservative_normalization(entity_type: str, raw: str, expected: str) -> None:
    assert normalize_entity_value(entity_type, raw) == expected


def test_preserves_wildcard_and_url_identity_boundaries() -> None:
    assert normalize_entity_value("domain", "*.Example.com") == "*.example.com"
    assert normalize_entity_value("domain", "*.Example.com") != normalize_entity_value(
        "domain", "Example.com"
    )
    assert normalize_entity_value("domain", "faß.de") != normalize_entity_value(
        "domain", "fass.de"
    )
    assert normalize_entity_value("url", "http://example.com/a") != normalize_entity_value(
        "url", "https://example.com/a"
    )
    assert normalize_entity_value("url", "https://example.com/a") != normalize_entity_value(
        "url", "https://example.com/b"
    )


def test_type_normalization_does_not_coalesce_type_boundaries() -> None:
    assert normalize_entity_type(" Domain ") == "domain"
    assert normalize_entity_type("hostname") == "host"
    assert normalize_entity_type("fqdn") == "domain"
    assert normalize_entity_type("domain") != normalize_entity_type("subdomain")
