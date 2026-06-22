"""MCP-server-side lease filtering.

When a request carries a valid lease, the server-side ``_require_tool_in_lease``
helper rejects calls to tools outside the lease's allowed surface, and the
``lease://current`` resource exposes the curated context. Without a lease,
every tool stays open (legacy path).
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.mcp import auth as mcp_auth
from app.mcp.server import (
    _require_tool_in_lease,
    resource_lease_current,
)
from app.models import (
    Engagement,
    EngagementStatus,
    MCPLease,
    MCPLeaseStatus,
    OwnerEligibility,
    Task,
    TaskKind,
    TaskStatus,
)


@pytest.fixture()
def engagement(db: Session) -> Iterator[Engagement]:
    eng = Engagement(
        name="lease-filter-test",
        slug=f"lease-filter-{uuid.uuid4().hex[:8]}",
        status=EngagementStatus.active,
    )
    db.add(eng)
    db.commit()
    db.refresh(eng)
    try:
        yield eng
    finally:
        db.execute(text("SELECT flush_engagement(:id)"), {"id": eng.id})
        db.commit()


@pytest.fixture()
def task(db: Session, engagement: Engagement) -> Task:
    t = Task(
        engagement_id=engagement.id,
        title="filter test",
        kind=TaskKind.enum,
        owner_eligibility=OwnerEligibility.agent,
        status=TaskStatus.pending,
        payload={},
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def _build_lease(task: Task, allowed: list[str]) -> MCPLease:
    """Build an in-memory MCPLease for direct ContextVar injection."""
    return MCPLease(
        id=uuid.uuid4(),
        task_id=task.id,
        engagement_id=task.engagement_id,
        allowed_tools=allowed,
        context={
            "engagement": {"slug": "x", "name": "X", "description": None},
            "scope": [],
            "task": {"id": str(task.id), "title": task.title, "kind": "enum"},
        },
        prompt_keys=["passive_recon"],
        status=MCPLeaseStatus.active.value,
        created_at=datetime.now(tz=UTC),
        expires_at=datetime.now(tz=UTC) + timedelta(seconds=600),
    )


def test_no_lease_means_no_filtering(task: Task) -> None:
    """Without an active lease the guard returns None (call proceeds)."""
    # Default ContextVar is None.
    assert _require_tool_in_lease("subfinder") is None
    assert _require_tool_in_lease("portscan") is None


def test_lease_allows_in_surface_tools(task: Task) -> None:
    lease = _build_lease(task, allowed=["subfinder", "crt_sh"])
    tok = mcp_auth.set_current_lease_for_tests(lease)
    try:
        assert _require_tool_in_lease("subfinder") is None
        assert _require_tool_in_lease("crt_sh") is None
    finally:
        mcp_auth.reset_current_lease_for_tests(tok)


def test_lease_blocks_out_of_surface_tools(task: Task) -> None:
    lease = _build_lease(task, allowed=["subfinder"])
    tok = mcp_auth.set_current_lease_for_tests(lease)
    try:
        result = _require_tool_in_lease("portscan")
        assert result is not None
        assert "error" in result
        assert "portscan" in result["error"]
        assert str(lease.id) in result["error"]
    finally:
        mcp_auth.reset_current_lease_for_tests(tok)


def test_lease_current_resource_returns_curated_context(task: Task) -> None:
    lease = _build_lease(task, allowed=["subfinder", "crt_sh"])
    tok = mcp_auth.set_current_lease_for_tests(lease)
    try:
        payload = json.loads(resource_lease_current())
    finally:
        mcp_auth.reset_current_lease_for_tests(tok)

    assert payload["lease_id"] == str(lease.id)
    assert payload["task_id"] == str(task.id)
    assert payload["engagement_id"] == str(task.engagement_id)
    assert payload["allowed_tools"] == ["subfinder", "crt_sh"]
    assert payload["prompt_keys"] == ["passive_recon"]
    assert payload["context"]["engagement"]["slug"] == "x"


def test_lease_current_resource_raises_without_lease() -> None:
    """No lease → caller should fail loudly so the agent doesn't silently
    operate without context."""
    with pytest.raises(ValueError, match="no active MCP lease"):
        resource_lease_current()
