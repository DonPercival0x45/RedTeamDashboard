"""Stage 3 — Strategic policy LLM call at lease-mint time.

Verifies ``StrategicAgent.provision_lease`` correctly invokes the policy
LLM, enforces narrow-only + charter filters on the LLM's tool list,
preserves the dispatch tool, propagates ``requires_container``, writes
an AgentExecution row in both success and failure paths, and falls back
safely when the LLM is unreachable.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.agents.strategic import StrategicAgent, _LeasePolicy
from app.models import (
    AgentExecution,
    AgentExecutionStatus,
    AgentName,
    AgentTrigger,
    Engagement,
    EngagementStatus,
    MCPLease,
    OwnerEligibility,
    ScopeItem,
    ScopeKind,
    Task,
    TaskKind,
    TaskStatus,
)


class _FakeLeaseLLM:
    """Minimal stand-in for a tool-bound chat model.

    ``with_structured_output`` returns self so callers can chain
    ``.invoke(messages)``; the canned response is what the test wants
    Strategic to receive. Pass an Exception to simulate an LLM failure
    (network, validation, etc).
    """

    def __init__(self, response: _LeasePolicy | Exception) -> None:
        self.response = response
        self.invocations: list[list[tuple[str, str]]] = []

    def with_structured_output(self, _schema: Any) -> _FakeLeaseLLM:
        return self

    def invoke(self, messages: list[tuple[str, str]]) -> Any:
        self.invocations.append(messages)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


@pytest.fixture()
def engagement(db: Session) -> Iterator[Engagement]:
    eng = Engagement(
        name="stage3-policy",
        slug=f"s3-{uuid.uuid4().hex[:8]}",
        status=EngagementStatus.active,
    )
    db.add(eng)
    db.commit()
    db.refresh(eng)
    db.add(
        ScopeItem(
            engagement_id=eng.id,
            kind=ScopeKind.domain,
            value="acme.test",
            is_exclusion=False,
        )
    )
    db.commit()
    try:
        yield eng
    finally:
        db.execute(text("SELECT flush_engagement(:id)"), {"id": eng.id})
        db.commit()


def _enum_task(db: Session, eng: Engagement) -> Task:
    t = Task(
        engagement_id=eng.id,
        title="enum subdomains for stage3",
        kind=TaskKind.enum,
        owner_eligibility=OwnerEligibility.agent,
        status=TaskStatus.pending,
        payload={"tool": "subfinder", "target": "acme.test"},
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def _executions_for(db: Session, eng: Engagement) -> list[AgentExecution]:
    return list(
        db.execute(
            select(AgentExecution)
            .where(AgentExecution.engagement_id == eng.id)
            .order_by(AgentExecution.started_at)
        ).scalars()
    )


def test_policy_narrows_tools_and_persists_requires_container(
    db: Session, engagement: Engagement
) -> None:
    """Happy path: LLM picks a valid subset + container=True → lease
    reflects it exactly, AgentExecution.completed with tokens null
    (fake LLM has no metadata)."""
    task = _enum_task(db, engagement)
    fake = _FakeLeaseLLM(
        _LeasePolicy(
            tools=["subfinder", "crt_sh"],
            requires_container=True,
            reason="task targets a known-active domain; container isolates.",
        )
    )
    agent = StrategicAgent(provider="test", model_name="fake-llm", llm=fake)
    lease = agent.provision_lease(db, task=task)
    db.commit()

    db.refresh(lease)
    assert set(lease.allowed_tools) == {"subfinder", "crt_sh"}
    assert lease.requires_container is True

    executions = _executions_for(db, engagement)
    assert len(executions) == 1
    exec_row = executions[0]
    assert exec_row.agent == AgentName.strategic
    assert exec_row.trigger == AgentTrigger.lease_provision
    assert exec_row.status == AgentExecutionStatus.completed
    assert exec_row.output is not None
    assert exec_row.output["tools"] == ["subfinder", "crt_sh"]
    assert exec_row.output["requires_container"] is True
    assert "container isolates" in exec_row.output["reason"]


def test_policy_filters_out_widening_attempt(
    db: Session, engagement: Engagement
) -> None:
    """LLM returns a tool NOT in pack defaults → filtered. Only
    pack-intersection tools survive."""
    task = _enum_task(db, engagement)
    fake = _FakeLeaseLLM(
        _LeasePolicy(
            # port_scan is NOT in the enum pack defaults — widening
            # attempt. crt_sh IS in the pack — keeps.
            tools=["subfinder", "port_scan", "crt_sh"],
            requires_container=False,
            reason="trying to be thorough",
        )
    )
    agent = StrategicAgent(provider="test", model_name="fake-llm", llm=fake)
    lease = agent.provision_lease(db, task=task)
    db.commit()

    db.refresh(lease)
    assert "port_scan" not in lease.allowed_tools
    assert set(lease.allowed_tools) == {"subfinder", "crt_sh"}

    exec_row = _executions_for(db, engagement)[0]
    # The full LLM-proposed list is preserved for audit even when filtered.
    assert "port_scan" in exec_row.output["llm_proposed_tools"]
    assert "port_scan" not in exec_row.output["tools"]


def test_policy_reinserts_dispatch_tool_when_llm_drops_it(
    db: Session, engagement: Engagement
) -> None:
    """If the LLM omits the dispatch tool, we re-add it — without it the
    worker can't execute the task at all."""
    task = _enum_task(db, engagement)  # dispatch tool = subfinder
    fake = _FakeLeaseLLM(
        _LeasePolicy(
            tools=["crt_sh", "dns_lookup"],  # subfinder dropped
            requires_container=False,
            reason="the dispatch tool isn't useful here",
        )
    )
    agent = StrategicAgent(provider="test", model_name="fake-llm", llm=fake)
    lease = agent.provision_lease(db, task=task)
    db.commit()

    db.refresh(lease)
    # subfinder re-added so the worker can dispatch.
    assert "subfinder" in lease.allowed_tools
    assert "crt_sh" in lease.allowed_tools
    assert "dns_lookup" in lease.allowed_tools


