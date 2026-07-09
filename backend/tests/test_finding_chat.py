"""Finding pane chatbot tests (Phase 2)."""
from __future__ import annotations

import uuid
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.main import app
from app.models import (
    AgentExecution,
    AuditLog,
    Conversation,
    ConversationMessage,
    Engagement,
    EngagementStatus,
    Finding,
    FindingPhase,
    FindingStatus,
    Severity,
)
from app.services.finding_chat import build_finding_dossier

HDR = {"X-User-Id": "finding-chat@example.com"}


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def engagement(db: Session) -> Iterator[Engagement]:
    eng = Engagement(
        name="Finding Chat",
        slug=f"finding-chat-{uuid.uuid4().hex[:8]}",
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
def finding(db: Session, engagement: Engagement) -> Finding:
    row = Finding(
        engagement_id=engagement.id,
        title="Exposed admin portal",
        severity=Severity.high,
        details={"evidence": "HTTP title: Admin", "url": "https://admin.example.test"},
        source_tool="http_probe",
        target="admin.example.test",
        phase=FindingPhase.vuln_scan,
        status=FindingStatus.pending_validation,
        summary="Admin portal exposed to the internet.",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


class _FakeResponse:
    content = "Review scope, confirm exposure, and capture evidence before reporting."
    response_metadata = {"usage": {"input_tokens": 10, "output_tokens": 6}}


class _FakeLLM:
    def __init__(self) -> None:
        self.messages: Any = None

    def invoke(self, messages: Any) -> _FakeResponse:
        self.messages = messages
        return _FakeResponse()


def _patch_llm(monkeypatch: pytest.MonkeyPatch) -> _FakeLLM:
    import app.services.finding_chat as chat

    fake = _FakeLLM()
    monkeypatch.setattr(chat, "default_provider_model", lambda: ("anthropic", "fake-1"))
    monkeypatch.setattr(
        chat,
        "resolve_for_user",
        lambda *_args, **_kwargs: SimpleNamespace(api_key="sk-test", endpoint=None),
    )
    monkeypatch.setattr(chat, "_make_chat_model", lambda *_args, **_kwargs: fake)
    return fake


def test_build_finding_dossier_includes_core_finding_context(
    db: Session, finding: Finding
) -> None:
    dossier = build_finding_dossier(db, finding)

    assert dossier["finding"]["id"] == str(finding.id)
    assert dossier["finding"]["title"] == "Exposed admin portal"
    assert dossier["finding"]["details"]["evidence"] == "HTTP title: Admin"
    assert dossier["activity"][0]["kind"] == "created"


def test_finding_chat_state_empty_before_conversation(
    client: TestClient, finding: Finding
) -> None:
    resp = client.get(f"/findings/{finding.id}/chat", headers=HDR)

    assert resp.status_code == 200
    assert resp.json() == {"conversation_id": None, "messages": []}


def test_ask_finding_chat_persists_bubbles_execution_and_audit(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
    db: Session,
    finding: Finding,
) -> None:
    fake = _patch_llm(monkeypatch)

    resp = client.post(
        f"/findings/{finding.id}/chat",
        headers=HDR,
        json={"message": "What should I do next?"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["conversation_id"]
    assert body["user_message"]["role"] == "user"
    assert body["assistant_message"]["role"] == "assistant"
    assert "Review scope" in body["assistant_message"]["content"]
    assert "Finding dossier JSON" in fake.messages[1][1]
    assert "Exposed admin portal" in fake.messages[1][1]

    conversation_id = uuid.UUID(body["conversation_id"])
    messages = list(
        db.execute(
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == conversation_id)
            .order_by(ConversationMessage.created_at.asc())
        ).scalars()
    )
    assert [m.role for m in messages] == ["user", "assistant"]

    execution = db.get(AgentExecution, uuid.UUID(body["execution_id"]))
    assert execution is not None
    assert execution.input["mode"] == "finding_chat"
    assert execution.input["finding_id"] == str(finding.id)
    assert execution.status.value == "completed"

    audit = db.execute(
        select(AuditLog).where(AuditLog.event_type == "finding.chat_asked")
    ).scalar_one()
    assert audit.payload["conversation_id"] == str(conversation_id)

    state = client.get(f"/findings/{finding.id}/chat", headers=HDR)
    assert state.status_code == 200
    assert [m["role"] for m in state.json()["messages"]] == ["user", "assistant"]


def test_finding_chat_reuses_latest_conversation(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
    db: Session,
    finding: Finding,
) -> None:
    _patch_llm(monkeypatch)

    first = client.post(
        f"/findings/{finding.id}/chat",
        headers=HDR,
        json={"message": "First"},
    ).json()
    second = client.post(
        f"/findings/{finding.id}/chat",
        headers=HDR,
        json={"message": "Second"},
    ).json()

    assert second["conversation_id"] == first["conversation_id"]
    count = db.scalar(
        select(func.count())
        .select_from(Conversation)
        .where(Conversation.finding_id == finding.id)
    )
    assert count == 1
