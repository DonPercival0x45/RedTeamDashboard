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
    ScopeItem,
    Severity,
    Task,
)
from app.services.finding_chat import (
    accept_chat_action,
    build_finding_dossier,
    deny_chat_action,
    summarize_finding_chat,
)

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
    content = """{
      "answer": "Review scope, confirm exposure, and capture evidence before reporting.",
      "actions": [
        {
          "type": "run_tool",
          "title": "Fingerprint admin portal",
          "description": "Run built-in service detection against the target.",
          "params": {
            "tool": "service_detect",
            "target": "admin.example.test",
            "task_kind": "enum",
            "args": {}
          }
        }
      ]
    }"""
    response_metadata = {"usage": {"input_tokens": 10, "output_tokens": 6}}


class _FakePlainResponse:
    content = "Plain prose recommendation with no JSON."
    response_metadata = {"usage": {"input_tokens": 3, "output_tokens": 4}}


class _FakeLLM:
    def __init__(self) -> None:
        self.messages: Any = None

    def invoke(self, messages: Any) -> _FakeResponse:
        self.messages = messages
        return _FakeResponse()


class _FakePlainLLM(_FakeLLM):
    def invoke(self, messages: Any) -> _FakePlainResponse:
        self.messages = messages
        return _FakePlainResponse()


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


def _patch_plain_llm(monkeypatch: pytest.MonkeyPatch) -> _FakePlainLLM:
    import app.services.finding_chat as chat

    fake = _FakePlainLLM()
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
    actions = body["assistant_message"]["action_payload"]["actions"]
    assert actions[0]["type"] == "run_tool"
    assert actions[0]["status"] == "proposed"
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
        select(AuditLog).where(
            AuditLog.engagement_id == finding.engagement_id,
            AuditLog.event_type == "finding.chat_asked",
        )
    ).scalar_one()
    assert audit.payload["conversation_id"] == str(conversation_id)

    state = client.get(f"/findings/{finding.id}/chat", headers=HDR)
    assert state.status_code == 200
    assert [m["role"] for m in state.json()["messages"]] == ["user", "assistant"]


def test_plain_prose_chat_response_gets_safe_agent_action(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
    finding: Finding,
) -> None:
    _patch_plain_llm(monkeypatch)

    resp = client.post(
        f"/findings/{finding.id}/chat",
        headers=HDR,
        json={"message": "What next?"},
    )

    assert resp.status_code == 200, resp.text
    assistant = resp.json()["assistant_message"]
    assert assistant["content"] == "Plain prose recommendation with no JSON."
    actions = assistant["action_payload"]["actions"]
    assert actions[0]["type"] == "run_tool"
    assert actions[0]["status"] == "proposed"
    assert actions[0]["params"]["tool"] == "service_detect"


