"""Work-item comments API — analyst discussion thread on a work item.

Covers: POST creates a comment (author resolved from X-User-Id), GET lists the
thread oldest-first with an author label, an archived/completed engagement
rejects POST (read-only gate), and empty/whitespace-only bodies are rejected.

NOTE: this test does not import engagement_strategist / suggestion_router, so it
doesn't need the lazy-import dance the strategist tests use to avoid collection-
time pollution. Comments are plain CRUD on app.models.WorkItemComment.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.main import app
from app.models import Engagement, EngagementStatus, EngagementWorkState

HDR = {"X-User-Id": "work-item-comments@example.com"}


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def engagement(db: Session) -> Iterator[Engagement]:
    row = Engagement(
        name="Work Item Comments",
        slug=f"work-item-comments-{uuid.uuid4().hex[:8]}",
        status=EngagementStatus.active,
        work_state=EngagementWorkState.active,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    try:
        yield row
    finally:
        db.execute(text("SELECT flush_engagement(:id)"), {"id": row.id})
        db.commit()


def _make_work_item(client: TestClient, engagement: Engagement) -> str:
    resp = client.post(
        f"/engagements/{engagement.slug}/work-items",
        json={"title": "Comment target"},
        headers=HDR,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_post_and_list_comment(client: TestClient, engagement: Engagement) -> None:
    work_item_id = _make_work_item(client, engagement)

    created = client.post(
        f"/work-items/{work_item_id}/comments",
        json={"body": "  hello world  "},  # body is stripped server-side
        headers=HDR,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["body"] == "hello world"
    assert body["work_item_id"] == work_item_id
    assert body["author"]["label"]  # display_name or email, non-empty
    assert body["author"]["id"]

    listed = client.get(f"/work-items/{work_item_id}/comments", headers=HDR)
    assert listed.status_code == 200, listed.text
    rows = listed.json()
    assert len(rows) == 1
    assert rows[0]["body"] == "hello world"
    assert rows[0]["author"]["label"]


def test_comments_listed_oldest_first(client: TestClient, engagement: Engagement) -> None:
    work_item_id = _make_work_item(client, engagement)
    for n in ("first", "second", "third"):
        resp = client.post(
            f"/work-items/{work_item_id}/comments",
            json={"body": n},
            headers=HDR,
        )
        assert resp.status_code == 201, resp.text
    rows = client.get(f"/work-items/{work_item_id}/comments", headers=HDR).json()
    assert [r["body"] for r in rows] == ["first", "second", "third"]


def test_archived_engagement_rejects_comment(
    client: TestClient, engagement: Engagement, db: Session
) -> None:
    work_item_id = _make_work_item(client, engagement)
    engagement.status = EngagementStatus.archived
    db.commit()

    resp = client.post(
        f"/work-items/{work_item_id}/comments",
        json={"body": "should be rejected"},
        headers=HDR,
    )
    assert resp.status_code == 409, resp.text


def test_empty_or_whitespace_body_rejected(
    client: TestClient, engagement: Engagement
) -> None:
    work_item_id = _make_work_item(client, engagement)

    empty = client.post(
        f"/work-items/{work_item_id}/comments",
        json={"body": ""},
        headers=HDR,
    )
    assert empty.status_code == 422, empty.text  # pydantic min_length=1

    whitespace = client.post(
        f"/work-items/{work_item_id}/comments",
        json={"body": "    "},
        headers=HDR,
    )
    assert whitespace.status_code == 400, whitespace.text  # server-side strip -> empty


def test_get_comments_404_for_unknown_work_item(client: TestClient) -> None:
    missing = uuid.uuid4()
    resp = client.get(f"/work-items/{missing}/comments", headers=HDR)
    assert resp.status_code == 404, resp.text
