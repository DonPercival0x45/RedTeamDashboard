"""A4 tests — MCPExecutor + executor pick wiring.

Layers covered:

- ``_coerce_response`` maps MCP wire shapes to StepResult correctly
  (content-parts, structured, string, error, findings count).
- ``MCPExecutor.run_step`` substitutes scope + dispatches through a mocked
  MCP client, returning canned responses.
- Enqueue stores executor_kind on the row; worker builds the right executor.
- API POST accepts executor='mcp' + persists it; validation rejects unknown.
- Seed playbook ``osint-enrichment`` is present + wired to MCP tool slugs.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.main import app
from app.models import (
    Engagement,
    EngagementArchitecture,
    EngagementStatus,
    EngagementWorkState,
    PlaybookExecutorKind,
    PlaybookRun,
    PlaybookRunStatus,
    ScopeItem,
    ScopeKind,
    User,
    UserRole,
)
from app.services import methodology as meth
from app.services.playbook import (
    catalog,
    enqueue_run,
    load_seed_playbooks,
)
from app.services.playbook.executor import (
    MCPExecutor,
    RoutedExecutor,
    StepResult,
    _coerce_response,
    _unwrap_content_parts,
)
from app.worker.playbook_worker import PlaybookWorkerThread


class _RecordingExecutor:
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[str] = []

    def run_step(self, *, tool_slug, args_template, scope_context):
        self.calls.append(tool_slug)
        return StepResult(ok=True, data={"executor": self.name})


def test_routed_executor_dispatches_each_step_by_server_affinity() -> None:
    internal = _RecordingExecutor("internal")
    mcp = _RecordingExecutor("mcp")
    routed = RoutedExecutor({"internal": internal, "mcp": mcp})

    assert (
        routed.run_step(tool_slug="whois", args_template={}, scope_context="example.com").data[
            "executor"
        ]
        == "internal"
    )
    assert (
        routed.run_step(tool_slug="dns_lookup", args_template={}, scope_context="example.com").data[
            "executor"
        ]
        == "mcp"
    )
    assert internal.calls == ["whois"]
    assert mcp.calls == ["dns_lookup"]


# ---------------------------------------------------------------------------
# _coerce_response
# ---------------------------------------------------------------------------


def test_coerce_response_content_parts_list() -> None:
    """MCP wire returns a list of TextContent-shaped dicts wrapping JSON."""
    raw = [{"type": "text", "text": json.dumps({"country": "US", "asn": 15169})}]
    result = _coerce_response(raw)
    assert result.ok is True
    assert result.data == {"country": "US", "asn": 15169}
    assert result.findings_total == 0  # no _lease_findings


def test_coerce_response_structured_content() -> None:
    """Newer CallToolResult surfaces ``structuredContent`` directly."""

    class Fake:
        structuredContent = {"asn": 42, "org": "Test LLC"}

    result = _coerce_response(Fake())
    assert result.ok is True
    assert result.data == {"asn": 42, "org": "Test LLC"}


def test_coerce_response_error_key_flips_ok_false() -> None:
    raw = [{"type": "text", "text": json.dumps({"error": "quota exceeded"})}]
    result = _coerce_response(raw)
    assert result.ok is False
    assert "quota exceeded" in (result.error or "")


def test_coerce_response_counts_lease_findings() -> None:
    """When a tool wrote findings via _lease_findings, the coerced result
    reports the count so the collection.job.completed milestone gets a
    non-zero FindingsSummary."""
    payload = {
        "domain": "foo.com",
        "_lease_findings": [
            {"kind": "subdomain", "value": "a.foo.com"},
            {"kind": "subdomain", "value": "b.foo.com"},
            {"kind": "subdomain", "value": "c.foo.com"},
        ],
    }
    raw = [{"type": "text", "text": json.dumps(payload)}]
    result = _coerce_response(raw)
    assert result.ok is True
    assert result.findings_total == 3
    assert result.findings_new == 3
    # ``_lease_findings`` is stripped from ``data``.
    assert "_lease_findings" not in result.data
    assert result.data["domain"] == "foo.com"


def test_coerce_response_string_that_is_not_json() -> None:
    result = _coerce_response("plain text response")
    assert result.ok is True
    assert result.data == {"raw": "plain text response"}


def test_unwrap_content_parts_bare_list_no_text() -> None:
    """Empty / non-text parts don't crash — pass-through."""
    assert _unwrap_content_parts([]) == []
    assert _unwrap_content_parts([{"type": "image", "data": "..."}]) == [
        {"type": "image", "data": "..."}
    ]


# ---------------------------------------------------------------------------
# MCPExecutor.run_step — mocked MCP client
# ---------------------------------------------------------------------------


class _FakeMCPTool:
    """Minimal async ``ainvoke`` shim matching langchain_mcp_adapters BaseTool."""

    def __init__(self, name: str, response) -> None:
        self.name = name
        self._response = response
        self.calls: list[dict] = []

    async def ainvoke(self, args):
        self.calls.append(dict(args))
        return self._response


class _FakeMCPClient:
    def __init__(self, tools: list[_FakeMCPTool]) -> None:
        self._tools = tools

    async def get_tools(self):
        return self._tools


