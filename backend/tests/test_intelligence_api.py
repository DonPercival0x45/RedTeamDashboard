"""Per-engagement v3 activation and analyst on-demand intelligence API."""
from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.main import app
from app.models import (
    AgentExecution,
    AgentExecutionStatus,
    AgentPromptMode,
    AgentTrigger,
    AuditLog,
    Engagement,
    EngagementArchitecture,
    EngagementStatus,
    MemoryElement,
    MemoryKind,
)
from app.schemas.intelligence import AnalysisOutput, ProposedFact
from app.services import methodology as methodology_service


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def cleanup_slugs(db: Session) -> Iterator[list[str]]:
    slugs: list[str] = []
    yield slugs
    for slug in slugs:
        engagement_id = db.scalar(select(Engagement.id).where(Engagement.slug == slug))
        if engagement_id is not None:
            db.execute(text("SELECT flush_engagement(:id)"), {"id": engagement_id})
            db.commit()


def _headers() -> dict[str, str]:
    return {"X-User-Id": "v3-intelligence-api@example.com"}


def _seed_methodologies(db: Session) -> None:
    methodology_service.load_seed_catalog(db)
    db.commit()


def _create(
    client: TestClient,
    cleanup_slugs: list[str],
    *,
    architecture: str = "legacy",
) -> dict[str, Any]:
    slug = f"intel-{uuid.uuid4().hex[:8]}"
    body: dict[str, Any] = {
        "name": "Intelligence API",
        "slug": slug,
        "intelligence_architecture": architecture,
    }
    if architecture == "v3":
        body["methodology_slug"] = "osint-minimal"
    response = client.post("/engagements", json=body, headers=_headers())
    assert response.status_code == 201, response.text
    cleanup_slugs.append(slug)
    return response.json()


class FakeLLM:
    def __init__(self, result: Any) -> None:
        self.result = result

    def with_structured_output(self, _schema: type) -> FakeLLM:
        return self

    def invoke(self, _messages: Any) -> Any:
        return self.result


class RaisingLLM:
    def with_structured_output(self, _schema: type) -> RaisingLLM:
        return self

    def invoke(self, _messages: Any) -> Any:
        raise RuntimeError("injected model failure")


def test_default_creation_remains_legacy(
    client: TestClient, cleanup_slugs: list[str]
) -> None:
    row = _create(client, cleanup_slugs)
    assert row["intelligence_architecture"] == "legacy"
    assert row["converted_to_v3_at"] is None


def test_v3_creation_requires_and_atomically_selects_methodology(
    client: TestClient, db: Session, cleanup_slugs: list[str]
) -> None:
    slug = f"missing-method-{uuid.uuid4().hex[:8]}"
    rejected = client.post(
        "/engagements",
        json={
            "name": "Missing methodology",
            "slug": slug,
            "intelligence_architecture": "v3",
        },
        headers=_headers(),
    )
    assert rejected.status_code == 422

    unknown_slug = f"unknown-method-{uuid.uuid4().hex[:8]}"
    unknown = client.post(
        "/engagements",
        json={
            "name": "Unknown methodology",
            "slug": unknown_slug,
            "intelligence_architecture": "v3",
            "methodology_slug": "does-not-exist",
        },
        headers=_headers(),
    )
    assert unknown.status_code == 400
    assert db.scalar(select(Engagement).where(Engagement.slug == unknown_slug)) is None

    _seed_methodologies(db)
    row = _create(client, cleanup_slugs, architecture="v3")
    assert row["intelligence_architecture"] == "v3"
    assert row["methodology_id"] is not None
    assert row["methodology_selected_at"] is not None


