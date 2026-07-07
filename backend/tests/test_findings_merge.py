"""Tests for the manual-merge behavior added in v1.4.10.

Covers the two new invariants:

- ``POST /findings/{parent_id}/merge`` unions each child's items[] into
  the parent (with dedup), and stamps ``parent.group_key = "manual:<...>"``
  so future auto-regroup runs leave the row alone.
- ``POST /findings/repair-groups`` skips any parent whose group_key
  starts with ``manual:`` — no rekey, no items rebuild.

Uses the same live compose Postgres harness as ``test_engagements_api``.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
import redis as redis_lib
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.main import app
from app.models import Engagement, Finding, Severity, User, UserRole
from app.runs.streams import inbound_stream, outbound_stream


# ---------------------------------------------------------------------------
# Fixtures (same shape as test_engagements_api)
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


TEST_USER_EMAIL = "merge-test@example.com"


def _headers() -> dict[str, str]:
    return {"X-User-Id": TEST_USER_EMAIL}


def _create_engagement(client: TestClient, name: str) -> dict[str, Any]:
    response = client.post("/engagements", json={"name": name}, headers=_headers())
    assert response.status_code == 201, response.text
    return response.json()


def _promote_user_to_admin(db: Session) -> None:
    """Repair-groups is admin-only. Bump the test user so the call lands
    at the route logic, not the auth gate."""
    row = db.execute(
        select(User).where(User.email == TEST_USER_EMAIL)
    ).scalar_one_or_none()
    if row is not None:
        row.role = UserRole.admin
        db.commit()


def _finding(
    engagement_id: uuid.UUID,
    *,
    title: str,
    tool: str,
    target: str,
    severity: Severity,
    details: dict[str, Any],
    group_key: str | None = None,
) -> Finding:
    return Finding(
        engagement_id=engagement_id,
        title=title,
        severity=severity,
        source_tool=tool,
        target=target,
        details=details,
        group_key=group_key,
    )


# ---------------------------------------------------------------------------
# merge_findings
# ---------------------------------------------------------------------------


def test_merge_stamps_manual_group_key_on_ungrouped_parent(
    client: TestClient, db: Session, cleanup_slugs: list[str]
) -> None:
    eng = _create_engagement(client, f"Merge stamp {uuid.uuid4().hex[:6]}")
    cleanup_slugs.append(eng["slug"])
    eng_id = uuid.UUID(eng["id"])

    parent = _finding(
        eng_id,
        title="Parent ungrouped",
        tool="subfinder",
        target="acme.com",
        severity=Severity.low,
        details={"args": {"domain": "acme.com"}, "subdomains": ["a.acme.com"]},
    )
    child = _finding(
        eng_id,
        title="Child ungrouped",
        tool="subfinder",
        target="acme.com",
        severity=Severity.info,
        details={"args": {"domain": "acme.com"}, "subdomains": ["b.acme.com"]},
    )
    db.add(parent)
    db.add(child)
    db.commit()
    db.refresh(parent)
    db.refresh(child)

    response = client.post(
        f"/findings/{parent.id}/merge",
        json={"child_ids": [str(child.id)]},
        headers=_headers(),
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["group_key"] is not None
    assert body["group_key"].startswith("manual:"), body["group_key"]

    db.refresh(parent)
    items = (parent.details or {}).get("items") or []
    assert isinstance(items, list) and len(items) == 2, items
    subs = sorted(it.get("subdomain") for it in items if isinstance(it, dict))
    assert subs == ["a.acme.com", "b.acme.com"]

    db.refresh(child)
    assert child.deleted_at is not None


def test_merge_unions_items_from_grouped_children_with_dedup(
    client: TestClient, db: Session, cleanup_slugs: list[str]
) -> None:
    eng = _create_engagement(client, f"Merge union {uuid.uuid4().hex[:6]}")
    cleanup_slugs.append(eng["slug"])
    eng_id = uuid.UUID(eng["id"])

    parent = _finding(
        eng_id,
        title="Subdomains — acme.com",
        tool="subfinder",
        target="acme.com",
        severity=Severity.low,
        details={
            "grouped": True,
            "items": [
                {"subdomain": "a.acme.com", "source_tool": "subfinder"},
                {"subdomain": "b.acme.com", "source_tool": "subfinder"},
            ],
        },
        group_key="subdomains:acme.com",
    )
    child = _finding(
        eng_id,
        title="Subdomains — other.com",
        tool="subfinder",
        target="other.com",
        severity=Severity.info,
        details={
            "grouped": True,
            "items": [
                # b.acme.com duplicates — must dedup
                {"subdomain": "b.acme.com", "source_tool": "subfinder"},
                {"subdomain": "c.other.com", "source_tool": "subfinder"},
            ],
        },
        group_key="subdomains:other.com",
    )
    db.add(parent)
    db.add(child)
    db.commit()
    db.refresh(parent)
    db.refresh(child)

    response = client.post(
        f"/findings/{parent.id}/merge",
        json={"child_ids": [str(child.id)]},
        headers=_headers(),
    )
    assert response.status_code == 200, response.text

    db.refresh(parent)
    items = (parent.details or {}).get("items") or []
    subs = sorted(
        it.get("subdomain") for it in items if isinstance(it, dict) and it.get("subdomain")
    )
    assert subs == ["a.acme.com", "b.acme.com", "c.other.com"], subs

    assert parent.group_key is not None
    assert parent.group_key.startswith("manual:")


def test_merge_picks_highest_severity_across_group(
    client: TestClient, db: Session, cleanup_slugs: list[str]
) -> None:
    eng = _create_engagement(client, f"Merge sev {uuid.uuid4().hex[:6]}")
    cleanup_slugs.append(eng["slug"])
    eng_id = uuid.UUID(eng["id"])

    parent = _finding(
        eng_id,
        title="Med parent",
        tool="dns_lookup",
        target="acme.com",
        severity=Severity.medium,
        details={"args": {}, "a": ["1.1.1.1"]},
    )
    child = _finding(
        eng_id,
        title="Critical child",
        tool="dns_lookup",
        target="acme.com",
        severity=Severity.critical,
        details={"args": {}, "a": ["2.2.2.2"]},
    )
    db.add(parent)
    db.add(child)
    db.commit()
    db.refresh(parent)
    db.refresh(child)

    response = client.post(
        f"/findings/{parent.id}/merge",
        json={"child_ids": [str(child.id)]},
        headers=_headers(),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["severity"] == "critical"


# ---------------------------------------------------------------------------
# repair_groups skips manual: parents
# ---------------------------------------------------------------------------


def test_repair_groups_leaves_manual_parents_alone(
    client: TestClient, db: Session, cleanup_slugs: list[str]
) -> None:
    eng = _create_engagement(client, f"Repair manual {uuid.uuid4().hex[:6]}")
    cleanup_slugs.append(eng["slug"])
    eng_id = uuid.UUID(eng["id"])

    manual_items = [
        {"subdomain": "keeper.com", "source_tool": "subfinder"},
        {"port": 22, "host": "10.0.0.5", "source_tool": "portscan"},
    ]
    manual_parent = _finding(
        eng_id,
        title="Manual group",
        tool="subfinder",
        target="acme.com",
        severity=Severity.high,
        details={"grouped": True, "items": list(manual_items)},
        group_key="manual:deadbeef",
    )
    db.add(manual_parent)
    db.commit()
    db.refresh(manual_parent)
    manual_id = manual_parent.id

    _promote_user_to_admin(db)

    response = client.post(
        f"/engagements/{eng['slug']}/findings/repair-groups",
        headers=_headers(),
    )
    assert response.status_code == 200, response.text

    db.refresh(manual_parent)
    assert manual_parent.group_key == "manual:deadbeef"
    assert manual_parent.deleted_at is None
    items = (manual_parent.details or {}).get("items") or []
    assert isinstance(items, list) and len(items) == len(manual_items)
    # Manual parent's ID unchanged — it wasn't rekeyed away or folded.
    still_there = db.execute(
        select(Finding.group_key).where(Finding.id == manual_id)
    ).scalar_one_or_none()
    assert still_there == "manual:deadbeef"