def _prime_executor(ex: MCPExecutor, tools: list[_FakeMCPTool]) -> None:
    """Bypass ``_get_client`` by pre-populating the tool cache."""
    ex._tool_cache = {t.name: t for t in tools}  # noqa: SLF001


def test_mcp_executor_binds_validated_scope_and_returns_result() -> None:
    tool = _FakeMCPTool(
        "freeipapi",
        [{"type": "text", "text": json.dumps({"country": "US", "asn": 15169})}],
    )
    ex = MCPExecutor(base_url="http://x/mcp/sse", api_key="tk")
    _prime_executor(ex, [tool])
    result = ex.run_step(
        tool_slug="freeipapi",
        args_template={"ip": "203.0.113.99"},
        scope_context="1.2.3.4",
    )
    assert result.ok is True
    assert result.data == {"country": "US", "asn": 15169}
    assert tool.calls == [{"ip": "1.2.3.4"}]


def test_mcp_executor_maps_catalog_alias_and_binds_scope() -> None:
    tool = _FakeMCPTool(
        "port_scan",
        [{"type": "text", "text": json.dumps({"open_ports": [443]})}],
    )
    ex = MCPExecutor(base_url="http://x/mcp/sse", api_key="tk")
    _prime_executor(ex, [tool])

    result = ex.run_step(
        tool_slug="mcp_port_scan",
        args_template={"target": "198.51.100.9", "ports": "80,443"},
        scope_context="203.0.113.7",
    )

    assert result.ok is True
    assert tool.calls == [{"target": "203.0.113.7", "ports": "80,443"}]


def test_mcp_executor_unknown_tool_yields_failure() -> None:
    """A tool_slug the server doesn't expose becomes a step failure. Prime
    the cache with any other tool so ``_load_tools`` doesn't try the real
    client (empty cache short-circuits the reload)."""
    other = _FakeMCPTool("something-else", [{"type": "text", "text": "{}"}])
    ex = MCPExecutor(base_url="http://x/mcp/sse", api_key="tk")
    _prime_executor(ex, [other])
    result = ex.run_step(
        tool_slug="never-heard-of-it",
        args_template={},
        scope_context="foo",
    )
    assert result.ok is False
    assert "does not expose" in (result.error or "")


def test_mcp_executor_transport_error_yields_failure() -> None:
    class Boom(_FakeMCPTool):
        async def ainvoke(self, args):
            raise ConnectionError("connection refused")

    tool = Boom("freeipapi", None)
    ex = MCPExecutor(base_url="http://x/mcp/sse", api_key="tk")
    _prime_executor(ex, [tool])
    result = ex.run_step(
        tool_slug="freeipapi",
        args_template={"ip": "1.2.3.4"},
        scope_context="1.2.3.4",
    )
    assert result.ok is False
    assert "connection refused" in (result.error or "")
    assert "ConnectionError" in (result.error or "")


# ---------------------------------------------------------------------------
# Executor pick wired through runner + worker
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _cleanup_queue():
    s = SessionLocal()
    try:
        s.execute(delete(PlaybookRun))
        s.commit()
    finally:
        s.close()
    yield


@pytest.fixture()
def engagement(db: Session) -> Engagement:
    eng = Engagement(
        name="A4 Test",
        slug=f"a4-{uuid.uuid4().hex[:8]}",
        status=EngagementStatus.active,
        work_state=EngagementWorkState.active,
        intelligence_architecture=EngagementArchitecture.v3,
    )
    db.add(eng)
    db.flush()
    # POST /playbook-runs now enforces in-scope-only; seed the IP the HTTP
    # tests submit so they exercise the happy path.
    db.add(ScopeItem(engagement_id=eng.id, kind=ScopeKind.ip, value="1.2.3.4"))
    db.flush()
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
def enrichment_playbook(db: Session):
    load_seed_playbooks(db)
    db.commit()
    pb = catalog.get_by_slug(db, "osint-enrichment")
    assert pb is not None
    return pb


def test_enqueue_run_stores_executor_kind(
    db: Session, engagement: Engagement, enrichment_playbook
) -> None:
    run = enqueue_run(
        db,
        engagement=engagement,
        playbook=enrichment_playbook,
        scope_subset=["1.2.3.4"],
        executor_kind=PlaybookExecutorKind.mcp,
    )
    db.flush()
    assert run.executor_kind is PlaybookExecutorKind.mcp


def test_enqueue_run_defaults_to_internal(
    db: Session, engagement: Engagement, enrichment_playbook
) -> None:
    run = enqueue_run(
        db,
        engagement=engagement,
        playbook=enrichment_playbook,
        scope_subset=["1.2.3.4"],
    )
    db.flush()
    assert run.executor_kind is PlaybookExecutorKind.internal


