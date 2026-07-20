"""Engagements + scope + runs HTTP API.

Tests use the live compose Postgres + Redis. Each test that creates
engagements via the API registers their slugs with a teardown fixture so the
``flush_engagement`` DB helper can clean them up afterwards.
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
import redis as redis_lib
from fastapi.testclient import TestClient
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.main import app
from app.models import AuditLog, Engagement, Finding, ScopeItem, Severity
from app.runs.streams import inbound_stream, outbound_stream

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def redis_client() -> Iterator[redis_lib.Redis]:
    r = redis_lib.Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        yield r
    finally:
        r.close()


@pytest.fixture()
def cleanup_slugs(db: Session, redis_client: redis_lib.Redis) -> Iterator[list[str]]:
    """Tests append slugs they create; teardown flushes each engagement."""
    slugs: list[str] = []
    yield slugs
    for slug in slugs:
        eng_id = db.execute(
            select(Engagement.id).where(Engagement.slug == slug)
        ).scalar_one_or_none()
        if eng_id is None:
            continue
        db.execute(
            text("DELETE FROM approvals WHERE engagement_id = :id"),
            {"id": eng_id},
        )
        db.commit()
        db.execute(text("SELECT flush_engagement(:id)"), {"id": eng_id})
        db.commit()
        redis_client.delete(inbound_stream(eng_id), outbound_stream(eng_id))


def _headers() -> dict[str, str]:
    return {"X-User-Id": "engagement-test@example.com"}


def _create(client: TestClient, name: str, slug: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"name": name}
    if slug is not None:
        body["slug"] = slug
    response = client.post("/engagements", json=body, headers=_headers())
    assert response.status_code == 201, response.text
    return response.json()


def _seed_provider_key(client: TestClient, provider: str = "ollama") -> None:
    """Ensure the test user has a BYO provider key for the chosen provider.

    The ephemeral-keys model makes ``start_run`` require a cached BYO key
    for the acting user. Run tests need one seeded before they can enqueue
    a run.start. Ollama (keyless local) is the cheapest seed.
    """
    body = {
        "name": f"test-{provider}",
        "provider": provider,
        "kind": "model_provider",
        "is_local": True,
        "endpoint": "http://localhost:11434",
        "models": ["llama3.1:8b"],
    }
    res = client.post("/me/provider-keys", json=body, headers=_headers())
    # 201 on first call, 409 if a previous test already seeded — both fine.
    assert res.status_code in (201, 409), res.text


# ---------------------------------------------------------------------------
# Engagement CRUD
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/engagements",
        "/engagements/auth-boundary-missing",
        "/engagements/auth-boundary-missing/scope",
        "/engagements/auth-boundary-missing/findings",
        "/engagements/auth-boundary-missing/entities",
        "/engagements/auth-boundary-missing/observations",
        f"/findings/{uuid.UUID(int=0)}/observations",
        f"/engagements/{uuid.UUID(int=0)}/authorizations",
    ],
)
def test_sensitive_engagement_reads_require_auth(
    client: TestClient, path: str
) -> None:
    response = client.get(path)
    assert response.status_code == 401, (path, response.text)


def test_create_with_auto_generated_slug(
    client: TestClient, cleanup_slugs: list[str]
) -> None:
    # Use a unique name so a left-behind row from a previous run can't force
    # the unique-slug suffix path and trip the equality assertion.
    name = f"Auto Slug {uuid.uuid4().hex[:6]}"
    body = _create(client, name)
    cleanup_slugs.append(body["slug"])
    expected = name.lower().replace(" ", "-")
    assert body["slug"] == expected
    assert body["name"] == name
    assert body["status"] == "active"
    assert body["created_by"] is not None


def test_create_with_initial_scope_is_atomic_and_audited(
    client: TestClient,
    db: Session,
    cleanup_slugs: list[str],
) -> None:
    name = f"Scoped setup {uuid.uuid4().hex[:6]}"
    response = client.post(
        "/engagements",
        json={
            "name": name,
            "initial_scope": [
                {"kind": "domain", "value": "example.test"},
                {
                    "kind": "domain",
                    "value": "admin.example.test",
                    "is_exclusion": True,
                    "note": "Client exclusion",
                },
            ],
        },
        headers=_headers(),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    cleanup_slugs.append(body["slug"])
    assert body["scope_count"] == 1
    assert body["exclusion_count"] == 1

    engagement = db.execute(
        select(Engagement).where(Engagement.slug == body["slug"])
    ).scalar_one()
    scope = list(
        db.execute(
            select(ScopeItem).where(ScopeItem.engagement_id == engagement.id)
        ).scalars()
    )
    assert {(row.value, row.is_exclusion) for row in scope} == {
        ("example.test", False),
        ("admin.example.test", True),
    }
    assert all(row.source == "defined" for row in scope)
    audit = db.execute(
        select(AuditLog).where(
            AuditLog.engagement_id == engagement.id,
            AuditLog.event_type == "engagement.created",
        )
    ).scalar_one()
    assert audit.payload["initial_scope_count"] == 2
    assert audit.payload["include_count"] == 1
    assert audit.payload["exclusion_count"] == 1


def test_duplicate_initial_scope_rejects_entire_setup(
    client: TestClient, db: Session
) -> None:
    name = f"Duplicate setup {uuid.uuid4().hex[:6]}"
    response = client.post(
        "/engagements",
        json={
            "name": name,
            "initial_scope": [
                {"kind": "domain", "value": "example.test"},
                {"kind": "domain", "value": "example.test"},
            ],
        },
        headers=_headers(),
    )
    assert response.status_code == 422, response.text
    assert "domain:example.test" in response.text
    assert db.execute(
        select(Engagement.id).where(Engagement.name == name)
    ).scalar_one_or_none() is None


def test_concurrent_slug_conflict_returns_409_without_partial_setup(
    client: TestClient,
    db: Session,
    cleanup_slugs: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slug = f"slug-race-{uuid.uuid4().hex[:6]}"
    existing = _create(client, "Existing slug owner", slug=slug)
    cleanup_slugs.append(existing["slug"])

    from app.api import engagements as engagements_api

    monkeypatch.setattr(
        engagements_api,
        "_unique_slug",
        lambda _session, _base_slug: slug,
    )
    response = client.post(
        "/engagements",
        json={
            "name": "Concurrent loser",
            "initial_scope": [{"kind": "domain", "value": "loser.test"}],
        },
        headers=_headers(),
    )

    assert response.status_code == 409, response.text
    assert "claimed concurrently" in response.text
    assert db.execute(
        select(Engagement.id).where(Engagement.name == "Concurrent loser")
    ).scalar_one_or_none() is None
    owner = db.execute(
        select(Engagement).where(Engagement.slug == slug)
    ).scalar_one()
    assert db.execute(
        select(ScopeItem.id).where(ScopeItem.engagement_id == owner.id)
    ).scalar_one_or_none() is None


def test_include_and_exclusion_for_same_initial_value_are_distinct(
    client: TestClient, db: Session, cleanup_slugs: list[str]
) -> None:
    name = f"Carved setup {uuid.uuid4().hex[:6]}"
    response = client.post(
        "/engagements",
        json={
            "name": name,
            "initial_scope": [
                {"kind": "domain", "value": "example.test"},
                {
                    "kind": "domain",
                    "value": "example.test",
                    "is_exclusion": True,
                },
            ],
        },
        headers=_headers(),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    cleanup_slugs.append(body["slug"])
    assert body["scope_count"] == 1
    assert body["exclusion_count"] == 1
    engagement = db.execute(
        select(Engagement).where(Engagement.slug == body["slug"])
    ).scalar_one()
    rows = list(
        db.execute(
            select(ScopeItem).where(ScopeItem.engagement_id == engagement.id)
        ).scalars()
    )
    assert {row.is_exclusion for row in rows} == {False, True}


def test_blank_initial_scope_rejects_entire_setup(
    client: TestClient, db: Session
) -> None:
    name = f"Blank setup {uuid.uuid4().hex[:6]}"
    response = client.post(
        "/engagements",
        json={
            "name": name,
            "initial_scope": [{"kind": "domain", "value": "   "}],
        },
        headers=_headers(),
    )
    assert response.status_code == 422, response.text
    assert "initial scope value cannot be blank" in response.text
    assert db.execute(
        select(Engagement.id).where(Engagement.name == name)
    ).scalar_one_or_none() is None


def test_scope_insert_failure_rolls_back_engagement_and_audit(
    client: TestClient, db: Session
) -> None:
    name = f"Rollback setup {uuid.uuid4().hex[:6]}"

    def fail_scope_insert(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("injected scope insert failure")

    event.listen(ScopeItem, "before_insert", fail_scope_insert)
    try:
        with pytest.raises(RuntimeError, match="injected scope insert failure"):
            client.post(
                "/engagements",
                json={
                    "name": name,
                    "initial_scope": [
                        {"kind": "domain", "value": "example.test"},
                    ],
                },
                headers=_headers(),
            )
    finally:
        event.remove(ScopeItem, "before_insert", fail_scope_insert)

    assert db.execute(
        select(Engagement.id).where(Engagement.name == name)
    ).scalar_one_or_none() is None
    assert db.execute(
        select(AuditLog.id).where(
            AuditLog.event_type == "engagement.created",
            AuditLog.payload["name"].astext == name,
        )
    ).scalar_one_or_none() is None


def test_create_with_description_round_trips(
    client: TestClient, cleanup_slugs: list[str]
) -> None:
    name = f"Described {uuid.uuid4().hex[:6]}"
    desc = "Rules of engagement: passive OSINT first; no active without approval."
    response = client.post(
        "/engagements",
        json={"name": name, "description": desc},
        headers=_headers(),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    cleanup_slugs.append(body["slug"])
    assert body["description"] == desc

    # And it comes back on read.
    read = client.get(f"/engagements/{body['slug']}", headers=_headers()).json()
    assert read["description"] == desc


def test_create_without_description_is_null(
    client: TestClient, cleanup_slugs: list[str]
) -> None:
    body = _create(client, f"NoDesc {uuid.uuid4().hex[:6]}")
    cleanup_slugs.append(body["slug"])
    assert body["description"] is None


def test_create_with_explicit_slug(
    client: TestClient, cleanup_slugs: list[str]
) -> None:
    slug = f"acme-explicit-{uuid.uuid4().hex[:6]}"
    body = _create(client, "Acme Explicit", slug=slug)
    cleanup_slugs.append(body["slug"])
    assert body["slug"] == slug


def test_create_with_conflicting_slug_appends_suffix(
    client: TestClient, cleanup_slugs: list[str]
) -> None:
    base = f"acme-collide-{uuid.uuid4().hex[:6]}"
    first = _create(client, "Acme", slug=base)
    second = _create(client, "Acme Two", slug=base)
    cleanup_slugs.extend([first["slug"], second["slug"]])
    assert first["slug"] == base
    assert second["slug"].startswith(base + "-")
    assert second["slug"] != base


def test_requires_x_user_id_header(client: TestClient) -> None:
    response = client.post("/engagements", json={"name": "no auth"})
    assert response.status_code == 401


def test_list_engagements_filters_by_status(
    client: TestClient, cleanup_slugs: list[str]
) -> None:
    active = _create(client, f"List active {uuid.uuid4().hex[:6]}")
    archived = _create(client, f"List archived {uuid.uuid4().hex[:6]}")
    cleanup_slugs.extend([active["slug"], archived["slug"]])

    client.delete(f"/engagements/{archived['slug']}", headers=_headers())

    response = client.get(
        "/engagements", params={"status": "active"}, headers=_headers()
    )
    assert response.status_code == 200
    slugs = {e["slug"] for e in response.json()}
    assert active["slug"] in slugs
    assert archived["slug"] not in slugs


def test_get_engagement_404_for_unknown_slug(client: TestClient) -> None:
    response = client.get(
        f"/engagements/does-not-exist-{uuid.uuid4().hex[:6]}", headers=_headers()
    )
    assert response.status_code == 404


def test_patch_renames_engagement(
    client: TestClient, cleanup_slugs: list[str]
) -> None:
    eng = _create(client, "Original")
    cleanup_slugs.append(eng["slug"])

    response = client.patch(
        f"/engagements/{eng['slug']}",
        json={"name": "Renamed"},
        headers=_headers(),
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Renamed"


def test_patch_archive_then_unarchive_stamps_and_clears_archived_at(
    client: TestClient, cleanup_slugs: list[str]
) -> None:
    eng = _create(client, "Cycle")
    cleanup_slugs.append(eng["slug"])

    archived = client.patch(
        f"/engagements/{eng['slug']}",
        json={"status": "archived"},
        headers=_headers(),
    ).json()
    assert archived["status"] == "archived"
    assert archived["archived_at"] is not None

    unarchived = client.patch(
        f"/engagements/{eng['slug']}",
        json={"status": "active"},
        headers=_headers(),
    ).json()
    assert unarchived["status"] == "active"
    assert unarchived["archived_at"] is None


def test_patch_to_flushed_status_is_rejected(
    client: TestClient, cleanup_slugs: list[str]
) -> None:
    eng = _create(client, "No direct flush")
    cleanup_slugs.append(eng["slug"])

    response = client.patch(
        f"/engagements/{eng['slug']}",
        json={"status": "flushed"},
        headers=_headers(),
    )
    assert response.status_code == 400


def test_delete_soft_archives(
    client: TestClient, cleanup_slugs: list[str]
) -> None:
    eng = _create(client, "Soft archive me")
    cleanup_slugs.append(eng["slug"])

    response = client.delete(f"/engagements/{eng['slug']}", headers=_headers())
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "archived"
    assert body["archived_at"] is not None

    # Row still fetchable
    again = client.get(f"/engagements/{eng['slug']}", headers=_headers())
    assert again.status_code == 200


def test_flush_removes_engagement_and_streams(
    client: TestClient,
    db: Session,
    redis_client: redis_lib.Redis,
) -> None:
    eng = _create(client, f"Flush me {uuid.uuid4().hex[:6]}")
    # Don't add to cleanup_slugs — we're flushing manually below.

    # Flush is admin-only as of v0.5.0 (hard delete = backend change). Promote
    # the test user so the call lands at the route rather than at the gate.
    from app.models import User

    test_user = db.execute(
        select(User).where(User.email == "engagement-test@example.com")
    ).scalar_one_or_none()
    if test_user is not None:
        from app.models import UserRole

        test_user.role = UserRole.admin
        db.commit()

    # Seed an inbound stream message so we can confirm the redis cleanup.
    redis_client.xadd(
        inbound_stream(uuid.UUID(eng["id"])),
        {"data": "{}"},
    )
    assert redis_client.exists(inbound_stream(uuid.UUID(eng["id"]))) == 1

    response = client.post(
        f"/engagements/{eng['slug']}/flush", headers=_headers()
    )
    assert response.status_code == 204

    # Engagement row is gone.
    gone = db.execute(
        select(Engagement.id).where(Engagement.slug == eng["slug"])
    ).scalar_one_or_none()
    assert gone is None

    # Stream is gone.
    assert redis_client.exists(inbound_stream(uuid.UUID(eng["id"]))) == 0


# ---------------------------------------------------------------------------
# Scope CRUD
# ---------------------------------------------------------------------------


def test_create_and_list_scope_items(
    client: TestClient, cleanup_slugs: list[str]
) -> None:
    eng = _create(client, "Scope holder")
    cleanup_slugs.append(eng["slug"])

    a = client.post(
        f"/engagements/{eng['slug']}/scope",
        json={"kind": "domain", "value": "acme.com"},
        headers=_headers(),
    )
    assert a.status_code == 201, a.text
    b = client.post(
        f"/engagements/{eng['slug']}/scope",
        json={
            "kind": "cidr",
            "value": "10.0.0.0/24",
            "is_exclusion": False,
            "note": "internal range",
        },
        headers=_headers(),
    )
    assert b.status_code == 201

    listing = client.get(f"/engagements/{eng['slug']}/scope", headers=_headers())
    assert listing.status_code == 200
    rows = listing.json()
    assert len(rows) == 2
    values = {r["value"] for r in rows}
    assert values == {"acme.com", "10.0.0.0/24"}
    # v1.4.13: items created without a source default to "defined".
    assert {r["source"] for r in rows} == {"defined"}


def test_scope_item_source_found_round_trips(
    client: TestClient, cleanup_slugs: list[str]
) -> None:
    """v1.4.13 (roadmap #5): a scope item can be marked source='found'
    (added from findings) and the marker survives a read."""
    eng = _create(client, "Found scope holder")
    cleanup_slugs.append(eng["slug"])

    created = client.post(
        f"/engagements/{eng['slug']}/scope",
        json={"kind": "domain", "value": "shadow.acme.com", "source": "found"},
        headers=_headers(),
    )
    assert created.status_code == 201, created.text
    assert created.json()["source"] == "found"

    rows = client.get(
        f"/engagements/{eng['slug']}/scope", headers=_headers()
    ).json()
    found_rows = [r for r in rows if r["source"] == "found"]
    assert len(found_rows) == 1
    assert found_rows[0]["value"] == "shadow.acme.com"

    # An invalid source is rejected by the Literal validator.
    bad = client.post(
        f"/engagements/{eng['slug']}/scope",
        json={"kind": "domain", "value": "x.io", "source": "bogus"},
        headers=_headers(),
    )
    assert bad.status_code == 422


def test_engagement_read_carries_scope_counts(
    client: TestClient, cleanup_slugs: list[str]
) -> None:
    """v1.4.5: ``scope_count`` / ``exclusion_count`` ride on the engagement
    read + list + patch shapes so list cards can render the counts."""
    eng = _create(client, "Counted")
    cleanup_slugs.append(eng["slug"])

    # Empty engagement -> both counts zero.
    fresh = client.get(f"/engagements/{eng['slug']}", headers=_headers())
    assert fresh.status_code == 200
    assert fresh.json()["scope_count"] == 0
    assert fresh.json()["exclusion_count"] == 0

    for value, excl in [
        ("acme.com", False),
        ("10.0.0.0/24", False),
        ("legacy.acme.com", True),  # exclusion
    ]:
        r = client.post(
            f"/engagements/{eng['slug']}/scope",
            json={"kind": "domain", "value": value, "is_exclusion": excl},
            headers=_headers(),
        )
        assert r.status_code == 201, r.text

    # GET one engagement -> 2 in scope, 1 exclusion.
    got = client.get(f"/engagements/{eng['slug']}", headers=_headers()).json()
    assert got["scope_count"] == 2
    assert got["exclusion_count"] == 1

    # LIST carries the counts too (no extra N+1).
    listed = client.get("/engagements", headers=_headers()).json()
    this = next(e for e in listed if e["slug"] == eng["slug"])
    assert this["scope_count"] == 2
    assert this["exclusion_count"] == 1

    # PATCH echoes the same counts (rename shouldn't change scope).
    patched = client.patch(
        f"/engagements/{eng['slug']}",
        json={"name": "Counted renamed"},
        headers=_headers(),
    ).json()
    assert patched["scope_count"] == 2
    assert patched["exclusion_count"] == 1

    # Newly created engagement always reports zeros.
    new = _create(client, "Untouched")
    cleanup_slugs.append(new["slug"])
    created = client.post(
        "/engagements",
        json={"name": "Also untouched"},
        headers=_headers(),
    )
    assert created.status_code == 201
    cleanup_slugs.append(created.json()["slug"])
    assert created.json()["scope_count"] == 0


def test_update_scope_item(
    client: TestClient, db: Session, cleanup_slugs: list[str]
) -> None:
    eng = _create(client, "Scope edit")
    cleanup_slugs.append(eng["slug"])
    created = client.post(
        f"/engagements/{eng['slug']}/scope",
        json={"kind": "domain", "value": "acme.com"},
        headers=_headers(),
    ).json()

    response = client.patch(
        f"/engagements/{eng['slug']}/scope/{created['id']}",
        json={"value": "acme.org", "note": "renamed"},
        headers=_headers(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["value"] == "acme.org"
    assert body["note"] == "renamed"


def test_delete_scope_item(
    client: TestClient, cleanup_slugs: list[str]
) -> None:
    eng = _create(client, "Scope delete")
    cleanup_slugs.append(eng["slug"])
    created = client.post(
        f"/engagements/{eng['slug']}/scope",
        json={"kind": "domain", "value": "doomed.example.com"},
        headers=_headers(),
    ).json()

    response = client.delete(
        f"/engagements/{eng['slug']}/scope/{created['id']}",
        headers=_headers(),
    )
    assert response.status_code == 204

    listing = client.get(
        f"/engagements/{eng['slug']}/scope", headers=_headers()
    ).json()
    assert listing == []


def test_scope_404_when_id_belongs_to_other_engagement(
    client: TestClient, cleanup_slugs: list[str]
) -> None:
    a = _create(client, f"A {uuid.uuid4().hex[:6]}")
    b = _create(client, f"B {uuid.uuid4().hex[:6]}")
    cleanup_slugs.extend([a["slug"], b["slug"]])

    item_a = client.post(
        f"/engagements/{a['slug']}/scope",
        json={"kind": "domain", "value": "a.com"},
        headers=_headers(),
    ).json()

    # Try to update under engagement b's slug — must 404.
    response = client.patch(
        f"/engagements/{b['slug']}/scope/{item_a['id']}",
        json={"value": "leaked.com"},
        headers=_headers(),
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------


def test_list_findings_unpacks_persisted_rows(
    client: TestClient, db: Session, cleanup_slugs: list[str]
) -> None:
    eng = _create(client, f"Findings holder {uuid.uuid4().hex[:6]}")
    cleanup_slugs.append(eng["slug"])

    # Mirror what the worker's _persist_finding writes: tool data flattened into
    # details alongside the {thread_id, args} envelope.
    db.add(
        Finding(
            engagement_id=uuid.UUID(eng["id"]),
            title="dns_lookup → acme.com",
            severity=Severity.info,
            source_tool="dns_lookup",
            target="acme.com",
            details={
                "thread_id": "t-1",
                "args": {"domain": "acme.com"},
                "a": ["1.2.3.4"],
            },
        )
    )
    db.commit()

    response = client.get(
        f"/engagements/{eng['slug']}/findings", headers=_headers()
    )
    assert response.status_code == 200, response.text
    rows = response.json()
    assert len(rows) == 1
    row = rows[0]
    assert row["tool"] == "dns_lookup"
    assert row["target"] == "acme.com"
    assert row["thread_id"] == "t-1"
    assert row["args"] == {"domain": "acme.com"}
    # data is the details remainder after the envelope keys are popped.
    assert row["data"] == {"a": ["1.2.3.4"]}
    assert row["severity"] == "info"


def test_list_findings_empty_for_new_engagement(
    client: TestClient, cleanup_slugs: list[str]
) -> None:
    eng = _create(client, f"No findings {uuid.uuid4().hex[:6]}")
    cleanup_slugs.append(eng["slug"])
    response = client.get(
        f"/engagements/{eng['slug']}/findings", headers=_headers()
    )
    assert response.status_code == 200
    assert response.json() == []


def _create_finding(
    client: TestClient, slug: str, title: str = "t", **fields: Any
) -> dict[str, Any]:
    body: dict[str, Any] = {"title": title}
    body.update(fields)
    r = client.post(f"/engagements/{slug}/findings", json=body, headers=_headers())
    assert r.status_code == 201, r.text
    return r.json()


def test_finding_tags_round_trip(client: TestClient, cleanup_slugs: list[str]) -> None:
    """Allowed tags survive create/read/patch/clear; overlong tags are rejected."""
    eng = _create(client, f"Tags holder {uuid.uuid4().hex[:6]}")
    cleanup_slugs.append(eng["slug"])

    # Valid tags are trimmed and de-duplicated without truncating their content.
    created = _create_finding(
        client,
        eng["slug"],
        title="tagged",
        tags=[" xss ", "xss", "recon", "z" * 40],
    )
    assert created["tags"] == ["xss", "recon", "z" * 40]

    listed = client.get(
        f"/engagements/{eng['slug']}/findings", headers=_headers()
    ).json()
    assert listed[0]["tags"] == ["xss", "recon", "z" * 40]

    fid = created["id"]
    patched_response = client.patch(
        f"/findings/{fid}",
        json={"tags": ["xss", "cred-leak"]},
        headers=_headers(),
    )
    assert patched_response.status_code == 200, patched_response.text
    assert patched_response.json()["tags"] == ["xss", "cred-leak"]

    overlong_patch = client.patch(
        f"/findings/{fid}",
        json={"tags": ["z" * 41]},
        headers=_headers(),
    )
    assert overlong_patch.status_code == 422, overlong_patch.text

    cleared_response = client.patch(
        f"/findings/{fid}",
        json={"tags": []},
        headers=_headers(),
    )
    assert cleared_response.status_code == 200, cleared_response.text
    assert cleared_response.json()["tags"] == []

    overlong_create = client.post(
        f"/engagements/{eng['slug']}/findings",
        json={"title": "overlong tag", "tags": ["z" * 41]},
        headers=_headers(),
    )
    assert overlong_create.status_code == 422, overlong_create.text

    bare = _create_finding(client, eng["slug"], title="no tags")
    assert bare["tags"] == []


def test_finding_tags_cap_at_twenty(
    client: TestClient, cleanup_slugs: list[str]
) -> None:
    """Exactly 20 tags are accepted; a 21st is rejected rather than dropped."""
    eng = _create(client, f"Cap holder {uuid.uuid4().hex[:6]}")
    cleanup_slugs.append(eng["slug"])

    exact = _create_finding(
        client, eng["slug"], tags=[f"tag-{i}" for i in range(20)]
    )
    assert exact["tags"] == [f"tag-{i}" for i in range(20)]

    overflow = client.post(
        f"/engagements/{eng['slug']}/findings",
        json={"title": "too many tags", "tags": [f"tag-{i}" for i in range(21)]},
        headers=_headers(),
    )
    assert overflow.status_code == 422, overflow.text


def test_observation_finding_link_round_trip(
    client: TestClient, cleanup_slugs: list[str]
) -> None:
    """v1.4.8: an observation can reference findings; the back-ref shows up
    on the finding, and unlink removes it."""
    eng = _create(client, f"Link holder {uuid.uuid4().hex[:6]}")
    cleanup_slugs.append(eng["slug"])

    finding = _create_finding(client, eng["slug"], title="hardened domain")
    fid = finding["id"]

    obs = client.post(
        f"/engagements/{eng['slug']}/observations",
        json={"content": "This domain is remarkably hardened."},
        headers=_headers(),
    ).json()
    oid = obs["id"]
    # New observations carry an empty finding_ids list.
    assert obs["finding_ids"] == []

    # Link (idempotent — call twice, still one ref).
    linked = client.post(f"/observations/{oid}/findings/{fid}", headers=_headers())
    assert linked.status_code == 201, linked.text
    assert linked.json()["finding_ids"] == [fid]
    again = client.post(f"/observations/{oid}/findings/{fid}", headers=_headers())
    assert again.status_code == 201
    assert again.json()["finding_ids"] == [fid]

    # The observation list carries the link too.
    listed = client.get(
        f"/engagements/{eng['slug']}/observations", headers=_headers()
    ).json()
    assert listed[0]["finding_ids"] == [fid]

    # Back-ref: the finding surfaces the observations that reference it.
    back = client.get(f"/findings/{fid}/observations", headers=_headers()).json()
    assert len(back) == 1
    assert back[0]["id"] == oid

    # Unlink (idempotent).
    gone = client.delete(f"/observations/{oid}/findings/{fid}", headers=_headers())
    assert gone.status_code == 204
    assert (
        client.delete(f"/observations/{oid}/findings/{fid}", headers=_headers()).status_code
        == 204
    )
    assert client.get(
        f"/findings/{fid}/observations", headers=_headers()
    ).json() == []


def test_observation_link_rejects_cross_engagement(
    client: TestClient, cleanup_slugs: list[str]
) -> None:
    """A link across two different engagements is rejected — it would
    confuse the report and the back-ref surface."""
    eng_a = _create(client, f"Eng A {uuid.uuid4().hex[:6]}")
    eng_b = _create(client, f"Eng B {uuid.uuid4().hex[:6]}")
    cleanup_slugs.extend([eng_a["slug"], eng_b["slug"]])

    finding_b = _create_finding(client, eng_b["slug"], title="other eng finding")
    obs_a = client.post(
        f"/engagements/{eng_a['slug']}/observations",
        json={"content": "note in eng A"},
        headers=_headers(),
    ).json()

    bad = client.post(
        f"/observations/{obs_a['id']}/findings/{finding_b['id']}",
        headers=_headers(),
    )
    assert bad.status_code == 400
    assert "different engagements" in bad.json()["detail"]


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


def test_run_endpoint_enqueues_run_start(
    client: TestClient,
    redis_client: redis_lib.Redis,
    cleanup_slugs: list[str],
) -> None:
    _seed_provider_key(client)
    eng = _create(client, "Runnable")
    cleanup_slugs.append(eng["slug"])

    response = client.post(
        f"/engagements/{eng['slug']}/runs",
        json={
            "prompt": "enumerate acme.com",
            # Pin the provider so the BYO-key precheck hits the seeded
            # Ollama row regardless of ``settings.llm_provider`` default
            # (CI sets LLM_PROVIDER=ollama; local hosts often don't).
            "model": {"provider": "ollama", "name": "llama3.1:8b"},
        },
        headers=_headers(),
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["engagement_id"] == eng["id"]
    assert body["events_stream"] == outbound_stream(uuid.UUID(eng["id"]))

    # Verify the envelope hit the inbound stream.
    queued = redis_client.xrange(inbound_stream(uuid.UUID(eng["id"])))
    assert len(queued) == 1
    payload = json.loads(queued[0][1]["data"])
    assert payload["type"] == "run.start"
    assert payload["thread_id"] == body["thread_id"]
    assert payload["prompt"] == "enumerate acme.com"
    # BYO-keys wireup: the envelope carries the acting user id (NOT plaintext
    # api_key — that's resolved lazily by the worker via the ephemeral
    # Redis-backed provider-key store).
    assert "acting_user_id" in payload
    assert "api_key" not in payload


def test_run_endpoint_rejects_archived_engagement(
    client: TestClient, cleanup_slugs: list[str]
) -> None:
    eng = _create(client, "Archived no runs")
    cleanup_slugs.append(eng["slug"])
    client.delete(f"/engagements/{eng['slug']}", headers=_headers())

    response = client.post(
        f"/engagements/{eng['slug']}/runs",
        json={"prompt": "should be rejected"},
        headers=_headers(),
    )
    assert response.status_code == 409


def test_run_endpoint_404_for_unknown_engagement(client: TestClient) -> None:
    response = client.post(
        f"/engagements/does-not-exist-{uuid.uuid4().hex[:6]}/runs",
        json={"prompt": "..."},
        headers=_headers(),
    )
    assert response.status_code == 404


def test_run_endpoint_defaults_model_when_body_omits(
    client: TestClient,
    redis_client: redis_lib.Redis,
    cleanup_slugs: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Body without model => response + envelope echo the settings default."""
    # Pin the default to ollama so the seeded ollama key satisfies the
    # BYO-key precheck regardless of host env (CI sets LLM_PROVIDER=ollama
    # already; local hosts often don't). The "echo the settings default"
    # semantic still holds — the assertions read settings.llm_provider.
    monkeypatch.setattr(settings, "llm_provider", "ollama")
    _seed_provider_key(client)
    eng = _create(client, "Default model")
    cleanup_slugs.append(eng["slug"])

    response = client.post(
        f"/engagements/{eng['slug']}/runs",
        json={"prompt": "go"},
        headers=_headers(),
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["model"]["provider"] == settings.llm_provider
    assert body["model"]["name"]  # non-empty

    queued = redis_client.xrange(inbound_stream(uuid.UUID(eng["id"])))
    payload = json.loads(queued[-1][1]["data"])
    # v1.4.12: model now carries an optional key_id; compare the fields
    # that matter, not exact-dict equality.
    assert payload["model"]["provider"] == body["model"]["provider"]
    assert payload["model"]["name"] == body["model"]["name"]


def test_run_endpoint_passes_through_explicit_model(
    client: TestClient,
    redis_client: redis_lib.Redis,
    cleanup_slugs: list[str],
) -> None:
    """Body with model => envelope carries that exact model; redis cache populated."""
    _seed_provider_key(client)
    eng = _create(client, "Explicit model")
    cleanup_slugs.append(eng["slug"])

    chosen = {"provider": "ollama", "name": "llama3.1:8b"}
    response = client.post(
        f"/engagements/{eng['slug']}/runs",
        json={"prompt": "go", "model": chosen},
        headers=_headers(),
    )
    assert response.status_code == 202, response.text
    body = response.json()
    # v1.4.12: model now carries an optional key_id; compare fields.
    assert body["model"]["provider"] == chosen["provider"]
    assert body["model"]["name"] == chosen["name"]

    payload = json.loads(
        redis_client.xrange(inbound_stream(uuid.UUID(eng["id"])))[-1][1]["data"]
    )
    assert payload["model"]["provider"] == chosen["provider"]
    assert payload["model"]["name"] == chosen["name"]

    cached = redis_client.hgetall(f"run:model:{body['thread_id']}")
    # The cache hash also stores acting_user_id so the approval-resume
    # envelope can carry the kicker forward. Assert on the model fields
    # only, not exact equality.
    assert cached["provider"] == chosen["provider"]
    assert cached["name"] == chosen["name"]
    assert "acting_user_id" in cached


def test_run_endpoint_rejects_when_no_user_provider_key(
    client: TestClient,
    cleanup_slugs: list[str],
) -> None:
    """BYO-keys wireup: a provider the user has no key for returns 400.

    The acting user (engagement-test@example.com) has an ``ollama`` row
    seeded by ``_seed_provider_key`` in other tests; here we ask for
    ``anthropic`` instead and expect the strict-mode refusal with a
    pointer to /settings/keys.
    """
    _seed_provider_key(client)  # ollama row, NOT anthropic
    eng = _create(client, "No anthropic key")
    cleanup_slugs.append(eng["slug"])

    response = client.post(
        f"/engagements/{eng['slug']}/runs",
        json={
            "prompt": "go",
            "model": {"provider": "anthropic", "name": "claude-opus-4-7"},
        },
        headers=_headers(),
    )
    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert "anthropic" in detail.lower()
    assert "/settings/keys" in detail
