"""Playbook finding persistence — operator complaint 4a.

A playbook run must surface its findings in the engagement's Findings table and
link them back to the run (``FindingOrigin.thread_id == run.id``) so the
post-run gather-then-analyze milestone has a batch to analyze — instead of
showing a count in the run detail while the Findings tab stays empty.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import (
    Engagement,
    EngagementArchitecture,
    EngagementStatus,
    EngagementWorkState,
    Finding,
    FindingOrigin,
    FindingStatus,
    Playbook,
    PlaybookRunStatus,
    PlaybookStep,
    ScopeItem,
    ScopeKind,
    User,
    UserRole,
)
from app.services import methodology as meth
from app.services.entities import extract_entities, include_scope_entities
from app.services.playbook import InternalExecutor, catalog, load_seed_playbooks
from app.services.playbook.executor import MCPExecutor, StepResult
from app.services.playbook.finding_bridge import bridge_step_to_finding
from app.services.playbook.runner import start_run


@pytest.fixture()
def engagement(db: Session) -> Engagement:
    eng = Engagement(
        name="4a persist",
        slug=f"p4a-{uuid.uuid4().hex[:8]}",
        status=EngagementStatus.active,
        work_state=EngagementWorkState.active,
        intelligence_architecture=EngagementArchitecture.v3,
    )
    db.add(eng)
    db.flush()
    db.add(
        ScopeItem(
            engagement_id=eng.id, kind=ScopeKind.domain, value="foo.example"
        )
    )
    db.commit()
    meth.load_seed_catalog(db)
    meth.select_for_engagement(
        db,
        engagement_id=eng.id,
        slug="osint-minimal",
        now=datetime(2026, 7, 23, tzinfo=UTC),
    )
    db.commit()
    return eng


@pytest.fixture()
def dns_playbook(db: Session) -> Playbook:
    pb = Playbook(
        slug=f"dns4a-{uuid.uuid4().hex[:6]}",
        version=1,
        name="DNS persist test",
        description="one dns-inventory step",
        applies_to_asset_class="domain",
        active=False,
    )
    db.add(pb)
    db.flush()
    db.add(
        PlaybookStep(
            playbook_id=pb.id,
            sort_order=10,
            tool_slug="dns-inventory",
            args_template={"domain": "{{scope_item}}"},
            satisfies_node_ids=[],
        )
    )
    db.commit()
    return pb


class _FakeDnsExecutor(InternalExecutor):
    """Registry override: dns-inventory returns canned records (no network)."""

    def __init__(self) -> None:
        super().__init__(registry={"dns-inventory": self._dns})

    @staticmethod
    def _dns(scope_context: str, args: dict) -> StepResult:
        return StepResult(
            ok=True,
            data={
                "records": {
                    "A": ["203.0.113.10", "203.0.113.11"],
                    "AAAA": ["2001:db8::10"],
                    "CNAME": ["alias.foo.example"],
                    "MX": ["10 mail.foo.example"],
                    "TXT": ["v=spf1 -all"],
                    "NS": ["ns1.foo.example"],
                }
            },
        )


def test_run_persists_findings_and_links_to_run(
    db: Session, engagement: Engagement, dns_playbook: Playbook
) -> None:
    user = User(
        id=uuid.uuid4(),
        email=f"p4a-{uuid.uuid4().hex[:6]}@example.com",
        display_name="4a",
        role=UserRole.user,
        is_active=True,
    )
    db.add(user)
    db.commit()

    run = start_run(
        db,
        engagement=engagement,
        playbook=dns_playbook,
        scope_subset=["foo.example"],
        executor=_FakeDnsExecutor(),
        actor_id=str(user.id),
        requested_by=user.id,
    )
    db.commit()

    assert run.status is PlaybookRunStatus.completed
    # All six DNS record types persist through canonical grouping.
    assert run.findings_new == 7
    assert run.findings_total == 7

    # A canonical Finding row exists, grouped by (tool, target), with all items.
    finding = db.execute(
        select(Finding).where(
            Finding.engagement_id == engagement.id,
            Finding.group_key == "dns_records:foo.example",
        )
    ).scalar_one()
    assert finding.source_tool == "dns_lookup"
    assert finding.target == "foo.example"
    assert finding.status is FindingStatus.validated  # OSINT auto-validates
    items = (finding.details or {}).get("items") or []
    labels = {f"{i.get('type')}={i.get('value')}" for i in items}
    assert labels == {
        "A=203.0.113.10",
        "A=203.0.113.11",
        "AAAA=2001:db8::10",
        "CNAME=alias.foo.example",
        "MX=10 mail.foo.example",
        "TXT=v=spf1 -all",
        "NS=ns1.foo.example",
    }

    # The finding links back to the run so post-run analysis can gather it.
    origin = db.execute(
        select(FindingOrigin).where(
            FindingOrigin.finding_id == finding.id,
            FindingOrigin.thread_id == run.id,
        )
    ).scalar_one()
    assert origin.source_tool == "dns-inventory"


def test_rerun_dedups_items_instead_of_duplicating(
    db: Session, engagement: Engagement, dns_playbook: Playbook
) -> None:
    executor = _FakeDnsExecutor()
    start_run(
        db,
        engagement=engagement,
        playbook=dns_playbook,
        scope_subset=["foo.example"],
        executor=executor,
    )
    db.commit()
    # Second run with the same canned output: zero NEW items.
    run2 = start_run(
        db,
        engagement=engagement,
        playbook=dns_playbook,
        scope_subset=["foo.example"],
        executor=executor,
    )
    db.commit()
    assert run2.findings_new == 0
    assert run2.findings_total == 7
    # Still exactly one canonical row.
    rows = db.execute(
        select(Finding).where(
            Finding.engagement_id == engagement.id,
            Finding.group_key == "dns_records:foo.example",
        )
    ).scalars().all()
    assert len(rows) == 1
    assert len((rows[0].details or {}).get("items") or []) == 7
    rerun_origin = db.execute(
        select(FindingOrigin).where(
            FindingOrigin.finding_id == rows[0].id,
            FindingOrigin.thread_id == run2.id,
        )
    ).scalar_one()
    assert rerun_origin.source_tool == "dns-inventory"


def test_seeded_whois_resolves_scope_target_and_reruns_dedup(
    db: Session,
    engagement: Engagement,
) -> None:
    user = User(
        email=f"seeded-playbook-{uuid.uuid4().hex[:8]}@example.test",
        role=UserRole.user,
    )
    db.add(user)
    load_seed_playbooks(db)
    db.commit()
    playbook = catalog.get_by_slug(db, "osint-passive-domain")
    assert playbook is not None
    whois_step = next(step for step in playbook.steps if step.tool_slug == "whois")
    assert whois_step.args_template == {"domain": "{{scope_item}}"}
    calls: list[tuple[str, dict]] = []

    def fake_dns(scope_context: str, args: dict) -> StepResult:
        assert scope_context == "foo.example"
        assert args == {"domain": "foo.example"}
        return StepResult(
            ok=True,
            data={
                "records": {
                    "A": ["203.0.113.10"],
                    "AAAA": ["2001:db8::10"],
                    "CNAME": ["Alias.Foo.Example."],
                    "MX": ["10 Mail.Foo.Example."],
                    "NS": ["NS1.Foo.Example."],
                }
            },
        )

    def fake_whois(scope_context: str, args: dict) -> StepResult:
        calls.append((scope_context, dict(args)))
        return StepResult(
            ok=True,
            data={
                "record": {
                    "domain": "other.example",
                    "registrar": "Example Registrar",
                    "emails": ["abuse@foo.example"],
                    "name_servers": ["ns1.foo.example"],
                }
            },
        )

    def stub(*_args, **_kwargs) -> StepResult:
        return StepResult(ok=True, stub=True, data={})

    executor = InternalExecutor(
        registry={
            **{step.tool_slug: stub for step in playbook.steps},
            "dns-inventory": fake_dns,
            "whois": fake_whois,
        }
    )
    run1 = start_run(
        db,
        engagement=engagement,
        playbook=playbook,
        scope_subset=["foo.example"],
        executor=executor,
        actor_id=str(user.id),
        requested_by=user.id,
    )
    db.commit()
    run2 = start_run(
        db,
        engagement=engagement,
        playbook=playbook,
        scope_subset=["foo.example"],
        executor=executor,
        actor_id=str(user.id),
        requested_by=user.id,
    )
    db.commit()

    assert calls == [
        ("foo.example", {"domain": "foo.example"}),
        ("foo.example", {"domain": "foo.example"}),
    ]
    assert (run1.findings_new, run1.findings_total) == (6, 6)
    assert (run2.findings_new, run2.findings_total) == (0, 6)
    rows = list(
        db.execute(
            select(Finding).where(
                Finding.engagement_id == engagement.id,
                Finding.source_tool == "whois_lookup",
                Finding.deleted_at.is_(None),
            )
        ).scalars()
    )
    assert len(rows) == 1
    finding = rows[0]
    assert finding.group_key == "whois:foo.example"
    assert finding.target == "foo.example"
    assert "{{scope_item}}" not in finding.title
    assert finding.details["args"]["domain"] == "foo.example"
    assert finding.details["items"][0]["domain"] == "foo.example"
    assert len(finding.details["items"]) == 1
    origins = list(
        db.execute(
            select(FindingOrigin).where(FindingOrigin.finding_id == finding.id)
        ).scalars()
    )
    assert {origin.thread_id for origin in origins} == {run1.id, run2.id}

    active_findings = list(
        db.execute(
            select(Finding).where(
                Finding.engagement_id == engagement.id,
                Finding.deleted_at.is_(None),
            )
        ).scalars()
    )
    scope_items = list(
        db.execute(
            select(ScopeItem).where(ScopeItem.engagement_id == engagement.id)
        ).scalars()
    )
    entities = include_scope_entities(
        extract_entities(active_findings), scope_items
    )
    assert {(entity["type"], entity["value"]) for entity in entities} == {
        ("domain", "foo.example"),
        ("domain", "alias.foo.example"),
        ("domain", "mail.foo.example"),
        ("domain", "ns1.foo.example"),
        ("ip", "203.0.113.10"),
        ("ip", "2001:db8::10"),
        ("email", "abuse@foo.example"),
    }
    scope_entity = next(
        entity for entity in entities if entity["value"] == "foo.example"
    )
    assert scope_entity["count"] == 2
    assert len(scope_entity["findings"]) == 2


def test_mcp_enrichment_binds_context_secret_and_persists_canonical_finding(
    db: Session,
    engagement: Engagement,
) -> None:
    playbook = Playbook(
        slug=f"mcp-enrichment-{uuid.uuid4().hex[:6]}",
        version=1,
        name="MCP enrichment persistence",
        applies_to_asset_class="ip",
        active=False,
    )
    db.add(playbook)
    db.flush()
    db.add(
        PlaybookStep(
            playbook_id=playbook.id,
            sort_order=10,
            tool_slug="freeipapi",
            args_template={"ip": "wrong.example"},
            satisfies_node_ids=[],
        )
    )
    user = User(
        email=f"mcp-playbook-{uuid.uuid4().hex[:8]}@example.test",
        role=UserRole.user,
    )
    db.add(user)
    db.commit()

    calls: list[dict] = []

    class _Tool:
        name = "freeipapi"

        async def ainvoke(self, args: dict) -> dict:
            calls.append(dict(args))
            return {
                "ip": "198.51.100.20",  # untrusted response cannot retarget persistence
                "country_name": "Exampleland",
                "latitude": 1.25,
                "longitude": 2.5,
            }

    executor = MCPExecutor(
        base_url="http://mcp.test/sse",
        api_key="worker-key",
        lease_token="lease-token",
        engagement_slug=engagement.slug,
        tool_secrets={"freeipapi": "analyst-secret"},
    )
    executor._tool_cache = {"freeipapi": _Tool()}  # noqa: SLF001

    run = start_run(
        db,
        engagement=engagement,
        playbook=playbook,
        scope_subset=["1.2.3.4"],
        executor=executor,
        actor_id=str(user.id),
        requested_by=user.id,
    )
    db.commit()

    assert calls == [
        {
            "ip": "1.2.3.4",
            "engagement_slug": engagement.slug,
            "api_key": "analyst-secret",
        }
    ]
    assert (run.findings_new, run.findings_total) == (1, 1)
    finding = db.execute(
        select(Finding).where(
            Finding.engagement_id == engagement.id,
            Finding.group_key == "ip_enrichment:1.2.3.4",
            Finding.deleted_at.is_(None),
        )
    ).scalar_one()
    assert finding.target == "1.2.3.4"
    assert finding.details["items"][0]["ip"] == "1.2.3.4"
    assert finding.details["items"][0]["country_name"] == "Exampleland"
    assert db.execute(
        select(FindingOrigin).where(
            FindingOrigin.finding_id == finding.id,
            FindingOrigin.thread_id == run.id,
        )
    ).scalar_one()


def test_whois_rerun_retires_literal_scope_group_and_persists_real_target(
    db: Session,
    engagement: Engagement,
) -> None:
    playbook = Playbook(
        slug=f"whois-repair-{uuid.uuid4().hex[:6]}",
        version=1,
        name="WHOIS repair test",
        description="one WHOIS step",
        applies_to_asset_class="domain",
        active=False,
    )
    db.add(playbook)
    db.flush()
    db.add(
        PlaybookStep(
            playbook_id=playbook.id,
            sort_order=10,
            tool_slug="whois",
            args_template={"domain": "{{scope_item}}"},
            satisfies_node_ids=[],
        )
    )
    legacy = Finding(
        engagement_id=engagement.id,
        title="WHOIS record — {{scope_item}}",
        target="{{scope_item}}",
        source_tool="whois_lookup",
        group_key="whois:{{scope_item}}",
        status=FindingStatus.validated,
        details={
            "args": {"domain": "{{scope_item}}"},
            "grouped": True,
            "items": [
                {
                    "domain": "{{scope_item}}",
                    "registrar": "stale registrar",
                }
            ],
        },
    )
    db.add(legacy)
    db.commit()

    class _FakeWhoisExecutor(InternalExecutor):
        def __init__(self) -> None:
            super().__init__(registry={"whois": self._whois})

        @staticmethod
        def _whois(scope_context: str, args: dict) -> StepResult:
            assert scope_context == "foo.example"
            assert args["domain"] == "foo.example"
            return StepResult(
                ok=True,
                data={
                    "record": {
                        "registrar": "Example Registrar",
                        "name_servers": ["ns1.foo.example"],
                    }
                },
            )

    executor = _FakeWhoisExecutor()
    run = start_run(
        db,
        engagement=engagement,
        playbook=playbook,
        scope_subset=["foo.example"],
        executor=executor,
    )
    db.commit()

    db.refresh(legacy)
    assert legacy.deleted_at is not None
    assert legacy.details["retired_reason"] == "literal playbook scope template"
    assert run.findings_new == 1
    assert run.findings_total == 1
    finding = db.execute(
        select(Finding).where(
            Finding.engagement_id == engagement.id,
            Finding.group_key == "whois:foo.example",
            Finding.deleted_at.is_(None),
        )
    ).scalar_one()
    assert finding.title == "WHOIS record — foo.example"
    assert finding.target == "foo.example"
    assert finding.details["args"]["domain"] == "foo.example"
    assert finding.details["items"][0]["domain"] == "foo.example"
    assert finding.details["items"][0]["registrar"] == "Example Registrar"

    rerun = start_run(
        db,
        engagement=engagement,
        playbook=playbook,
        scope_subset=["foo.example"],
        executor=executor,
    )
    db.commit()
    assert rerun.findings_new == 0
    assert rerun.findings_total == 1
    assert db.execute(
        select(Finding).where(
            Finding.engagement_id == engagement.id,
            Finding.deleted_at.is_(None),
            Finding.group_key == "whois:foo.example",
        )
    ).scalars().all() == [finding]


def test_bridge_failure_does_not_poison_playbook_transaction(
    db: Session,
    engagement: Engagement,
    dns_playbook: Playbook,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken_upsert(*_args, **_kwargs):
        # Real database failure leaves a transaction aborted unless the bridge
        # contains it in a SAVEPOINT.
        db.execute(text("SELECT 1 / 0"))

    monkeypatch.setattr(
        "app.services.playbook.finding_bridge.upsert_grouped_finding",
        broken_upsert,
    )
    run = start_run(
        db,
        engagement=engagement,
        playbook=dns_playbook,
        scope_subset=["foo.example"],
        executor=_FakeDnsExecutor(),
    )
    db.commit()
    assert run.status is PlaybookRunStatus.failed
    assert run.steps_succeeded == 0
    assert run.steps_failed == 1
    assert run.findings_new == 0
    assert run.findings_total == 0
    assert run.last_error == "could not persist canonical dns_lookup finding"


def test_dehashed_lookup_persists_canonical_finding_and_deduplicates(
    db: Session, engagement: Engagement
) -> None:
    thread_id = uuid.uuid4()
    record_id = str(uuid.uuid4())
    payload = {
        "provider": "dehashed_import",
        "email": "Analyst@example.com",
        "records": [
            {
                "email": "Analyst@example.com",
                "database_name": "Example breach",
                "entity_id": record_id,
            }
        ],
    }

    first = bridge_step_to_finding(
        db,
        engagement_id=engagement.id,
        playbook_tool="breach-lookup",
        scope_item="Analyst@example.com",
        args_template={"email": "{{scope_item}}"},
        data=payload,
        thread_id=thread_id,
    )
    second = bridge_step_to_finding(
        db,
        engagement_id=engagement.id,
        playbook_tool="breach-lookup",
        scope_item="Analyst@example.com",
        args_template={"email": "{{scope_item}}"},
        data=payload,
        thread_id=thread_id,
    )
    db.commit()

    assert first is not None and first.created is True
    assert second is not None and second.items_added == 0
    row = db.execute(
        select(Finding).where(
            Finding.engagement_id == engagement.id,
            Finding.group_key == "dehashed:Analyst@example.com",
        )
    ).scalar_one()
    assert len(row.details["items"]) == 1


def test_stub_steps_persist_nothing(
    db: Session, engagement: Engagement, dns_playbook: Playbook
) -> None:
    """A stub step produces zero candidate findings and zero Finding rows."""
    load_seed_playbooks(db)
    db.commit()
    pb = catalog.get_by_slug(db, "osint-passive-domain")
    assert pb is not None

    class _StubOnly(InternalExecutor):
        def __init__(self) -> None:
            super().__init__(
                registry={
                    "subfinder": lambda *_a, **_k: StepResult(
                        ok=True, stub=True, data={}
                    )
                }
            )

    run = start_run(
        db,
        engagement=engagement,
        playbook=pb,
        scope_subset=["foo.example"],
        executor=_StubOnly(),
    )
    db.commit()
    assert run.findings_new == 0
    count = db.execute(
        select(Finding).where(Finding.engagement_id == engagement.id)
    ).scalars().all()
    assert count == []
