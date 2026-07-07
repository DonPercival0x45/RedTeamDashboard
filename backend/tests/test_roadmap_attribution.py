"""Roadmap suggestion attribution — who submitted / approved / shipped.

The feedback surface needs to show *who* submitted a suggestion and *who*
approved it (roadmap item id ``019f1536-...``, admin-noted "do it nao"). The
DB already carried the ``author_user_id`` / ``reviewed_by_user_id`` /
``implemented_by_user_id`` FKs; these tests pin that the read schema now
resolves them to display names + emails via the model's joined
relationships, and that unresolved (null) FKs serialize as null rather than
erroring.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.models.roadmap_suggestion import (
    RoadmapSuggestion,
    RoadmapSuggestionStatus,
)
from app.models.user import User, UserRole


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _mk_user(db: Session, *, display_name: str, role: UserRole) -> User:
    user = User(
        email=f"{uuid.uuid4().hex[:12]}@attribution.test",
        display_name=display_name,
        role=role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture()
def seeded(db: Session) -> Iterator[dict]:
    """An approved suggestion authored by one user, reviewed by an admin."""
    author = _mk_user(db, display_name="Alice Author", role=UserRole.user)
    admin = _mk_user(db, display_name="Boss Admin", role=UserRole.admin)
    row = RoadmapSuggestion(
        author_user_id=author.id,
        body="Attribution should show who submitted and approved.",
        agent_pros=["clear"],
        agent_cons=[],
        agent_summary="do it",
        status=RoadmapSuggestionStatus.approved,
        reviewed_by_user_id=admin.id,
        review_note="ship it",
        source="ui",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    try:
        yield {"row": row, "author": author, "admin": admin}
    finally:
        db.delete(row)
        db.delete(author)
        db.delete(admin)
        db.commit()


def test_get_resolves_author_and_reviewer(
    client: TestClient, seeded: dict
) -> None:
    row = seeded["row"]
    resp = client.get(
        f"/roadmap-suggestions/{row.id}",
        headers={"X-User-Id": "reader@attribution.test"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["author_user_id"] == str(seeded["author"].id)
    assert body["author_display_name"] == "Alice Author"
    assert body["author_email"] == seeded["author"].email
    assert body["reviewed_by_display_name"] == "Boss Admin"
    assert body["reviewed_by_email"] == seeded["admin"].email
    # Never shipped -> implementer fields stay null, not errors.
    assert body["implemented_by_display_name"] is None
    assert body["implemented_by_email"] is None


def test_list_includes_attribution(client: TestClient, seeded: dict) -> None:
    resp = client.get(
        "/roadmap-suggestions",
        headers={"X-User-Id": "reader@attribution.test"},
    )
    assert resp.status_code == 200, resp.text
    match = next(
        (r for r in resp.json() if r["id"] == str(seeded["row"].id)), None
    )
    assert match is not None
    assert match["author_display_name"] == "Alice Author"
    assert match["reviewed_by_display_name"] == "Boss Admin"


def test_unreviewed_suggestion_has_null_reviewer(
    client: TestClient, db: Session
) -> None:
    author = _mk_user(db, display_name="Solo", role=UserRole.user)
    row = RoadmapSuggestion(
        author_user_id=author.id,
        body="Pending row with no reviewer yet.",
        agent_pros=[],
        agent_cons=[],
        status=RoadmapSuggestionStatus.pending_review,
        source="ui",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    try:
        resp = client.get(
            f"/roadmap-suggestions/{row.id}",
            headers={"X-User-Id": "reader@attribution.test"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["author_display_name"] == "Solo"
        assert body["reviewed_by_user_id"] is None
        assert body["reviewed_by_display_name"] is None
        assert body["reviewed_by_email"] is None
    finally:
        db.delete(row)
        db.delete(author)
        db.commit()