def test_policy_falls_back_when_no_provider_key(
    db: Session, engagement: Engagement
) -> None:
    """No provider key for the engagement creator → _resolve_llm raises
    → failed AgentExecution + pack defaults + requires_container=False.
    Lease still mints."""
    task = _enum_task(db, engagement)
    # No `llm=` injection and the engagement creator has no key — the
    # real _resolve_llm path will try to build ChatAnthropic with no key
    # and blow up. Force the failure deterministically via a raising
    # fake so the test doesn't depend on env vars.
    fake = _FakeLeaseLLM(RuntimeError("no key configured"))
    agent = StrategicAgent(provider="test", model_name="fake-llm", llm=fake)
    lease = agent.provision_lease(db, task=task)
    db.commit()

    db.refresh(lease)
    # Pack defaults preserved verbatim.
    assert "subfinder" in lease.allowed_tools
    assert "crt_sh" in lease.allowed_tools
    assert lease.requires_container is False

    exec_row = _executions_for(db, engagement)[0]
    assert exec_row.status == AgentExecutionStatus.failed
    assert exec_row.error is not None
    assert "no key configured" in exec_row.error


def test_policy_explicit_kwarg_bypasses_llm(
    db: Session, engagement: Engagement
) -> None:
    """``provision_lease(requires_container=...)`` skips the LLM entirely.
    Used by callers that already know what they want — no AgentExecution
    row is written for the bypass."""
    task = _enum_task(db, engagement)
    fake = _FakeLeaseLLM(
        _LeasePolicy(
            tools=["subfinder"],
            requires_container=False,
            reason="should not be called",
        )
    )
    agent = StrategicAgent(provider="test", model_name="fake-llm", llm=fake)
    lease = agent.provision_lease(db, task=task, requires_container=True)
    db.commit()

    db.refresh(lease)
    # Explicit kwarg → container=True, full pack defaults (LLM bypassed).
    assert lease.requires_container is True
    assert "crt_sh" in lease.allowed_tools  # pack default present
    # LLM never invoked.
    assert fake.invocations == []
    # No execution row written for the bypass.
    assert _executions_for(db, engagement) == []


def test_policy_full_lease_record_via_engagement_relation(
    db: Session, engagement: Engagement
) -> None:
    """Sanity: one engagement → one minted lease with the policy-decided
    tool list, queryable via the engagement_id relation."""
    task = _enum_task(db, engagement)
    fake = _FakeLeaseLLM(
        _LeasePolicy(
            tools=["subfinder"],
            requires_container=True,
            reason="minimal surface — only the dispatch tool needed.",
        )
    )
    agent = StrategicAgent(provider="test", model_name="fake-llm", llm=fake)
    agent.provision_lease(db, task=task)
    db.commit()

    leases = list(
        db.execute(
            select(MCPLease).where(MCPLease.engagement_id == engagement.id)
        ).scalars()
    )
    assert len(leases) == 1
    assert leases[0].allowed_tools == ["subfinder"]
    assert leases[0].requires_container is True
