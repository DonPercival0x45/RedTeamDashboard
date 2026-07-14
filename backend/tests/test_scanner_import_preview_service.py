"""Pure service coverage for scanner preview/commit planning."""
from __future__ import annotations

import hashlib

import pytest

from app.models import ScopeItem, ScopeKind
from app.services.scanner_import import (
    DuplicateIndex,
    build_scanner_preview,
    prepare_scanner_commit,
)


def _nessus_xml(*hosts: tuple[str, str, str, str, int]) -> bytes:
    host_xml = []
    for fqdn, ip, plugin_id, plugin_name, severity in hosts:
        host_xml.append(
            f"""<ReportHost name="{fqdn}">
  <HostProperties>
    <tag name="host-fqdn">{fqdn}</tag>
    <tag name="host-ip">{ip}</tag>
  </HostProperties>
  <ReportItem port="443" protocol="tcp" severity="{severity}"
      pluginID="{plugin_id}" pluginName="{plugin_name}" pluginFamily="General">
    <synopsis>Summary</synopsis>
  </ReportItem>
</ReportHost>"""
        )
    return (
        "<NessusClientData_v2><Report name=\"test\">"
        + "".join(host_xml)
        + "</Report></NessusClientData_v2>"
    ).encode()


def test_preview_hash_groups_and_informational_default() -> None:
    raw = _nessus_xml(
        ("app.example.test", "192.0.2.10", "100", "TLS issue", 3),
        ("app.example.test", "192.0.2.10", "200", "Informational", 0),
    )

    preview = build_scanner_preview("nessus", raw, scope_items=[])

    assert preview.file_sha256 == hashlib.sha256(raw).hexdigest()
    assert preview.total_source_rows == 2
    groups = {group.selection_key: group for group in preview.groups}
    assert groups["nessus:100"].default_selected is True
    assert groups["nessus:200"].default_selected is False
    assert groups["nessus:100"].scope_decision == "empty_scope_allowed"


def test_preview_exposes_mixed_scope_and_existing_items() -> None:
    raw = _nessus_xml(
        ("allowed.example.test", "192.0.2.10", "100", "TLS issue", 3),
        ("outside.example.test", "198.51.100.10", "100", "TLS issue", 3),
    )
    scope = ScopeItem(
        kind=ScopeKind.ip,
        value="192.0.2.10",
        is_exclusion=False,
        source="defined",
    )
    duplicates = DuplicateIndex(
        group_targets={"nessus:100": frozenset({"allowed.example.test:443"})}
    )

    preview = build_scanner_preview(
        "nessus",
        raw,
        scope_items=[scope],
        duplicate_index=duplicates,
    )

    group = preview.groups[0]
    assert group.scope_decision == "mixed"
    assert group.in_scope_item_count == 1
    assert group.out_of_scope_item_count == 1
    assert group.duplicate_state == "partial"
    assert group.duplicate_item_count == 1
    assert group.default_selected is False


def test_commit_reparses_and_keeps_only_selected_allowed_new_items() -> None:
    raw = _nessus_xml(
        ("allowed.example.test", "192.0.2.10", "100", "TLS issue", 3),
        ("outside.example.test", "198.51.100.10", "100", "TLS issue", 3),
        ("other.example.test", "192.0.2.11", "200", "Other issue", 2),
    )
    scope = ScopeItem(
        kind=ScopeKind.cidr,
        value="192.0.2.0/24",
        is_exclusion=False,
        source="defined",
    )
    preview = build_scanner_preview("nessus", raw, scope_items=[scope])

    prepared = prepare_scanner_commit(
        "nessus",
        raw,
        expected_sha256=preview.file_sha256,
        selected_group_keys={"nessus:100"},
        scope_items=[scope],
    )

    assert prepared.selected_group_count == 1
    assert prepared.selected_item_count == 1
    assert prepared.skipped_out_of_scope == 1
    assert [item.target for item in prepared.items] == ["allowed.example.test:443"]


@pytest.mark.parametrize(
    ("source", "raw", "expected_key"),
    [
        (
            "burp",
            b"""<issues exportTime="Mon, 30 Jun 2026 14:22:01 GMT">
              <issue><serialNumber>42</serialNumber><type>1001</type>
              <name>Reflected input</name><host ip="192.0.2.20">https://app.example.test</host>
              <path>/search</path><severity>Medium</severity></issue>
            </issues>""",
            "burp:1001",
        ),
        (
            "nmap",
            b"""<nmaprun start="1783987200"><host><status state="up"/>
              <address addr="192.0.2.30" addrtype="ipv4"/>
              <ports><port protocol="tcp" portid="443"><state state="open"/>
              <service name="https"/></port></ports></host></nmaprun>""",
            "nmap:tcp:443:https",
        ),
    ],
)
def test_preview_supports_each_scanner_source(
    source: str,
    raw: bytes,
    expected_key: str,
) -> None:
    preview = build_scanner_preview(source, raw, scope_items=[])  # type: ignore[arg-type]

    assert preview.groups[0].selection_key == expected_key
    assert preview.groups[0].scope_decision == "empty_scope_allowed"


def test_commit_rejects_changed_file_and_unknown_selection() -> None:
    raw = _nessus_xml(
        ("app.example.test", "192.0.2.10", "100", "TLS issue", 3),
    )
    digest = hashlib.sha256(raw).hexdigest()

    with pytest.raises(ValueError, match="does not match"):
        prepare_scanner_commit(
            "nessus",
            raw + b" ",
            expected_sha256=digest,
            selected_group_keys={"nessus:100"},
            scope_items=[],
        )

    with pytest.raises(ValueError, match="unknown scanner preview selection"):
        prepare_scanner_commit(
            "nessus",
            raw,
            expected_sha256=digest,
            selected_group_keys={"nessus:not-present"},
            scope_items=[],
        )