def test_accept_finding_chat_run_tool_action_dispatches_task(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
    db: Session,
    finding: Finding,
) -> None:
    _patch_llm(monkeypatch)
    chat = client.post(
        f"/findings/{finding.id}/chat",
        headers=HDR,
        json={"message": "Suggest tags"},
    ).json()
    message_id = chat["assistant_message"]["id"]

    resp = client.post(
        f"/findings/{finding.id}/chat/messages/{message_id}/actions/accept",
        headers=HDR,
        json={"action_index": 0},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["action_type"] == "run_tool"
    assert body["result"]["tool"] == "service_detect"
    assert body["result"]["target"] == "admin.example.test"
    assert body["result"]["dispatched"] is True
    assert body["result"]["task_id"]
    assert body["message"]["action_payload"]["actions"][0]["status"] == "accepted"

    task = db.get(Task, uuid.UUID(body["result"]["task_id"]))
    assert task is not None
    assert task.status.value == "dispatched"
    assert task.payload["tool"] == "service_detect"
    audit = db.execute(
        select(AuditLog).where(
            AuditLog.engagement_id == finding.engagement_id,
            AuditLog.event_type == "finding.chat_action.accepted",
        )
    ).scalar_one()
    assert audit.payload["action_type"] == "run_tool"


def test_clear_finding_chat_deletes_current_user_conversation(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
    db: Session,
    finding: Finding,
) -> None:
    _patch_llm(monkeypatch)
    created = client.post(
        f"/findings/{finding.id}/chat",
        headers=HDR,
        json={"message": "Start"},
    )
    assert created.status_code == 200
    conversation_id = uuid.UUID(created.json()["conversation_id"])

    resp = client.delete(f"/findings/{finding.id}/chat", headers=HDR)

    assert resp.status_code == 204
    assert db.get(Conversation, conversation_id) is None
    state = client.get(f"/findings/{finding.id}/chat", headers=HDR)
    assert state.json() == {"conversation_id": None, "messages": []}
    audit = db.execute(
        select(AuditLog).where(
            AuditLog.engagement_id == finding.engagement_id,
            AuditLog.event_type == "finding.chat_cleared",
        )
    ).scalar_one()
    assert audit.payload["conversation_count"] == 1


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


def _message_with_action(
    db: Session, conversation: Conversation, action: dict
) -> ConversationMessage:
    m = ConversationMessage(
        conversation_id=conversation.id,
        role="assistant",
        content="proposing an action",
        action_payload={"actions": [action]},
    )
    db.add(m)
    db.commit()
    db.refresh(m)
    return m


def _conversation(db: Session, finding: Finding) -> Conversation:
    c = Conversation(
        engagement_id=finding.engagement_id,
        finding_id=finding.id,
        created_by_user_id=None,
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def test_deny_chat_action_marks_denied_and_is_idempotent(
    db: Session, finding: Finding
) -> None:
    conv = _conversation(db, finding)
    msg = _message_with_action(
        db,
        conv,
        {
            "type": "run_tool",
            "title": "probe",
            "params": {"tool": "httpx_probe", "target": "x.test", "task_kind": "enum"},
            "status": "proposed",
        },
    )
    typ, result = deny_chat_action(db, message=msg, action_index=0)
    assert typ == "run_tool" and result == {"denied": True}
    db.commit()
    db.refresh(msg)
    assert msg.action_payload["actions"][0]["status"] == "denied"
    # denying again is rejected (already terminal)
    import pytest as _pytest

    with _pytest.raises(ValueError):
        deny_chat_action(db, message=msg, action_index=0)


def test_accept_add_scope_creates_found_scope_item(
    db: Session, finding: Finding
) -> None:
    conv = _conversation(db, finding)
    msg = _message_with_action(
        db,
        conv,
        {
            "type": "add_scope",
            "title": "Add out-of-scope host",
            "params": {"value": "mail.outside.test", "kind": "domain"},
            "status": "proposed",
        },
    )
    typ, result = accept_chat_action(
        db, finding=finding, message=msg, action_index=0, acting_user_id=None
    )
    assert typ == "add_scope"
    assert result["source"] == "found"
    item = db.get(ScopeItem, uuid.UUID(result["scope_item_id"]))
    assert item is not None
    assert item.value == "mail.outside.test"
    assert item.source == "found"  # highlights as discovered-during-engagement (#94)
    db.commit()
    db.refresh(msg)
    assert msg.action_payload["actions"][0]["status"] == "accepted"


class _RaisingLLM:
    """LLM stub whose invoke always fails — exercises the summarize fallback."""

    def invoke(self, *_a, **_kw):  # noqa: ANN001
        raise RuntimeError("no key configured")


def test_summarize_finding_chat_falls_back_and_audits_without_llm(
    monkeypatch: pytest.MonkeyPatch, db: Session, finding: Finding
) -> None:
    conv = _conversation(db, finding)
    for role, body in (("user", "what next"), ("assistant", "try a port scan")):
        db.add(
            ConversationMessage(conversation_id=conv.id, role=role, content=body)
        )
    db.commit()

    monkeypatch.setattr(
        "app.services.finding_chat._make_chat_model", lambda *a, **k: _RaisingLLM()
    )
    summary, n = summarize_finding_chat(
        db,
        redis_client=None,
        finding=finding,
        conversation=conv,
        acting_user_id=None,
    )
    assert n == 2
    assert "2 message" in summary  # deterministic fallback digest
    audit = db.execute(
        select(AuditLog).where(AuditLog.event_type == "finding.chat_summarized")
    ).scalar_one()
    assert audit.payload["summary"] == summary
    assert audit.payload["finding_id"] == str(finding.id)
