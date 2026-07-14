"""Race and authoritative-state regressions for Engagement Strategist chat."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.main import app
from app.models import (
    AgentExecution,
    AgentExecutionStatus,
    AgentName,
    Conversation,
    ConversationContextType,
    ConversationMessage,
    Engagement,
    EngagementStatus,
    EngagementStrategyRevision,
    StrategyRevisionState,
    Suggestion,
    SuggestionKind,
    SuggestionStatus,
    User,
    WorkItem,
)
from app.services.engagement_strategist import run_engagement_strategist


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def engagement(db: Session) -> Iterator[Engagement]:
    row = Engagement(
        name="Strategist race governance",
        slug=f"strategist-race-{uuid.uuid4().hex[:8]}",
        status=EngagementStatus.active,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    try:
        yield row
    finally:
        db.execute(text("SELECT flush_engagement(:id)"), {"id": row.id})
        db.commit()


def test_stale_chat_deny_cannot_flip_an_accepted_suggestion(
    client: TestClient, db: Session, engagement: Engagement
) -> None:
    user = User(email=f"stale-deny-{uuid.uuid4().hex[:6]}@example.test")
    db.add(user)
    db.flush()
    conversation = Conversation(
        engagement_id=engagement.id,
        finding_id=None,
        context_type=ConversationContextType.engagement,
        created_by_user_id=user.id,
        title="Strategy chat",
    )
    suggestion = Suggestion(
        engagement_id=engagement.id,
        title="Review authentication behavior",
        kind=SuggestionKind.work_item,
        status=SuggestionStatus.open,
        created_by_agent=AgentName.engagement_strategist,
        payload={
            "schema_version": 1,
            "work_item": {
                "title": "Review authentication behavior",
                "priority": "medium",
                "executor_type": "analyst",
                "finding_links": [],
            },
        },
    )
    db.add_all([conversation, suggestion])
    db.flush()
    message = ConversationMessage(
        conversation_id=conversation.id,
        role="assistant",
        content="Proposed shared work.",
        action_payload={
            "actions": [
                {
                    "type": "suggestion",
                    "suggestion_id": str(suggestion.id),
                    "suggestion_kind": "work_item",
                    "title": suggestion.title,
                    "status": "proposed",
                }
            ]
        },
    )
    db.add(message)
    db.commit()
    db.refresh(message)

    headers = {"X-User-Id": user.email}
    accepted = client.post(f"/suggestions/{suggestion.id}/accept", headers=headers)
    assert accepted.status_code == 200, accepted.text

    stale_deny = client.post(
        f"/engagements/{engagement.slug}/strategy/chat/messages/{message.id}/actions/deny",
        headers=headers,
        json={"action_index": 0},
    )
    assert stale_deny.status_code == 409, stale_deny.text
    db.expire_all()
    assert db.get(Suggestion, suggestion.id).status == SuggestionStatus.accepted
    assert (
        db.execute(select(WorkItem).where(WorkItem.engagement_id == engagement.id))
        .scalar_one()
        .title
        == "Review authentication behavior"
    )


def test_inflight_run_discards_proposals_if_engagement_is_archived(
    db: Session,
    engagement: Engagement,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = User(email=f"archive-race-{uuid.uuid4().hex[:6]}@example.test")
    db.add(user)
    db.commit()
    db.refresh(user)

    class ArchivingLLM:
        def invoke(self, _messages: object) -> SimpleNamespace:
            from app.db.session import SessionLocal

            with SessionLocal() as other:
                current = other.get(Engagement, engagement.id)
                assert current is not None
                current.status = EngagementStatus.archived
                other.commit()
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "situation_summary": "Proposal raced with archive.",
                        "facts": [],
                        "inferences": [],
                        "hypotheses": [],
                        "work_item_proposals": [
                            {
                                "proposal_key": "must-not-persist",
                                "title": "Must not persist",
                                "priority": "medium",
                                "executor_type": "analyst",
                                "finding_links": [],
                            }
                        ],
                        "strategy_revision_proposal": None,
                        "coverage_gaps": [],
                        "warnings": [],
                    }
                ),
                response_metadata={},
            )

    from app.services import engagement_strategist as service

    monkeypatch.setattr(service, "_resolve_model", lambda *_args: ("test", "fake"))
    monkeypatch.setattr(
        service,
        "resolve_for_user",
        lambda *_args, **_kwargs: SimpleNamespace(api_key="ephemeral", endpoint=None),
    )
    monkeypatch.setattr(service, "_make_chat_model", lambda *_args, **_kwargs: ArchivingLLM())

    with pytest.raises(RuntimeError, match="archived while strategist was running"):
        run_engagement_strategist(
            db,
            object(),
            engagement=engagement,
            acting_user_id=user.id,
            mode="recommend",
        )

    db.expire_all()
    assert (
        db.execute(
            select(Suggestion).where(Suggestion.engagement_id == engagement.id)
        ).scalar_one_or_none()
        is None
    )
    execution = db.execute(
        select(AgentExecution).where(AgentExecution.engagement_id == engagement.id)
    ).scalar_one()
    assert execution.status == AgentExecutionStatus.failed


def test_inflight_run_rejects_output_when_strategy_revision_changes(
    db: Session,
    engagement: Engagement,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = User(email=f"revision-race-{uuid.uuid4().hex[:6]}@example.test")
    revision_x = EngagementStrategyRevision(
        engagement_id=engagement.id,
        version=1,
        state=StrategyRevisionState.current,
        body="Strategy X",
        structured={},
        created_by_user_id=None,
    )
    db.add_all([user, revision_x])
    db.commit()
    db.refresh(user)
    db.refresh(revision_x)

    class RevisingLLM:
        def invoke(self, _messages: object) -> SimpleNamespace:
            from app.db.session import SessionLocal

            with SessionLocal() as other:
                old = other.get(EngagementStrategyRevision, revision_x.id)
                assert old is not None
                old.state = StrategyRevisionState.superseded
                other.add(
                    EngagementStrategyRevision(
                        engagement_id=engagement.id,
                        version=2,
                        state=StrategyRevisionState.current,
                        based_on_revision_id=old.id,
                        body="Strategy Y",
                        structured={},
                    )
                )
                other.commit()
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "situation_summary": "Reasoned over stale Strategy X.",
                        "facts": [],
                        "inferences": [],
                        "hypotheses": [],
                        "work_item_proposals": [
                            {
                                "proposal_key": "stale-x-proposal",
                                "title": "Must be discarded",
                                "priority": "medium",
                                "executor_type": "analyst",
                                "finding_links": [],
                            }
                        ],
                        "strategy_revision_proposal": None,
                        "coverage_gaps": [],
                        "warnings": [],
                    }
                ),
                response_metadata={},
            )

    from app.services import engagement_strategist as service

    monkeypatch.setattr(service, "_resolve_model", lambda *_args: ("test", "fake"))
    monkeypatch.setattr(
        service,
        "resolve_for_user",
        lambda *_args, **_kwargs: SimpleNamespace(api_key="ephemeral", endpoint=None),
    )
    monkeypatch.setattr(service, "_make_chat_model", lambda *_args, **_kwargs: RevisingLLM())

    with pytest.raises(ValueError, match="strategy changed while"):
        run_engagement_strategist(
            db,
            object(),
            engagement=engagement,
            acting_user_id=user.id,
            mode="recommend",
        )

    db.expire_all()
    assert (
        db.execute(
            select(Suggestion).where(Suggestion.engagement_id == engagement.id)
        ).scalar_one_or_none()
        is None
    )
    execution = db.execute(
        select(AgentExecution).where(AgentExecution.engagement_id == engagement.id)
    ).scalar_one()
    assert execution.status == AgentExecutionStatus.failed


def test_run_lock_transport_failure_marks_execution_failed(
    db: Session,
    engagement: Engagement,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = User(email=f"lock-failure-{uuid.uuid4().hex[:6]}@example.test")
    db.add(user)
    db.commit()
    db.refresh(user)

    class BrokenRedis:
        def set(self, *_args: object, **_kwargs: object) -> bool:
            raise ConnectionError("redis unavailable")

    from app.services import engagement_strategist as service

    monkeypatch.setattr(service, "_resolve_model", lambda *_args: ("test", "fake"))
    with pytest.raises(RuntimeError, match="lock unavailable"):
        run_engagement_strategist(
            db,
            BrokenRedis(),
            engagement=engagement,
            acting_user_id=user.id,
            mode="recommend",
        )

    db.expire_all()
    execution = db.execute(
        select(AgentExecution).where(AgentExecution.engagement_id == engagement.id)
    ).scalar_one()
    assert execution.status == AgentExecutionStatus.failed
    assert "redis unavailable" in (execution.error or "")
