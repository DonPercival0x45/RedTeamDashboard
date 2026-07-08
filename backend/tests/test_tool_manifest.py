"""Tool manifest schema tests (v0.16.1).

Focused on the ``entity_source`` declaration — the UI hint that tells
the invocation form which engagement entity type(s) a tool sources by
default. The tool's source code is the source of truth; this just has
to round-trip and default to empty.
"""
from __future__ import annotations

from app.services.tool_manifest import manifest_to_jsonb, parse_manifest

_BASE = """\
apiVersion: rtd.tools/v1
kind: Tool
metadata:
  name: {name}
  description: d
spec:
  kind: python
  lane: analyst
  entrypoint: main.py
"""


def test_entity_source_round_trips() -> None:
    m = parse_manifest(_BASE.format(name="upn") + "  entity_source: [email]\n")
    assert m.spec.entity_source == ["email"]
    spec = manifest_to_jsonb(m)["spec"]
    assert spec["entity_source"] == ["email"]


def test_entity_source_defaults_to_empty_when_omitted() -> None:
    m = parse_manifest(_BASE.format(name="nohint"))
    assert m.spec.entity_source == []


def test_entity_source_can_list_multiple_types() -> None:
    m = parse_manifest(
        _BASE.format(name="reputation") + "  entity_source: [ip, host]\n"
    )
    assert m.spec.entity_source == ["ip", "host"]