def test_worker_builds_mcp_executor_when_kind_is_mcp(
    db: Session, engagement: Engagement, enrichment_playbook
) -> None:
    """The worker inspects run.executor_kind and instantiates the right
    executor. We don't call the MCP server here — just prove the class."""
    enqueue_run(
        db,
        engagement=engagement,
        playbook=enrichment_playbook,
        scope_subset=["1.2.3.4"],
        executor_kind=PlaybookExecutorKind.mcp,
    )
    db.commit()
    worker = PlaybookWorkerThread(session_factory=SessionLocal)
    built = worker._build_executor(PlaybookExecutorKind.mcp)  # noqa: SLF001
    assert isinstance(built, MCPExecutor)
    built_internal = worker._build_executor(  # noqa: SLF001
        PlaybookExecutorKind.internal
    )
    from app.services.playbook import InternalExecutor as _Internal

    assert isinstance(built_internal, _Internal)


# ---------------------------------------------------------------------------
# HTTP surface — POST accepts executor field
# ---------------------------------------------------------------------------


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def user(db: Session) -> User:
    u = User(
        id=uuid.uuid4(),
        email=f"a4-{uuid.uuid4().hex[:6]}@example.com",
        display_name="A4 Tester",
        role=UserRole.user,
        is_active=True,
    )
    db.add(u)
    db.commit()
    return u


def _headers(u: User) -> dict[str, str]:
    return {"X-User-Id": u.email}


def _post_reviewed_run(
    client: TestClient,
    *,
    engagement_slug: str,
    user: User,
    payload: dict,
):
    plan = client.post(
        f"/engagements/{engagement_slug}/playbook-runs/plan",
        headers=_headers(user),
        json=payload,
    )
    assert plan.status_code == 200, plan.text
    return client.post(
        f"/engagements/{engagement_slug}/playbook-runs",
        headers=_headers(user),
        json={**payload, "plan_sha256": plan.json()["plan_sha256"]},
    )


def test_catalog_declares_server_owned_executor_affinity(
    client: TestClient,
    user: User,
    enrichment_playbook,
) -> None:
    response = client.get("/playbooks", headers=_headers(user))

    assert response.status_code == 200
    enrichment = next(item for item in response.json() if item["slug"] == "osint-enrichment")
    assert enrichment["required_executor"] == "mcp"
    assert enrichment["required_credentials"] == ["freeipapi", "ipinfo"]


def test_post_accepts_executor_mcp_and_persists_it(
    db: Session,
    client: TestClient,
    user: User,
    engagement: Engagement,
    enrichment_playbook,
) -> None:
    resp = _post_reviewed_run(
        client,
        engagement_slug=engagement.slug,
        user=user,
        payload={
            "playbook_slug": "osint-enrichment",
            "scope_subset": ["1.2.3.4"],
            "executor": "mcp",
        },
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["executor"] == "mcp"
    assert body["status"] == PlaybookRunStatus.pending.value


def test_post_selects_catalog_required_executor_when_omitted(
    db: Session,
    client: TestClient,
    user: User,
    engagement: Engagement,
    enrichment_playbook,
) -> None:
    resp = _post_reviewed_run(
        client,
        engagement_slug=engagement.slug,
        user=user,
        payload={
            "playbook_slug": "osint-enrichment",
            "scope_subset": ["1.2.3.4"],
        },
    )
    assert resp.status_code == 202
    assert resp.json()["executor"] == "mcp"


def test_post_rejects_incompatible_executor_before_queueing(
    client: TestClient,
    user: User,
    engagement: Engagement,
    enrichment_playbook,
) -> None:
    response = client.post(
        f"/engagements/{engagement.slug}/playbook-runs",
        headers=_headers(user),
        json={
            "playbook_slug": "osint-enrichment",
            "scope_subset": ["1.2.3.4"],
            "executor": "internal",
        },
    )

    assert response.status_code == 422
    assert "requires executor 'mcp'" in response.json()["detail"]


def test_post_rejects_unknown_executor_422(
    db: Session,
    client: TestClient,
    user: User,
    engagement: Engagement,
    enrichment_playbook,
) -> None:
    resp = client.post(
        f"/engagements/{engagement.slug}/playbook-runs",
        headers=_headers(user),
        json={
            "playbook_slug": "osint-enrichment",
            "scope_subset": ["1.2.3.4"],
            "executor": "carrier-pigeon",
        },
    )
    assert resp.status_code == 422
    assert "internal" in resp.json()["detail"]
    assert "mcp" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Seed playbook — osint-enrichment
# ---------------------------------------------------------------------------


def test_osint_enrichment_seed_is_installed(db: Session, client: TestClient, user: User) -> None:
    resp = client.get("/playbooks", headers=_headers(user))
    slugs = {p["slug"] for p in resp.json()}
    assert "osint-enrichment" in slugs


def test_osint_enrichment_targets_ip_asset_class(db: Session, enrichment_playbook) -> None:
    assert enrichment_playbook.applies_to_asset_class == "ip"
    tools = {s.tool_slug for s in enrichment_playbook.steps}
    assert tools == {"mcp_reverse_dns", "freeipapi", "ipinfo"}


def test_step_result_is_frozen() -> None:
    from dataclasses import FrozenInstanceError

    r = StepResult(ok=True)
    with pytest.raises(FrozenInstanceError):
        r.ok = False  # type: ignore[misc]
