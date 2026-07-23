"""A3b tests — real InternalExecutor + HTTP surface.

Two layers:

- Executor dispatch — unknown slug degrades to failure; registered tool
  gets called with substituted args; overridable registry for tests.
- Tool functions — the 3 stubs return canned successes; the 2 real tools
  behave sensibly given monkeypatched dnspython / python-whois.
- HTTP layer — POST /engagements/{slug}/playbook-runs happy path,
  GET list + detail, 404s, auth (guest blocked from POST).
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.main import app
from app.models import (
    Engagement,
    EngagementStatus,
    EngagementWorkState,
    PlaybookRun,
    PlaybookRunStatus,
    User,
    UserRole,
)
from app.services import methodology as meth
from app.services.playbook import (
    InternalExecutor,
    StepResult,
    load_seed_playbooks,
)
from app.services.playbook.tools import (
    breach_lookup,
    crtsh,
    dns_inventory,
    subfinder,
    whois,
)

# ---------------------------------------------------------------------------
# Executor dispatch
# ---------------------------------------------------------------------------


def test_executor_unknown_tool_returns_failed_step() -> None:
    ex = InternalExecutor(registry={})
    result = ex.run_step(
        tool_slug="never-heard-of-it",
        args_template={},
        scope_context="foo.com",
    )
    assert result.ok is False
    assert "unknown tool" in (result.error or "")


def test_executor_dispatches_by_slug() -> None:
    calls: list[dict[str, Any]] = []

    def fake_tool(scope: str, args: dict[str, Any]) -> StepResult:
        calls.append({"scope": scope, "args": args})
        return StepResult(ok=True, findings_total=7)

    ex = InternalExecutor(registry={"my-tool": fake_tool})
    result = ex.run_step(
        tool_slug="my-tool",
        args_template={"domain": "{{scope_item}}"},
        scope_context="foo.com",
    )
    assert result.ok is True
    assert result.findings_total == 7
    assert calls == [{"scope": "foo.com", "args": {"domain": "foo.com"}}]


def test_executor_register_replaces_tool() -> None:
    ex = InternalExecutor(registry={"a": lambda *_: StepResult(ok=False, error="old")})
    ex.register("a", lambda *_: StepResult(ok=True, findings_total=1))
    result = ex.run_step(tool_slug="a", args_template={}, scope_context="x")
    assert result.ok is True
    assert result.findings_total == 1


def test_default_executor_covers_all_seed_playbook_slugs() -> None:
    """The default registry must have an entry for every ``tool_slug`` the
    seed playbooks reference — otherwise a fresh install would hit
    ``unknown tool`` failures on its own seeds."""
    seed_slugs = {
        "dns-inventory",
        "whois",
        "subfinder",
        "crtsh",
        "breach-lookup",
    }
    ex = InternalExecutor()
    for slug in seed_slugs:
        assert slug in ex._registry, f"default registry missing {slug!r}"  # noqa: SLF001


# ---------------------------------------------------------------------------
# Stub tools — executable placeholders explicitly marked as non-coverage
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tool_module",
    [subfinder, crtsh, breach_lookup],
    ids=["subfinder", "crtsh", "breach_lookup"],
)
def test_stub_tools_return_success_with_note(tool_module) -> None:
    result = tool_module.run("foo.com", {"domain": "foo.com"})
    assert result.ok is True
    assert result.stub is True
    assert result.findings_total == 0
    # A note explaining the stub is present so the coverage record's audit
    # trail carries provenance without falsely satisfying baseline.
    assert "stub" in (result.data.get("note") or "").lower()


# ---------------------------------------------------------------------------
# Real tools — behavior via monkeypatch (no live network in tests)
# ---------------------------------------------------------------------------


def test_dns_inventory_counts_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    import dns.resolver  # type: ignore[import-untyped]

    class FakeAnswer:
        def __init__(self, text: str) -> None:
            self._text = text

        def to_text(self) -> str:
            return self._text

    class FakeResolver:
        timeout = 3
        lifetime = 5

        def resolve(self, name, qtype):
            table = {
                "A": ["1.2.3.4"],
                "AAAA": ["2001:db8::1"],
                "MX": ["10 mail.foo.com."],
                "TXT": ["v=spf1 -all"],
                "NS": ["ns1.foo.com.", "ns2.foo.com."],
            }
            entries = table.get(qtype, [])
            if not entries:
                raise dns.resolver.NoAnswer()
            return [FakeAnswer(e) for e in entries]

    monkeypatch.setattr(dns.resolver, "Resolver", lambda: FakeResolver())

    result = dns_inventory.run("foo.com", {"domain": "foo.com"})
    assert result.ok is True
    # 1 A + 1 AAAA + 1 MX + 1 TXT + 2 NS = 6.
    assert result.findings_total == 6
    assert result.data["records"]["A"] == ["1.2.3.4"]


def test_dns_inventory_nxdomain_yields_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    import dns.resolver  # type: ignore[import-untyped]

    class NxResolver:
        timeout = 3
        lifetime = 5

        def resolve(self, name, qtype):
            raise dns.resolver.NXDOMAIN()

    monkeypatch.setattr(dns.resolver, "Resolver", lambda: NxResolver())

    result = dns_inventory.run("does-not-exist.example", {"domain": "does-not-exist.example"})
    assert result.ok is False
    assert "NXDOMAIN" in (result.error or "")


def test_whois_counts_populated_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.playbook.tools.whois as whois_tool

    def fake_whois(domain: str) -> dict[str, Any]:
        return {
            "registrar": "Registrar LLC",
            "registrant_organization": "Example Corp",
            "creation_date": "2024-01-01",
            "expiration_date": "2027-01-01",
            "name_servers": ["ns1.foo.com", "ns2.foo.com"],
            "emails": None,  # falsy — not counted
        }

    class FakeMod:
        whois = staticmethod(fake_whois)

    monkeypatch.setattr(whois_tool, "whois", FakeMod, raising=False)
    # The tool imports ``whois`` locally; patch its module namespace.
    import sys

    fake_module = type(sys)("whois")
    fake_module.whois = fake_whois
    monkeypatch.setitem(sys.modules, "whois", fake_module)

    result = whois.run("foo.com", {"domain": "foo.com"})
    assert result.ok is True
    # 5 truthy fields (registrar, registrant_organization, creation_date,
    # expiration_date, name_servers).
    assert result.findings_total == 5
    assert result.data["record"]["registrar"] == "Registrar LLC"


def test_whois_upstream_error_yields_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    def raiser(domain: str):
        raise RuntimeError("upstream borked")

    fake_module = type(sys)("whois")
    fake_module.whois = raiser
    monkeypatch.setitem(sys.modules, "whois", fake_module)

    result = whois.run("foo.com", {"domain": "foo.com"})
    assert result.ok is False
    assert "upstream borked" in (result.error or "")


# ---------------------------------------------------------------------------
# HTTP surface
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(db: Session) -> TestClient:
    return TestClient(app)


@pytest.fixture()
def user(db: Session) -> User:
    u = User(
        id=uuid.uuid4(),
        email=f"tester-{uuid.uuid4().hex[:6]}@example.com",
        display_name="Tester",
        role=UserRole.user,
        is_active=True,
    )
    db.add(u)
    db.commit()
    return u


@pytest.fixture()
def guest(db: Session) -> User:
    u = User(
        id=uuid.uuid4(),
        email=f"guest-{uuid.uuid4().hex[:6]}@example.com",
        display_name="Guest",
        role=UserRole.guest,
        is_active=True,
    )
    db.add(u)
    db.commit()
    return u


@pytest.fixture()
def engagement(db: Session) -> Engagement:
    eng = Engagement(
        name="A3b HTTP",
        slug=f"a3b-{uuid.uuid4().hex[:8]}",
        status=EngagementStatus.active,
        work_state=EngagementWorkState.active,
    )
    db.add(eng)
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


def _headers(user: User) -> dict[str, str]:
    return {"X-User-Id": user.email}


def test_list_playbooks_installs_seeds_on_first_call(
    db: Session, client: TestClient, user: User
) -> None:
    resp = client.get("/playbooks", headers=_headers(user))
    assert resp.status_code == 200
    slugs = {p["slug"] for p in resp.json()}
    assert {"osint-passive-domain", "ptes-passive-recon"} <= slugs


def test_get_playbook_detail_returns_steps(
    db: Session, client: TestClient, user: User
) -> None:
    load_seed_playbooks(db)
    db.commit()
    resp = client.get("/playbooks/osint-passive-domain", headers=_headers(user))
    assert resp.status_code == 200
    body = resp.json()
    step_tools = {s["tool_slug"] for s in body["steps"]}
    assert step_tools == {"subfinder", "dns-inventory", "crtsh", "whois", "breach-lookup"}


def test_get_playbook_unknown_slug_404(
    db: Session, client: TestClient, user: User
) -> None:
    resp = client.get("/playbooks/never", headers=_headers(user))
    assert resp.status_code == 404


def test_create_playbook_run_enqueues_and_returns_202(
    db: Session,
    client: TestClient,
    user: User,
    engagement: Engagement,
) -> None:
    """A3c: POST enqueues; the worker drives to terminal. Endpoint returns
    202 + a pending row."""
    load_seed_playbooks(db)
    db.commit()
    resp = client.post(
        f"/engagements/{engagement.slug}/playbook-runs",
        headers=_headers(user),
        json={
            "playbook_slug": "osint-passive-domain",
            "scope_subset": ["foo.example"],
        },
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["playbook_slug"] == "osint-passive-domain"
    assert body["steps_total"] == 5
    assert body["status"] == PlaybookRunStatus.pending.value
    run = db.execute(
        select(PlaybookRun).where(PlaybookRun.id == uuid.UUID(body["id"]))
    ).scalar_one()
    assert run.engagement_id == engagement.id
    assert run.status is PlaybookRunStatus.pending


def test_create_playbook_run_guest_blocked(
    db: Session, client: TestClient, guest: User, engagement: Engagement
) -> None:
    load_seed_playbooks(db)
    db.commit()
    resp = client.post(
        f"/engagements/{engagement.slug}/playbook-runs",
        headers=_headers(guest),
        json={"playbook_slug": "osint-passive-domain", "scope_subset": ["foo.example"]},
    )
    assert resp.status_code == 403


def test_create_playbook_run_unknown_playbook_404(
    db: Session, client: TestClient, user: User, engagement: Engagement
) -> None:
    resp = client.post(
        f"/engagements/{engagement.slug}/playbook-runs",
        headers=_headers(user),
        json={"playbook_slug": "never", "scope_subset": ["x"]},
    )
    assert resp.status_code == 404


def test_list_and_get_playbook_run_round_trip(
    db: Session, client: TestClient, user: User, engagement: Engagement
) -> None:
    load_seed_playbooks(db)
    db.commit()
    post = client.post(
        f"/engagements/{engagement.slug}/playbook-runs",
        headers=_headers(user),
        json={"playbook_slug": "osint-passive-domain", "scope_subset": ["foo.example"]},
    )
    assert post.status_code == 202
    run_id = post.json()["id"]

    listing = client.get(
        f"/engagements/{engagement.slug}/playbook-runs", headers=_headers(user)
    )
    assert listing.status_code == 200
    assert any(r["id"] == run_id for r in listing.json())

    detail = client.get(f"/playbook-runs/{run_id}", headers=_headers(user))
    assert detail.status_code == 200
    assert detail.json()["id"] == run_id
