"""Tests for v1.24.0 Settings > Configurations — per-analyst per-engagement
agent-model routing. Covers CRUD, export/import round-trip, unknown-slug
skip, resolver fallback chain, and per-user isolation.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.main import app
from app.models import (
    AgentModelPreference,
    AgentName,
    Engagement,
    EngagementStatus,
)
from app.services.agent_model_resolver import (
    parse_model_string,
    provider_for_model,
    resolve_agent_model,
)

HDR_A = {"X-User-Id": "config-alice@example.com"}
HDR_B = {"X-User-Id": "config-bob@example.com"}


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def engagement(db: Session) -> Iterator[Engagement]:
    eng = Engagement(
        name="Config Engagement",
        slug=f"config-eng-{uuid.uuid4().hex[:8]}",
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
def other_engagement(db: Session) -> Iterator[Engagement]:
    eng = Engagement(
        name="Other Config Engagement",
        slug=f"config-eng-{uuid.uuid4().hex[:8]}",
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


# ---------------------------------------------------------------------------
# CRUD happy path
# ---------------------------------------------------------------------------


def test_put_creates_all_three_roles(
    client: TestClient, engagement: Engagement
) -> None:
    resp = client.put(
        f"/agent-configurations/{engagement.slug}",
        headers=HDR_A,
        json={
            "strategic": "claude-sonnet-4-6",
            "tactical": "claude-haiku-4-5",
            "correlate": "claude-opus-4-8",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["engagement_slug"] == engagement.slug
    assert body["strategic"] == "claude-sonnet-4-6"
    assert body["tactical"] == "claude-haiku-4-5"
    assert body["correlate"] == "claude-opus-4-8"


def test_list_returns_current_users_configs_only(
    client: TestClient, engagement: Engagement
) -> None:
    # Alice sets a config.
    client.put(
        f"/agent-configurations/{engagement.slug}",
        headers=HDR_A,
        json={"strategic": "claude-sonnet-4-6"},
    )
    # Alice sees it.
    resp = client.get("/agent-configurations", headers=HDR_A)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["configurations"]) == 1
    assert body["configurations"][0]["strategic"] == "claude-sonnet-4-6"

    # Bob sees an empty list.
    resp_b = client.get("/agent-configurations", headers=HDR_B)
    assert resp_b.status_code == 200
    assert resp_b.json() == {"configurations": []}


def test_put_partial_leaves_others_untouched(
    client: TestClient, engagement: Engagement
) -> None:
    client.put(
        f"/agent-configurations/{engagement.slug}",
        headers=HDR_A,
        json={
            "strategic": "claude-sonnet-4-6",
            "tactical": "claude-haiku-4-5",
        },
    )
    resp = client.put(
        f"/agent-configurations/{engagement.slug}",
        headers=HDR_A,
        json={"correlate": "gpt-4o"},
    )
    body = resp.json()
    assert body["strategic"] == "claude-sonnet-4-6"
    assert body["tactical"] == "claude-haiku-4-5"
    assert body["correlate"] == "gpt-4o"


def test_put_null_clears_that_role(
    client: TestClient, engagement: Engagement
) -> None:
    client.put(
        f"/agent-configurations/{engagement.slug}",
        headers=HDR_A,
        json={"strategic": "claude-sonnet-4-6"},
    )
    resp = client.put(
        f"/agent-configurations/{engagement.slug}",
        headers=HDR_A,
        json={"strategic": None},
    )
    # Explicit null on strategic clears; body reflects null and the row
    # is gone.
    body = resp.json()
    assert body["strategic"] is None


def test_delete_clears_all_roles_for_this_user_and_engagement(
    client: TestClient, db: Session, engagement: Engagement
) -> None:
    client.put(
        f"/agent-configurations/{engagement.slug}",
        headers=HDR_A,
        json={
            "strategic": "claude-sonnet-4-6",
            "tactical": "claude-haiku-4-5",
        },
    )
    resp = client.delete(
        f"/agent-configurations/{engagement.slug}", headers=HDR_A
    )
    assert resp.status_code == 204

    remaining = db.execute(
        select(AgentModelPreference).where(
            AgentModelPreference.engagement_id == engagement.id
        )
    ).scalars().all()
    assert remaining == []


# ---------------------------------------------------------------------------
# Export / import round-trip
# ---------------------------------------------------------------------------


def test_export_returns_json_download(
    client: TestClient, engagement: Engagement
) -> None:
    client.put(
        f"/agent-configurations/{engagement.slug}",
        headers=HDR_A,
        json={"strategic": "claude-sonnet-4-6", "tactical": "gpt-4o"},
    )
    resp = client.get("/agent-configurations/export", headers=HDR_A)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    assert "attachment" in resp.headers.get("content-disposition", "")

    body = resp.json()
    assert body["version"] == 1
    assert engagement.slug in body["configurations"]
    payload = body["configurations"][engagement.slug]
    assert payload["strategic"] == "claude-sonnet-4-6"
    assert payload["tactical"] == "gpt-4o"


def test_import_round_trip(
    client: TestClient, engagement: Engagement, other_engagement: Engagement
) -> None:
    # Seed one engagement, export, wipe, re-import — everything should
    # come back.
    client.put(
        f"/agent-configurations/{engagement.slug}",
        headers=HDR_A,
        json={
            "strategic": "claude-sonnet-4-6",
            "tactical": "claude-haiku-4-5",
            "correlate": "claude-opus-4-8",
        },
    )
    client.put(
        f"/agent-configurations/{other_engagement.slug}",
        headers=HDR_A,
        json={"strategic": "gpt-4o"},
    )

    exported = client.get("/agent-configurations/export", headers=HDR_A).json()

    client.delete(f"/agent-configurations/{engagement.slug}", headers=HDR_A)
    client.delete(
        f"/agent-configurations/{other_engagement.slug}", headers=HDR_A
    )

    resp = client.post(
        "/agent-configurations/import", headers=HDR_A, json=exported
    )
    assert resp.status_code == 200
    result = resp.json()
    assert set(result["applied_slugs"]) == {
        engagement.slug,
        other_engagement.slug,
    }
    assert result["skipped_unknown_slugs"] == []

    listed = client.get("/agent-configurations", headers=HDR_A).json()
    by_slug = {c["engagement_slug"]: c for c in listed["configurations"]}
    assert by_slug[engagement.slug]["strategic"] == "claude-sonnet-4-6"
    assert by_slug[engagement.slug]["tactical"] == "claude-haiku-4-5"
    assert by_slug[engagement.slug]["correlate"] == "claude-opus-4-8"
    assert by_slug[other_engagement.slug]["strategic"] == "gpt-4o"


def test_import_skips_unknown_slugs(
    client: TestClient, engagement: Engagement
) -> None:
    payload = {
        "version": 1,
        "exported_at": "2026-07-09T00:00:00Z",
        "exported_by_user_id": str(uuid.uuid4()),
        "configurations": {
            engagement.slug: {"strategic": "claude-sonnet-4-6"},
            "engagement-that-does-not-exist": {"strategic": "gpt-4o"},
        },
    }
    resp = client.post(
        "/agent-configurations/import", headers=HDR_A, json=payload
    )
    assert resp.status_code == 200
    result = resp.json()
    assert result["applied_slugs"] == [engagement.slug]
    assert result["skipped_unknown_slugs"] == [
        "engagement-that-does-not-exist"
    ]


# ---------------------------------------------------------------------------
# Resolver fallback chain
# ---------------------------------------------------------------------------


def test_resolver_uses_preference_row_when_present(
    client: TestClient, db: Session, engagement: Engagement
) -> None:
    # Setting via API to also create the user record.
    client.put(
        f"/agent-configurations/{engagement.slug}",
        headers=HDR_A,
        json={"strategic": "claude-sonnet-4-6"},
    )
    # Look up alice's id.
    user_row = db.execute(
        text("SELECT id FROM users WHERE email = :e"),
        {"e": "config-alice@example.com"},
    ).scalar_one()
    resolved = resolve_agent_model(
        db,
        user_id=user_row,
        engagement_id=engagement.id,
        role=AgentName.strategic,
    )
    assert resolved is not None
    provider, model_name = resolved
    assert model_name == "claude-sonnet-4-6"
    assert provider == "anthropic"


def test_resolver_returns_none_without_preference_or_default(
    client: TestClient, db: Session, engagement: Engagement
) -> None:
    # Ensure the user exists but has no preference row and no
    # default_model — a bare GET creates the user record via auth.
    client.get("/agent-configurations", headers=HDR_A)
    user_row = db.execute(
        text("SELECT id FROM users WHERE email = :e"),
        {"e": "config-alice@example.com"},
    ).scalar_one()
    resolved = resolve_agent_model(
        db,
        user_id=user_row,
        engagement_id=engagement.id,
        role=AgentName.strategic,
    )
    assert resolved is None


def test_resolver_engagement_none_short_circuits(
    client: TestClient, db: Session, engagement: Engagement
) -> None:
    client.put(
        f"/agent-configurations/{engagement.slug}",
        headers=HDR_A,
        json={"strategic": "claude-sonnet-4-6"},
    )
    user_row = db.execute(
        text("SELECT id FROM users WHERE email = :e"),
        {"e": "config-alice@example.com"},
    ).scalar_one()
    # No engagement -> preference table is not consulted; falls through
    # to user default (None here) -> returns None.
    resolved = resolve_agent_model(
        db,
        user_id=user_row,
        engagement_id=None,
        role=AgentName.strategic,
    )
    assert resolved is None


# ---------------------------------------------------------------------------
# Model-string parsing / provider inference
# ---------------------------------------------------------------------------


def test_parse_provider_qualified_string() -> None:
    assert parse_model_string("anthropic:claude-opus-4-8") == (
        "anthropic",
        "claude-opus-4-8",
    )
    assert parse_model_string("openai:gpt-4o") == ("openai", "gpt-4o")


def test_parse_bare_string_leaves_provider_none() -> None:
    assert parse_model_string("claude-sonnet-4-6") == (None, "claude-sonnet-4-6")


def test_provider_inference_covers_common_prefixes() -> None:
    assert provider_for_model("claude-opus-4-8") == "anthropic"
    assert provider_for_model("sonnet-4-6") == "anthropic"
    assert provider_for_model("gpt-4o") == "openai"
    assert provider_for_model("o3-mini") == "openai"
    assert provider_for_model("grok-3") == "xai"
    assert provider_for_model("deepseek-chat") == "deepseek"
    assert provider_for_model("kimi-k2-turbo-preview") == "moonshot"
    assert provider_for_model("moonshot-v1-128k") == "moonshot"
    assert provider_for_model("gemini-1.5-pro") == "google"
    # Unknown prefix -> None; caller decides fallback.
    assert provider_for_model("some-weird-model") is None


# ---------------------------------------------------------------------------
# Per-user isolation
# ---------------------------------------------------------------------------


def test_alice_cannot_see_bobs_configs(
    client: TestClient, engagement: Engagement
) -> None:
    # Bob sets a config on the shared engagement.
    client.put(
        f"/agent-configurations/{engagement.slug}",
        headers=HDR_B,
        json={"strategic": "gpt-4o"},
    )
    # Alice's list is empty even though the engagement has a config.
    resp = client.get("/agent-configurations", headers=HDR_A)
    assert resp.status_code == 200
    assert resp.json() == {"configurations": []}


def test_alice_delete_does_not_touch_bobs_row(
    client: TestClient, db: Session, engagement: Engagement
) -> None:
    client.put(
        f"/agent-configurations/{engagement.slug}",
        headers=HDR_A,
        json={"strategic": "claude-sonnet-4-6"},
    )
    client.put(
        f"/agent-configurations/{engagement.slug}",
        headers=HDR_B,
        json={"strategic": "gpt-4o"},
    )
    client.delete(f"/agent-configurations/{engagement.slug}", headers=HDR_A)

    # Bob's row survives.
    bob_view = client.get("/agent-configurations", headers=HDR_B).json()
    assert len(bob_view["configurations"]) == 1
    assert bob_view["configurations"][0]["strategic"] == "gpt-4o"

    # Alice's row is gone.
    alice_view = client.get("/agent-configurations", headers=HDR_A).json()
    assert alice_view == {"configurations": []}


# ---------------------------------------------------------------------------
# Unknown engagement -> 404 on put/delete
# ---------------------------------------------------------------------------


def test_put_unknown_slug_404(client: TestClient) -> None:
    resp = client.put(
        "/agent-configurations/no-such-slug",
        headers=HDR_A,
        json={"strategic": "claude-sonnet-4-6"},
    )
    assert resp.status_code == 404


def test_delete_unknown_slug_404(client: TestClient) -> None:
    resp = client.delete(
        "/agent-configurations/no-such-slug", headers=HDR_A
    )
    assert resp.status_code == 404
