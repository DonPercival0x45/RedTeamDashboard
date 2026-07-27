from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.playbook import _validate_scope_subset
from app.models import Engagement, Finding, Playbook, ScopeItem, ScopeKind
from app.services.finding_grouping import compute_group_key, extract_items
from app.services.playbook import catalog, load_seed_playbooks
from app.services.playbook.finding_bridge import _translate, bridge_step_to_finding
from app.services.playbook.planning import build_execution_plan
from app.services.playbook.tools import posture
from app.services.playbook.tools.scope_hygiene import run_scope_hygiene


def test_keyless_playbooks_are_seeded_without_credentials(db: Session) -> None:
    load_seed_playbooks(db)
    expected = {
        "scope-hygiene-review": ("scope", 1),
        "dns-ownership-boundary": ("domain", 1),
        "dangling-dns-triage": ("domain", 3),
        "web-security-baseline": ("domain", 1),
        "mail-dns-posture": ("domain", 1),
        "cloud-edge-boundary": ("domain", 1),
    }
    for slug, (asset_class, step_count) in expected.items():
        playbook = catalog.get_by_slug(db, slug)
        assert playbook is not None
        assert playbook.applies_to_asset_class == asset_class
        assert len(playbook.steps) == step_count

    for slug, version in (
        ("web-security-baseline", 2),
        ("cloud-edge-boundary", 2),
    ):
        playbook = catalog.get_by_slug(db, slug)
        assert playbook is not None
        assert playbook.version == version
        assert playbook.active is True

    for slug, version in (
        ("domain-web-surface", 3),
        ("web-security-baseline", 1),
        ("cloud-edge-boundary", 1),
    ):
        pinned = catalog.get_by_slug(db, slug, version)
        assert pinned is not None
        assert pinned.active is False
        plan = build_execution_plan(
            playbook=pinned,
            scope_subset=["example.com"],
            required_executor="mcp" if slug == "domain-web-surface" else "internal",
        )
        assert plan["approval_required"] is True
        assert any(step["risk"] == "active" for step in plan["steps"])


def test_composite_playbooks_are_seeded_with_governed_recipes(db: Session) -> None:
    load_seed_playbooks(db)
    load_seed_playbooks(db)

    expected = {
        "external-attack-surface-baseline": {
            "category": "enumeration",
            "entity_types": ["domain"],
            "active": True,
            "steps": [
                "whois",
                "mcp_subfinder",
                "mcp_crt_sh",
                "mcp_dns_lookup",
                "dns-ownership-boundary",
                "cloud-edge-boundary",
                "mail-auth-posture",
                "dangling-dns-triage",
                "web-security-baseline",
            ],
        },
        "ip-exposure-triage": {
            "category": "validation",
            "entity_types": ["ip"],
            "active": True,
            "steps": [
                "mcp_reverse_dns",
                "freeipapi",
                "ipinfo",
                "mcp_port_scan",
                "mcp_service_detect",
            ],
        },
        "domain-decommission-risk-review": {
            "category": "validation",
            "entity_types": ["domain"],
            "active": True,
            "steps": [
                "mcp_dns_lookup",
                "mcp_crt_sh",
                "dns-ownership-boundary",
                "cloud-edge-boundary",
                "dangling-dns-triage",
            ],
        },
    }

    rows = list(db.scalars(select(Playbook).where(Playbook.slug.in_(expected))))
    assert len(rows) == len(expected)
    for playbook in rows:
        contract = expected[playbook.slug]
        assert playbook.version == 1
        assert playbook.origin == "system"
        assert playbook.category == contract["category"]
        assert playbook.applicable_entity_types == contract["entity_types"]
        assert playbook.active is contract["active"]
        assert [step.tool_slug for step in playbook.steps] == contract["steps"]

    external = catalog.get_by_slug(db, "external-attack-surface-baseline")
    assert external is not None
    assert external.steps[-2].args_template["__target_source"] == "discovered_domains"
    assert external.steps[-1].args_template["__target_source"] == "discovered_domains"

    ip_triage = catalog.get_by_slug(db, "ip-exposure-triage")
    assert ip_triage is not None
    port_scan = next(step for step in ip_triage.steps if step.tool_slug == "mcp_port_scan")
    service_detect = next(
        step for step in ip_triage.steps if step.tool_slug == "mcp_service_detect"
    )
    assert port_scan.args_template["ports"] == service_detect.args_template["ports"]
    assert port_scan.args_template["__on_error"] == "stop"

    for slug in (
        "external-attack-surface-baseline",
        "ip-exposure-triage",
        "domain-decommission-risk-review",
    ):
        playbook = catalog.get_by_slug(db, slug)
        assert playbook is not None
        plan = build_execution_plan(
            playbook=playbook,
            scope_subset=["203.0.113.7" if slug == "ip-exposure-triage" else "example.com"],
            required_executor="mcp",
        )
        assert plan["approval_required"] is True

    external_plan = build_execution_plan(
        playbook=external,
        scope_subset=["example.com"],
        required_executor="mcp",
    )
    risk_by_tool = {step["tool_slug"]: step["risk"] for step in external_plan["steps"]}
    assert risk_by_tool["cloud-edge-boundary"] == "active"
    assert risk_by_tool["web-security-baseline"] == "active"