def test_conversion_is_attributed_atomic_and_idempotent(
    client: TestClient, db: Session, cleanup_slugs: list[str]
) -> None:
    _seed_methodologies(db)
    row = _create(client, cleanup_slugs)
    body = {
        "methodology_slug": "osint-minimal",
        "reason": "Move this active engagement to the shared v3 brain",
    }
    converted = client.post(
        f"/engagements/{row['slug']}/intelligence/convert",
        json=body,
        headers=_headers(),
    )
    assert converted.status_code == 200, converted.text
    payload = converted.json()
    assert payload["intelligence_architecture"] == "v3"
    assert payload["already_converted"] is False
    assert len(payload["seeded_memory_element_ids"]) == 1

    repeated = client.post(
        f"/engagements/{row['slug']}/intelligence/convert",
        json=body,
        headers=_headers(),
    )
    assert repeated.status_code == 200
    assert repeated.json()["already_converted"] is True
    assert repeated.json()["seeded_memory_element_ids"] == []

    db.expire_all()
    engagement = db.scalar(select(Engagement).where(Engagement.slug == row["slug"]))
    assert engagement is not None
    assert engagement.intelligence_architecture is EngagementArchitecture.v3
    assert engagement.converted_to_v3_at is not None
    assert db.scalar(
        select(func.count(MemoryElement.id)).where(
            MemoryElement.engagement_id == engagement.id,
            MemoryElement.kind == MemoryKind.decision,
        )
    ) == 1
    assert db.scalar(
        select(func.count(AuditLog.id)).where(
            AuditLog.engagement_id == engagement.id,
            AuditLog.event_type == "engagement.intelligence_converted",
        )
    ) == 1

    engagement.status = EngagementStatus.archived
    db.commit()
    durable_retry = client.post(
        f"/engagements/{row['slug']}/intelligence/convert",
        json=body,
        headers=_headers(),
    )
    assert durable_retry.status_code == 200
    assert durable_retry.json()["already_converted"] is True


def test_on_demand_analysis_uses_manual_trigger_and_persists_output(
    client: TestClient,
    db: Session,
    cleanup_slugs: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_methodologies(db)
    row = _create(client, cleanup_slugs, architecture="v3")
    llm = FakeLLM(
        AnalysisOutput(
            proposed_facts=[
                ProposedFact(summary="Analyst-requested correlation", confidence=0.8)
            ]
        )
    )

    resolver_kwargs: dict[str, Any] = {}

    def resolve(*_args: Any, **kwargs: Any) -> tuple[Any, str, str]:
        resolver_kwargs.update(kwargs)
        return llm, "test-provider", "test-model"

    monkeypatch.setattr("app.api.intelligence.resolve_llm_for_mode", resolve)
    response = client.post(
        f"/engagements/{row['slug']}/intelligence/runs",
        json={"mode": "analysis"},
        headers=_headers(),
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["mode"] == "analysis"
    assert payload["status"] == "completed"
    assert payload["parsed"]["proposed_facts"][0]["summary"] == (
        "Analyst-requested correlation"
    )

    execution = db.get(AgentExecution, uuid.UUID(payload["execution_id"]))
    assert execution is not None
    assert execution.status is AgentExecutionStatus.completed
    assert execution.trigger is AgentTrigger.manual
    assert execution.model_provider == "test-provider"
    assert execution.model_name == "test-model"
    assert resolver_kwargs["user_id"] == uuid.UUID(execution.input["acting_user_id"])
    assert db.scalar(
        select(func.count(MemoryElement.id)).where(
            MemoryElement.engagement_id == execution.engagement_id,
            MemoryElement.kind == MemoryKind.fact,
        )
    ) == 1
    audit = db.scalar(
        select(AuditLog).where(
            AuditLog.engagement_id == execution.engagement_id,
            AuditLog.event_type == "intelligence.invoked",
        )
    )
    assert audit is not None
    assert audit.actor_id == execution.input["acting_user_id"]


def test_on_demand_model_failure_is_recorded_without_partial_memory(
    client: TestClient,
    db: Session,
    cleanup_slugs: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_methodologies(db)
    row = _create(client, cleanup_slugs, architecture="v3")

    def resolve(*_args: Any, **_kwargs: Any) -> tuple[Any, str, str]:
        return RaisingLLM(), "test-provider", "test-model"

    monkeypatch.setattr("app.api.intelligence.resolve_llm_for_mode", resolve)
    response = client.post(
        f"/engagements/{row['slug']}/intelligence/runs",
        json={"mode": "analysis"},
        headers=_headers(),
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "failed"
    assert "injected model failure" in payload["error"]

    execution = db.get(AgentExecution, uuid.UUID(payload["execution_id"]))
    assert execution is not None
    assert execution.status is AgentExecutionStatus.failed
    assert db.scalar(
        select(func.count(MemoryElement.id)).where(
            MemoryElement.engagement_id == execution.engagement_id
        )
    ) == 0


def test_on_demand_intelligence_rejects_legacy_engagement(
    client: TestClient, cleanup_slugs: list[str]
) -> None:
    row = _create(client, cleanup_slugs)
    response = client.post(
        f"/engagements/{row['slug']}/intelligence/runs",
        json={"mode": AgentPromptMode.strategy.value},
        headers=_headers(),
    )
    assert response.status_code == 409
    assert "must be converted" in response.text
