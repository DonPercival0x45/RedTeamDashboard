"""Reproducer for the DNS-to-IP grouping bug (v2.19.0).

Before the fix, a dedup hit in ``upsert_grouped_finding`` dropped the entire
incoming dict — so a ``dns_lookup`` A record on a subdomain that
``subfinder`` had already discovered vanished, and the IP never reached
``services/entities._walk``. These tests pin the ``_merge_enrichment_into``
helper's contract in isolation (no DB — the wiring into the upsert loop is
covered by shape review).
"""
from __future__ import annotations

from app.services.finding_grouping import (
    _find_item_by_key,
    _merge_enrichment_into,
    item_dedup_key,
)


def test_merge_enrichment_unions_dns_a_records() -> None:
    """subfinder-first entry gets dns_lookup's A record merged in."""
    existing = {
        "subdomain": "tpa.5qpartners.com",
        "source_tool": "subfinder",
    }
    incoming = {
        "subdomain": "tpa.5qpartners.com",
        "source_tool": "dns_lookup",
        "a": ["205.159.120.60"],
        "aaaa": [],
        "cname": [],
    }
    changed = _merge_enrichment_into(existing, incoming)
    assert changed is True
    assert existing["a"] == ["205.159.120.60"]


def test_merge_enrichment_is_idempotent_when_all_fields_present() -> None:
    """Re-merging the same enrichment shouldn't report a change."""
    existing = {
        "subdomain": "tpa.5qpartners.com",
        "source_tool": "subfinder",
        "a": ["205.159.120.60"],
    }
    incoming = {"subdomain": "tpa.5qpartners.com", "a": ["205.159.120.60"]}
    changed = _merge_enrichment_into(existing, incoming)
    assert changed is False
    assert existing["a"] == ["205.159.120.60"]


def test_merge_enrichment_unions_multiple_a_records_dedup() -> None:
    """New A records union in, dupes are dropped, order preserved."""
    existing = {"subdomain": "x.example.com", "a": ["1.1.1.1"]}
    incoming = {"subdomain": "x.example.com", "a": ["1.1.1.1", "2.2.2.2"]}
    changed = _merge_enrichment_into(existing, incoming)
    assert changed is True
    assert existing["a"] == ["1.1.1.1", "2.2.2.2"]


def test_merge_enrichment_preserves_scalar_fields_from_existing() -> None:
    """A later tool doesn't overwrite scalars the discovery tool set first."""
    existing = {
        "subdomain": "x.example.com",
        "source_tool": "subfinder",
    }
    incoming = {
        "subdomain": "x.example.com",
        "source_tool": "dns_lookup",
        "a": ["1.1.1.1"],
    }
    _merge_enrichment_into(existing, incoming)
    assert existing["source_tool"] == "subfinder"


def test_merge_enrichment_copies_missing_scalars_from_incoming() -> None:
    """Scalar fields the existing item lacks are copied from the incoming."""
    existing = {"subdomain": "x.example.com"}
    incoming = {"subdomain": "x.example.com", "source_tool": "dns_lookup"}
    changed = _merge_enrichment_into(existing, incoming)
    assert changed is True
    assert existing["source_tool"] == "dns_lookup"


def test_merge_enrichment_ignores_first_seen_at() -> None:
    """``first_seen_at`` is intentionally not copied — the earlier tool's
    timestamp is the correct 'first seen'."""
    existing = {"subdomain": "x.example.com", "first_seen_at": "2026-01-01"}
    incoming = {"subdomain": "x.example.com", "first_seen_at": "2026-07-20"}
    _merge_enrichment_into(existing, incoming)
    assert existing["first_seen_at"] == "2026-01-01"


def test_find_item_by_key_matches_subdomain_regardless_of_source_tool() -> None:
    """item_dedup_key short-circuits on the ``subdomain`` field, so a
    subfinder entry and a dns_lookup entry for the same subdomain look up
    to the same item — which is exactly what enables the enrichment merge."""
    items: list[dict] = [
        {"subdomain": "tpa.5qpartners.com", "source_tool": "subfinder"},
        {"subdomain": "other.5qpartners.com", "source_tool": "subfinder"},
    ]
    key = item_dedup_key("dns_lookup", {"subdomain": "tpa.5qpartners.com"})
    match = _find_item_by_key(items, "dns_lookup", key)
    assert match is items[0]


def test_find_item_by_key_returns_none_when_missing() -> None:
    items: list[dict] = [{"subdomain": "other.example.com"}]
    key = item_dedup_key("dns_lookup", {"subdomain": "x.example.com"})
    match = _find_item_by_key(items, "dns_lookup", key)
    assert match is None
