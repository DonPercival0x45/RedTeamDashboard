"""Tests for post-invocation finding analysis (v0.18.0).

Drives the persistence path with an injected extractor (no LLM) so the
suite stays offline + deterministic. Covers: findings created + emitted,
empty-stdout skip, and non-fatal-on-extractor-error.
"""
from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Engagement,
    Finding,
    Severity,
    Tool,
    ToolInvocation,
    ToolInvocationStatus,
    ToolKind,
    ToolLane,
    ToolStatus,
    ToolTaskKind,
    User,
)
from app.services.tool_finding_analysis import analyze_and_persist

ExtractFn = Callable[[str, Tool, Any, User], Awaitable[list[dict[str, Any]]]]


@pytest.fixture()
def engagement(db: Session) -> Engagement:
    eng = Engagement(
        name=f"analysis-{uuid.uuid4().hex[:8]}",
        slug=f"analysis-{uuid.uuid4().hex[:8]}",
    )
    db.add(eng)
    db.commit()
    db.refresh(eng)
    yield eng
    db.query(Finding).filter(Finding.engagement_id == eng.id).delete(
        synchronize_session=False
    )
    db.query(ToolInvocation).filter(
        ToolInvocation.engagement_id == eng.id
    ).delete(synchronize_session=False)
    db.query(Engagement).filter(Engagement.id == eng.id).delete(
        synchronize_session=False
    )
    db.commit()


def _make_tool(db: Session, *, task_kind: ToolTaskKind) -> Tool:
    tool = Tool(
        name=f"enum-tool-{uuid.uuid4().hex[:4]}",
        kind=ToolKind.python,
        lane=ToolLane.analyst,
        risk_level="active",
        task_kind=task_kind,
        status=ToolStatus.approved,
        manifest={"spec": {"analyze_findings": True}},
    )
    db.add(tool)
    db.commit()
    db.refresh(tool)
    return tool


def _make_invocation(
    db: Session, engagement: Engagement, tool: Tool, invoker: User, stdout: str
) -> ToolInvocation:
    inv = ToolInvocation(
        tool_id=tool.id,
        tool_version=tool.version,
        engagement_id=engagement.id,
        invoker_user_id=invoker.id,
        args={"candidates": "alice@contoso.com"},
        status=ToolInvocationStatus.completed,
        stdout=stdout,
    )
    db.add(inv)
    db.commit()
    db.refresh(inv)
    return inv


def _extract_returning(
    findings: list[dict[str, Any]],
) -> ExtractFn:
    async def _extract(stdout: str, tool: Tool, redis: Any, invoker: User):
        return findings

    return _extract


async def test_creates_findings_from_extracted_output(
    db: Session, engagement: Engagement
) -> None:
    invoker = User(email=f"u-{uuid.uuid4().hex[:6]}@example.com")
    db.add(invoker)
    db.commit()
    db.refresh(invoker)
    tool = _make_tool(db, task_kind=ToolTaskKind.enum)
    inv = _make_invocation(
        db,
        engagement,
        tool,
        invoker,
        stdout="UserName,Exists\nalice@contoso.com,TRUE\n",
    )
    fake_redis = MagicMock()

    canned = [
        {
            "title": "Account alice@contoso.com exists",
            "target": "alice@contoso.com",
            "severity": "medium",
            "summary": "GetCredentialType reports the account exists.",
        }
    ]
    created = await analyze_and_persist(
        db,
        fake_redis,
        engagement=engagement,
        invocation=inv,
        tool=tool,
        invoker=invoker,
        extract_fn=_extract_returning(canned),
    )

    assert len(created) == 1
    row = created[0]
    assert row.title == "Account alice@contoso.com exists"
    assert row.target == "alice@contoso.com"
    assert row.severity == Severity.medium
    assert row.source_tool == tool.name
    # enum task_kind -> osint phase -> auto-validated
    assert row.phase.value == "osint"
    assert row.status.value == "validated"
    # provenance stamped in details
    assert row.details["invocation_id"] == str(inv.id)
    # finding.created emitted onto the engagement stream
    assert fake_redis.xadd.called


async def test_skips_when_stdout_empty(db: Session, engagement: Engagement) -> None:
    invoker = User(email=f"u-{uuid.uuid4().hex[:6]}@example.com")
    db.add(invoker)
    db.commit()
    db.refresh(invoker)
    tool = _make_tool(db, task_kind=ToolTaskKind.enum)
    inv = _make_invocation(db, engagement, tool, invoker, stdout="")

    created = await analyze_and_persist(
        db,
        MagicMock(),
        engagement=engagement,
        invocation=inv,
        tool=tool,
        invoker=invoker,
        extract_fn=_extract_returning(
            [{"title": "should not happen", "severity": "info"}]
        ),
    )

    assert created == []
    # nothing landed in the DB
    rows = list(
        db.execute(
            select(Finding).where(Finding.engagement_id == engagement.id)
        ).scalars()
    )
    assert rows == []


async def test_non_fatal_when_extractor_raises(
    db: Session, engagement: Engagement
) -> None:
    invoker = User(email=f"u-{uuid.uuid4().hex[:6]}@example.com")
    db.add(invoker)
    db.commit()
    db.refresh(invoker)
    tool = _make_tool(db, task_kind=ToolTaskKind.enum)
    inv = _make_invocation(
        db, engagement, tool, invoker, stdout="some output"
    )

    async def _explode(stdout, tool, redis, invoker):
        raise RuntimeError("LLM blew up")

    created = await analyze_and_persist(
        db,
        MagicMock(),
        engagement=engagement,
        invocation=inv,
        tool=tool,
        invoker=invoker,
        extract_fn=_explode,
    )

    # extractor failed -> no findings, but no exception escapes
    assert created == []


async def test_exploit_task_kind_lands_pending(
    db: Session, engagement: Engagement
) -> None:
    invoker = User(email=f"u-{uuid.uuid4().hex[:6]}@example.com")
    db.add(invoker)
    db.commit()
    db.refresh(invoker)
    tool = _make_tool(db, task_kind=ToolTaskKind.exploit)
    inv = _make_invocation(db, engagement, tool, invoker, stdout="exploit output")

    created = await analyze_and_persist(
        db,
        MagicMock(),
        engagement=engagement,
        invocation=inv,
        tool=tool,
        invoker=invoker,
        extract_fn=_extract_returning(
            [{"title": "credentialed access", "severity": "high"}]
        ),
    )

    assert len(created) == 1
    # exploit phase -> NOT auto-validated (analyst review)
    assert created[0].phase.value == "exploit"
    assert created[0].status.value != "validated"
