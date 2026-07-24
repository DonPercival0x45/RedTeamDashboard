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
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Engagement,
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
from app.services.playbook import InternalExecutor, catalog, load_seed_playbooks
from app.services.playbook.executor import StepResult
from app.services.playbook.runner import start_run


@pytest.fixture()
def engagement(db: Session) -> Engagement:
    eng = Engagement(
        name="4a persist",
        slug=f"p4a-{uuid.uuid4().hex[:8]}",
        status=EngagementStatus.active,
        work_state=EngagementWorkState.active,
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
                    "MX": ["10 mail.foo.example"],
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
    # 3 answers across A + MX persisted as items.
    assert run.findings_new == 3
    assert run.findings_total == 3

    # A canonical Finding row exists, grouped by (tool, target), with all items.
    finding = db.execute(
        select(Finding).where(
            Finding.engagement_id == engagement.id,
            Finding.group_key == "playbook:dns-inventory:foo.example",
        )
    ).scalar_one()
    assert finding.source_tool == "dns-inventory"
    assert finding.target == "foo.example"
    assert finding.status is FindingStatus.validated  # OSINT auto-validates
    items = (finding.details or {}).get("items") or []
    labels = {i.get("label") for i in items}
    assert labels == {
        "A=203.0.113.10",
        "A=203.0.113.11",
        "MX=10 mail.foo.example",
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
    # Still exactly one canonical row.
    rows = db.execute(
        select(Finding).where(
            Finding.engagement_id == engagement.id,
            Finding.group_key == "playbook:dns-inventory:foo.example",
        )
    ).scalars().all()
    assert len(rows) == 1
    assert len((rows[0].details or {}).get("items") or []) == 3


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
