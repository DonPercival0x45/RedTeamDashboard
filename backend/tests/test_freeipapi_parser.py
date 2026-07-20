"""Pure-function tests for the freeipapi response parser + tool arg guards.

No HTTP mocking — the parser is a plain shape transform and the impl's
early guards (missing ip / missing api_key / invalid ip) short-circuit
before any network I/O. Wiring into finding_grouping is covered by the
group_key + dedup tests below.
"""
from app.orchestrator.tools.freeipapi import (  # noqa: I001
    freeipapi_impl,
    parse_freeipapi_response,
)
from app.services.finding_grouping import (
    compute_group_key,
    extract_items,
    group_title,
    item_dedup_key,
)


# ── impl early-return guards ────────────────────────────────────────────────


def test_freeipapi_impl_rejects_missing_ip() -> None:
    result = freeipapi_impl({})
    assert result.ok is False
    assert "missing" in (result.error or "").lower()


def test_freeipapi_impl_rejects_invalid_ip() -> None:
    result = freeipapi_impl({"ip": "not-an-ip"})
    assert result.ok is False
    assert "invalid" in (result.error or "").lower()


def test_freeipapi_impl_rejects_missing_api_key() -> None:
    """The dispatch node injects api_key from tool_secrets; if the analyst
    hasn't uploaded a freeipapi key the impl must short-circuit with a
    pointer to /settings/keys instead of calling the third-party unauthed."""
    result = freeipapi_impl({"ip": "205.159.120.60"})
    assert result.ok is False
    assert "api_key" in (result.error or "").lower()
    assert "settings" in (result.error or "").lower()


# ── parser shape ────────────────────────────────────────────────────────────


def test_parser_extracts_flat_response() -> None:
    body = {
        "ipAddress": "205.159.120.60",
        "latitude": 34.0512,
        "longitude": -84.0710,
        "countryName": "United States",
        "countryCode": "US",
        "regionName": "Georgia",
        "cityName": "Suwanee",
        "zipCode": "30024",
        "continent": "NA",
        "timeZone": "America/New_York",
        "isProxy": False,
        "isMobile": False,
    }
    parsed = parse_freeipapi_response("205.159.120.60", body)
    assert parsed["ip"] == "205.159.120.60"
    assert parsed["source_tool"] == "freeipapi"
    assert parsed["country_name"] == "United States"
    assert parsed["city_name"] == "Suwanee"
    assert parsed["latitude"] == 34.0512
    assert parsed["longitude"] == -84.0710
    assert parsed["time_zone"] == "America/New_York"


def test_parser_handles_nested_location() -> None:
    """Some freeipapi plans nest lat/lon under a `location` object."""
    body = {
        "ipAddress": "1.2.3.4",
        "countryName": "France",
        "location": {"latitude": 48.8566, "longitude": 2.3522},
    }
    parsed = parse_freeipapi_response("1.2.3.4", body)
    assert parsed["latitude"] == 48.8566
    assert parsed["longitude"] == 2.3522


def test_parser_handles_object_timezone() -> None:
    """Some plans return timeZone as an object with a name field."""
    body = {"timeZone": {"name": "Europe/Paris"}}
    parsed = parse_freeipapi_response("1.2.3.4", body)
    assert parsed["time_zone"] == "Europe/Paris"


def test_parser_returns_none_for_missing_fields() -> None:
    """Sparse response — parser fills unknowns with None, not KeyError."""
    parsed = parse_freeipapi_response("1.2.3.4", {})
    assert parsed["ip"] == "1.2.3.4"
    assert parsed["country_name"] is None
    assert parsed["city_name"] is None
    assert parsed["latitude"] is None
    assert parsed["longitude"] is None


def test_parser_coerces_stringified_coordinates() -> None:
    """freeipapi sometimes returns lat/lon as strings; parser coerces to float."""
    body = {"latitude": "34.0512", "longitude": "-84.0710"}
    parsed = parse_freeipapi_response("1.2.3.4", body)
    assert parsed["latitude"] == 34.0512
    assert parsed["longitude"] == -84.0710


def test_parser_drops_uncoerceable_coordinates() -> None:
    """Malformed lat/lon → None rather than crashing."""
    body = {"latitude": "not-a-number", "longitude": None}
    parsed = parse_freeipapi_response("1.2.3.4", body)
    assert parsed["latitude"] is None
    assert parsed["longitude"] is None


# ── finding_grouping integration ────────────────────────────────────────────


def test_group_key_is_stable_per_ip() -> None:
    """Re-running freeipapi on the same IP hits the same group_key so the
    merge branch enriches instead of duplicating."""
    key1 = compute_group_key("freeipapi", {"ip": "1.2.3.4"}, {"ip": "1.2.3.4"})
    key2 = compute_group_key(
        "freeipapi",
        {"ip": "1.2.3.4"},
        {"ip": "1.2.3.4", "cityName": "Paris"},
    )
    assert key1 == "ip_enrichment:1.2.3.4"
    assert key1 == key2


def test_group_key_differs_per_ip() -> None:
    key1 = compute_group_key("freeipapi", {"ip": "1.2.3.4"}, {"ip": "1.2.3.4"})
    key2 = compute_group_key("freeipapi", {"ip": "5.6.7.8"}, {"ip": "5.6.7.8"})
    assert key1 != key2


def test_group_key_none_when_ip_missing() -> None:
    key = compute_group_key("freeipapi", {}, {})
    assert key is None


def test_item_dedup_key_folds_reruns_on_same_ip() -> None:
    """Two freeipapi items for the same IP collapse to one dedup key."""
    key1 = item_dedup_key("freeipapi", {"ip": "1.2.3.4", "city_name": "Paris"})
    key2 = item_dedup_key("freeipapi", {"ip": "1.2.3.4", "city_name": "Lyon"})
    assert key1 == key2 == "1.2.3.4"


def test_extract_items_returns_one_item_per_freeipapi_call() -> None:
    data = {
        "ip": "1.2.3.4",
        "country_name": "France",
        "city_name": "Paris",
        "latitude": 48.8566,
    }
    items = extract_items("freeipapi", data)
    assert len(items) == 1
    assert items[0]["ip"] == "1.2.3.4"
    assert items[0]["country_name"] == "France"


def test_group_title_uses_the_ip_from_the_group_key() -> None:
    title = group_title("freeipapi", "ip_enrichment:1.2.3.4", {})
    assert title == "IP enrichment — 1.2.3.4"
