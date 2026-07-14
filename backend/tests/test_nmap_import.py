"""Nmap XML importer parser coverage."""
from __future__ import annotations

from app.models import ScopeItem, ScopeKind
from app.services.nmap_import import parse_nmap_xml

_SAMPLE = b"""<?xml version='1.0'?>
<nmaprun scanner="nmap" start="1783987200">
  <host>
    <status state="up"/>
    <address addr="192.0.2.10" addrtype="ipv4"/>
    <hostnames><hostname name="api.example.test" type="PTR"/></hostnames>
    <ports>
      <port protocol="tcp" portid="443">
        <state state="open"/>
        <service name="https" product="nginx" version="1.26"/>
        <script id="ssl-cert" output="CN=api.example.test"/>
      </port>
      <port protocol="tcp" portid="22">
        <state state="closed"/>
        <service name="ssh"/>
      </port>
    </ports>
  </host>
</nmaprun>
"""


def test_parse_nmap_open_ports_and_metadata() -> None:
    result = parse_nmap_xml(_SAMPLE)
    assert result.total_ports == 2
    assert result.skipped_closed == 1
    assert result.skipped_out_of_scope == 0
    assert len(result.items) == 1
    item = result.items[0]
    assert item.title == "Open https service on 443/tcp"
    assert item.target == "api.example.test:443"
    assert item.group_key == "nmap:tcp:443:https"
    assert item.details["product"] == "nginx"
    assert item.details["scripts"][0]["id"] == "ssl-cert"
    assert item.observed_at is not None


def test_parse_nmap_enforces_exact_scope() -> None:
    allowed = ScopeItem(
        kind=ScopeKind.domain,
        value="other.example.test",
        is_exclusion=False,
        source="defined",
    )
    result = parse_nmap_xml(_SAMPLE, scope_items=[allowed])
    assert result.items == []
    assert result.skipped_out_of_scope == 1


def test_parse_nmap_rejects_other_xml() -> None:
    try:
        parse_nmap_xml(b"<NessusClientData_v2 />")
    except ValueError as exc:
        assert "expected <nmaprun>" in str(exc)
    else:
        raise AssertionError("wrong root should be rejected")
