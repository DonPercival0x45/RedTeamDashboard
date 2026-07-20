"""Work-item creation is idempotent — no duplicate work items.

Accepting a work-item suggestion always created a new row even when an
identical one already existed (e.g. a bootstrap-seeded item the strategist
re-proposed pre-dedup). The accept path + bootstrap seeding now consult a
shared `_find_existing_work_item` identity check.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import (
    AgentName,
    Engagement,
    EngagementStatus,
    EngagementWorkState,
    ScopeItem,
    ScopeKind,
    Suggestion,
    SuggestionKind,
    SuggestionStatus,
    User,
    UserRole,
    WorkItem,
    WorkItemExecutor,
    WorkItemPriority,
    WorkItemStatus,
)
from app.services.suggestion_router import (
    _accept_work_item,
    _find_existing_work_item,
)


@pytest.fixture()
def engagement(db: Session):
    row = Engagement(
        name="WorkItemDedup",
        slug=f"wid-{uuid.uuid4().hex[:8]}",
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


def _user(db: Session) -> User:
    row = User(email=f"wid-{uuid.uuid4().hex[:8]}@example.com", role=UserRole.user)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _scope(db: Session, engagement: Engagement, value: str = "5qpartners.com") -> ScopeItem:
    s = ScopeItem(engagement_id=engagement.id, kind=ScopeKind.domain, value=value)
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


def test_find_existing_returns_identity_match(
    db: Session, engagement: Engagement
) -> None:
    """`_find_existing_work_item` matches by title+scope+entity+executor."""
    user = _user(db)
    scope = _scope(db, engagement, "5qpartners.com")
    a = WorkItem(
        engagement_id=engagement.id,
        title="Enumerate and triage 5qpartners.com",
        scope_item_id=scope.id,
        executor_type=WorkItemExecutor.finding_agent,
        status=WorkItemStatus.ready,
        priority=WorkItemPriority.high,
        created_by_user_id=user.id,
    )
    db.add(a)
    db.commit()

    found = _find_existing_work_item(
        db,
        engagement.id,
        title="Enumerate and triage 5qpartners.com",
        scope_item_id=scope.id,
        entity_id=None,
        executor_type=WorkItemExecutor.finding_agent,
    )
    assert found is not None
    assert found.id == a.id

    miss = _find_existing_work_item(
        db,
        engagement.id,
        title="A different title",
        scope_item_id=scope.id,
        entity_id=None,
        executor_type=WorkItemExecutor.finding_agent,
    )
    assert miss is None


def test_accept_work_item_links_to_existing_instead_of_duplicating(
    db: Session, engagement: Engagement
) -> None:
    """Accepting a suggestion matching an existing work item links, not duplicates."""
    user = _user(db)
    scope = _scope(db, engagement, "5qpartners.com")

    existing = WorkItem(
        engagement_id=engagement.id,
        title="Enumerate and triage 5qpartners.com",
        scope_item_id=scope.id,
        executor_type=WorkItemExecutor.finding_agent,
        status=WorkItemStatus.ready,
        priority=WorkItemPriority.high,
        created_by_user_id=user.id,
    )
    db.add(existing)
    db.commit()
    db.refresh(existing)

    suggestion = Suggestion(
        engagement_id=engagement.id,
        title="Enumerate and triage 5qpartners.com",
        kind=SuggestionKind.work_item,
        status=SuggestionStatus.open,
        payload={
            "schema_version": 1,
            "work_item": {
                "title": "Enumerate and triage 5qpartners.com",
                "scope_item_id": str(scope.id),
                "executor_type": "finding_agent",
                "priority": "high",
                "acceptance_criteria": [],
            },
        },
        created_by_agent=AgentName.strategic,
    )
    db.add(suggestion)
    db.commit()

    returned = _accept_work_item(db, suggestion, user_id=user.id)
    db.commit()

    # Linked to the existing item — no new row created.
    assert returned.id == existing.id
    assert suggestion.work_item_id == existing.id

    matching = (
        db.execute(
            select(WorkItem).where(
                WorkItem.engagement_id == engagement.id,
                WorkItem.title == "Enumerate and triage 5qpartners.com",
                WorkItem.executor_type == WorkItemExecutor.finding_agent,
            )
        ).scalars().all()
    )
    assert len(matching) == 1