def test_mail_posture_reports_bounded_keyless_policy_gaps(monkeypatch) -> None:
    answers = {
        ("example.com", "MX"): (["10 mail.example.com"], None),
        ("example.com", "TXT"): (["v=spf1 -all"], None),
        ("_dmarc.example.com", "TXT"): (["v=DMARC1; p=none"], None),
        ("_mta-sts.example.com", "TXT"): ([], "nxdomain"),
        ("_smtp._tls.example.com", "TXT"): ([], "nxdomain"),
    }
    monkeypatch.setattr(posture, "_query", lambda name, kind: answers[(name, kind)])

    result = posture.run_mail_auth_posture("example.com", {})

    assert result.ok is True
    assert {item["code"] for item in result.data["issues"]} == {
        "dmarc_monitoring_only",
        "mta_sts_missing",
        "tls_rpt_missing",
    }
    assert result.data["dkim"]["status"] == "not_tested"


def test_dangling_dns_only_flags_nxdomain_cname_target(monkeypatch) -> None:
    answers = {
        ("app.example.com", "CNAME"): (["missing.vendor.example"], None),
        ("missing.vendor.example", "A"): ([], "nxdomain"),
        ("missing.vendor.example", "AAAA"): ([], "nxdomain"),
    }
    monkeypatch.setattr(posture, "_query", lambda name, kind: answers[(name, kind)])

    result = posture.run_dangling_dns_triage("app.example.com", {})

    assert result.ok is True
    assert result.data["terminal"]["state"] == "nxdomain"
    assert result.data["issues"][0]["code"] == "dangling_cname_candidate"
    assert "confirmed" not in result.data["issues"][0]["message"].lower()


def test_web_baseline_uses_one_snapshot_and_reports_header_gaps(monkeypatch) -> None:
    monkeypatch.setattr(
        posture,
        "_http_snapshot",
        lambda _scope, _args: {
            "url": "https://example.com",
            "status": 200,
            "headers": {"x-content-type-options": "nosniff"},
            "cookies": [],
            "redirect": None,
        },
    )

    result = posture.run_web_security_baseline("example.com", {})

    assert result.ok is True
    assert {item["code"] for item in result.data["issues"]} == {
        "hsts_missing",
        "csp_missing",
        "referrer_policy_missing",
    }


def test_scope_asset_class_accepts_mixed_exact_includes_only(db: Session) -> None:
    engagement = Engagement(name="Mixed", slug=f"mixed-{uuid.uuid4().hex[:8]}")
    db.add(engagement)
    db.flush()
    db.add_all(
        [
            ScopeItem(
                engagement_id=engagement.id,
                kind=ScopeKind.domain,
                value="example.com",
            ),
            ScopeItem(
                engagement_id=engagement.id,
                kind=ScopeKind.ip,
                value="203.0.113.7",
            ),
        ]
    )
    db.flush()

    _validate_scope_subset(
        db,
        engagement,
        ["example.com", "203.0.113.7"],
        asset_class="scope",
    )
    with pytest.raises(HTTPException, match="exact include row"):
        _validate_scope_subset(
            db,
            engagement,
            ["api.example.com"],
            asset_class="scope",
        )


def test_scope_hygiene_is_report_only_and_uses_provenance(db: Session) -> None:
    engagement = Engagement(name="Hygiene", slug=f"hygiene-{uuid.uuid4().hex[:8]}")
    db.add(engagement)
    db.flush()
    defined = ScopeItem(
        engagement_id=engagement.id,
        kind=ScopeKind.domain,
        value="example.com",
        source="defined",
    )
    discovered = ScopeItem(
        engagement_id=engagement.id,
        kind=ScopeKind.ip,
        value="127.0.0.1",
        source="found",
    )
    db.add_all([defined, discovered])
    db.flush()
    before = [(item.id, item.value, item.source) for item in (defined, discovered)]

    result = run_scope_hygiene(
        db,
        engagement_id=engagement.id,
        scope_context="127.0.0.1",
    )

    assert result.ok is True
    issue = result.data["issues"][0]
    assert issue["code"] == "non_global_discovered_ip"
    assert issue["recommendation"] == "remove_or_document"
    assert [(item.id, item.value, item.source) for item in (defined, discovered)] == before


def test_posture_result_persists_as_one_canonical_finding(db: Session) -> None:
    engagement = Engagement(name="Posture", slug=f"posture-{uuid.uuid4().hex[:8]}")
    db.add(engagement)
    db.flush()

    result = bridge_step_to_finding(
        db,
        engagement_id=engagement.id,
        playbook_tool="mail-auth-posture",
        scope_item="example.com",
        args_template={"domain": "{{scope_item}}"},
        data={
            "check": "mail_auth_posture",
            "domain": "example.com",
            "issues": [
                {
                    "code": "dmarc_missing",
                    "target": "example.com",
                    "message": "No DMARC record was observed",
                }
            ],
        },
        thread_id=None,
    )

    assert result is not None and result.created is True
    finding = db.execute(
        select(Finding).where(Finding.id == result.finding_id)
    ).scalar_one()
    assert finding.group_key == "posture:mail_auth_posture:example.com"
    assert finding.title == "Mail authentication posture — example.com"
    assert finding.details["items"][0]["code"] == "dmarc_missing"


def test_posture_translation_has_stable_group_and_item_contract() -> None:
    grouping_tool, data = _translate(
        "dns-ownership-boundary",
        {"domain": "example.com"},
        {
            "check": "dns_ownership_boundary",
            "domain": "example.com",
            "issues": [
                {
                    "code": "external_dns_dependency",
                    "target": "example.com",
                    "message": "External NS",
                }
            ],
        },
    )
    assert grouping_tool == "posture_check"
    assert data is not None
    key = compute_group_key(grouping_tool, {"domain": "example.com"}, data)
    assert key == "posture:dns_ownership_boundary:example.com"
    assert extract_items(grouping_tool, data)[0]["code"] == "external_dns_dependency"
