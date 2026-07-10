"""Tests for the derived-entities cache (v0.30.0 perf pass).

``GET /engagements/{slug}/entities`` runs ``extract_entities`` (regex over
every finding's content) on every call. These tests pin the self-invalidating
Redis cache: cold call computes + caches, warm call hits, and adding a
finding changes the fingerprint so it recomputes.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
import redis as redis_lib
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import settings
from app.main import app
from app.models import Engagement, Finding, FindingPhase, FindingStatus, Severity

HDR = {"X-User-Id": "entities-cache@example.com"}


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
def engagement(db: Session) -> Iterator[Engagement]:
    eng = Engagement(
        name=f"entities-cache-{uuid.uuid4().hex[:8]}",
        slug=f"entities-cache-{uuid.uuid4().hex[:8]}",
    )
    db.add(eng)
    db.commit()
    db.refresh(eng)
    try:
        yield eng
    finally:
        db.query(Finding).filter(Finding.engagement_id == eng.id).delete(
            synchronize_session=False
        )
        db.query(Engagement).filter(Engagement.id == eng.id).delete(
            synchronize_session=False
        )
        db.commit()


def _finding(db: Session, engagement_id, target: str, detail_email: str) -> Finding:
    f = Finding(
        engagement_id=engagement_id,
        title=f"finding-{detail_email}",
        target=target,
        severity=Severity.info,
        phase=FindingPhase.osint,
        status=FindingStatus.validated,
        details={"contact": detail_email},
    )
    db.add(f)
    db.commit()
    db.refresh(f)
    return f


def _clear_cache(redis_client: redis_lib.Redis, engagement_id) -> None:
    for key in redis_client.scan_iter(f"entities:{engagement_id}:*"):
        redis_client.delete(key)


def test_entities_cache_warm_hit_and_invalidation(
    client: TestClient, db: Session, redis_client: redis_lib.Redis, engagement: Engagement
) -> None:
    _clear_cache(redis_client, engagement.id)
    _finding(db, engagement.id, "mail.contoso.com", "alice@contoso.com")

    # Cold call computes + writes a cache entry.
    r1 = client.get(f"/engagements/{engagement.slug}/entities", headers=HDR)
    assert r1.status_code == 200
    emails1 = {e["value"] for e in r1.json() if e["type"] == "email"}
    assert "alice@contoso.com" in emails1
    keys = list(redis_client.scan_iter(f"entities:{engagement.id}:*"))
    assert len(keys) == 1, "cold call should write exactly one cache entry"

    # Warm call hits the cache (same key, same payload).
    r2 = client.get(f"/engagements/{engagement.slug}/entities", headers=HDR)
    assert r2.status_code == 200
    assert {e["value"] for e in r2.json() if e["type"] == "email"} == emails1
    assert list(redis_client.scan_iter(f"entities:{engagement.id}:*")) == keys

    # Adding a finding changes the fingerprint (count + max updated_at) ->
    # new key, recompute picks up the new email.
    _finding(db, engagement.id, "mail2.contoso.com", "bob@contoso.com")
    r3 = client.get(f"/engagements/{engagement.slug}/entities", headers=HDR)
    assert r3.status_code == 200
    emails3 = {e["value"] for e in r3.json() if e["type"] == "email"}
    assert {"alice@contoso.com", "bob@contoso.com"} <= emails3
    # Old keys are allowed to remain until TTL expiry; the fingerprint move
    # should create a distinct warm key that contains the recomputed payload.
    new_keys = list(redis_client.scan_iter(f"entities:{engagement.id}:*"))
    assert set(new_keys) != set(keys)
    assert any(k not in keys for k in new_keys)


def test_entities_type_and_query_filter_off_cached_set(
    client: TestClient, db: Session, redis_client: redis_lib.Redis, engagement: Engagement
) -> None:
    _clear_cache(redis_client, engagement.id)
    _finding(db, engagement.id, "contoso.com", "carol@contoso.com")

    base = client.get(f"/engagements/{engagement.slug}/entities", headers=HDR)
    by_type = client.get(
        f"/engagements/{engagement.slug}/entities?type=email", headers=HDR
    )
    assert all(e["type"] == "email" for e in by_type.json())
    assert len(by_type.json()) <= len(base.json())
    q = client.get(
        f"/engagements/{engagement.slug}/entities?q=carol", headers=HDR
    )
    assert all("carol" in e["value"] for e in q.json())
    # all three answered from the same cached entry (no recompute)
    assert len(list(redis_client.scan_iter(f"entities:{engagement.id}:*"))) == 1
