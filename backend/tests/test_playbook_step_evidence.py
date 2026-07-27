"""Durable per-target playbook receipts and redacted evidence artifacts."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.main import app
from app.models import (
    CommandOutbox,
    CoverageRecord,
    Engagement,
    EngagementArchitecture,
    EngagementStatus,
    EngagementWorkState,
    EvidenceArtifact,
    Playbook,
    PlaybookRun,
    PlaybookRunStatus,
    PlaybookStepExecution,
    PlaybookStepExecutionStatus,
    ScopeItem,
    ScopeKind,
    User,
    UserRole,
)
from app.services import methodology as meth
from app.services.playbook import (
    StepResult,
    cancel_run,
    catalog,
    enqueue_run,
    execute_pending_run,
    load_seed_playbooks,
    recover_abandoned_runs,
    start_run,
)
from app.services.playbook.evidence import (
    MAX_EVIDENCE_BYTES,
    bounded_json,
    redact_json,
    redact_text,
)
from app.services.playbook.executor import substitute_scope


class ReceiptExecutor:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def run_step(
        self,
        *,
        tool_slug: str,
        args_template: Mapping[str, Any],
        scope_context: str,
    ) -> StepResult:
        args = substitute_scope(args_template, scope_context)
        self.calls.append({"tool": tool_slug, "target": scope_context, "args": args})
        if tool_slug == "whois":
            return StepResult(ok=False, error="token=should-not-persist whois timeout")
        return StepResult(
            ok=True,
            data={
                "target": scope_context,
                "api_key": "should-not-persist",
                "clean_result": True,
            },
        )


def _engagement_and_playbook(db: Session) -> tuple[Engagement, Playbook]:
    engagement = Engagement(
        name="Step evidence",
        slug=f"step-evidence-{uuid.uuid4().hex[:8]}",
        status=EngagementStatus.active,
        work_state=EngagementWorkState.active,
        intelligence_architecture=EngagementArchitecture.v3,
    )
    db.add(engagement)
    db.flush()
    meth.load_seed_catalog(db)
    meth.select_for_engagement(
        db,
        engagement_id=engagement.id,
        slug="osint-minimal",
        now=datetime(2026, 7, 25, tzinfo=UTC),
    )
    load_seed_playbooks(db)
    playbook = catalog.get_by_slug(db, "osint-passive-domain")
    assert playbook is not None
    return engagement, playbook


def test_run_persists_one_receipt_and_artifact_per_step_target(db: Session) -> None:
    engagement, playbook = _engagement_and_playbook(db)
    run = start_run(
        db,
        engagement=engagement,
        playbook=playbook,
        scope_subset=["example.com"],
        executor=ReceiptExecutor(),
    )

    receipts = list(
        db.execute(
            select(PlaybookStepExecution)
            .where(PlaybookStepExecution.playbook_run_id == run.id)
            .order_by(PlaybookStepExecution.sort_order)
        ).scalars()
    )
    assert len(receipts) == len(playbook.steps) == 5
    assert {receipt.target for receipt in receipts} == {"example.com"}
    assert all(receipt.attempt == 1 for receipt in receipts)
    assert all(receipt.completed_at is not None for receipt in receipts)
    assert all((receipt.duration_ms or 0) >= 0 for receipt in receipts)

    whois_receipt = next(row for row in receipts if row.tool_slug == "whois")
    assert whois_receipt.status is PlaybookStepExecutionStatus.failed
    assert whois_receipt.error == "token=[REDACTED] whois timeout"
    assert run.steps_failed == 1
    assert "should-not-persist" not in (run.last_error or "")
    coverage_notes = list(
        db.execute(
            select(CoverageRecord.notes).where(CoverageRecord.playbook_run_id == run.id)
        ).scalars()
    )
    assert "should-not-persist" not in str(coverage_notes)

    artifacts = list(
        db.execute(
            select(EvidenceArtifact).where(EvidenceArtifact.playbook_run_id == run.id)
        ).scalars()
    )
    assert len(artifacts) == len(receipts)
    assert all(artifact.redacted for artifact in artifacts)
    assert all(len(artifact.sha256) == 64 for artifact in artifacts)
    serialized = str([artifact.payload for artifact in artifacts])
    assert "should-not-persist" not in serialized
    assert "[REDACTED]" in serialized


def test_duplicate_target_attempts_preserve_history(db: Session) -> None:
    engagement, playbook = _engagement_and_playbook(db)
    run = start_run(
        db,
        engagement=engagement,
        playbook=playbook,
        scope_subset=["example.com", "example.com"],
        executor=ReceiptExecutor(),
    )
    attempts = list(
        db.execute(
            select(PlaybookStepExecution.attempt)
            .where(
                PlaybookStepExecution.playbook_run_id == run.id,
                PlaybookStepExecution.playbook_step_id == playbook.steps[0].id,
            )
            .order_by(PlaybookStepExecution.attempt)
        ).scalars()
    )
    assert attempts == [1, 2]


def test_nested_string_and_authorization_values_are_redacted() -> None:
    class SecretObject:
        def __str__(self) -> str:
            return "token=object-secret"

    redacted = redact_json(
        {
            "message": "curl -H 'Authorization: Bearer super-secret' https://x",
            "json_message": '{"api_key":"json-secret"}',
            "nested": [
                "postgresql://user:db-password@example/db",
                SecretObject(),
            ],
        }
    )
    rendered = str(redacted)
    assert "super-secret" not in rendered
    assert "db-password" not in rendered
    assert "json-secret" not in rendered
    assert "object-secret" not in rendered
    assert "[REDACTED]" in rendered
    assert redact_text("Authorization: Basic dXNlcjpwYXNz") == ("Authorization=[REDACTED]")


def test_evidence_payload_is_bounded_after_redaction() -> None:
    payload, digest, size, truncated = bounded_json(
        {"secret": "hidden", "items": ["x" * MAX_EVIDENCE_BYTES]},
        max_bytes=MAX_EVIDENCE_BYTES,
    )
    assert truncated is True
    assert size > MAX_EVIDENCE_BYTES
    assert len(digest) == 64
    assert payload["_truncated"] is True
    assert "hidden" not in payload["_preview_json"]


def test_expired_worker_lease_fails_run_and_inflight_receipt(db: Session) -> None:
    engagement, playbook = _engagement_and_playbook(db)
    old = datetime(2026, 7, 25, 10, 0, tzinfo=UTC)
    recovered_at = datetime(2026, 7, 25, 11, 0, tzinfo=UTC)
    run = enqueue_run(
        db,
        engagement=engagement,
        playbook=playbook,
        scope_subset=["example.com"],
    )
    run.status = PlaybookRunStatus.running
    run.started_at = old
    run.worker_id = "dead-worker"
    run.worker_heartbeat_at = old
    receipt = PlaybookStepExecution(
        playbook_run_id=run.id,
        playbook_step_id=playbook.steps[0].id,
        sort_order=playbook.steps[0].sort_order,
        tool_slug=playbook.steps[0].tool_slug,
        target="example.com",
        transport="internal",
        attempt=1,
        status=PlaybookStepExecutionStatus.running,
        arguments={"domain": "example.com"},
        started_at=old,
    )
    db.add(receipt)
    db.flush()

    recovered = recover_abandoned_runs(
        db,
        stale_before=datetime(2026, 7, 25, 10, 30, tzinfo=UTC),
        now=recovered_at,
    )

    assert [row.id for row in recovered] == [run.id]
    assert run.status is PlaybookRunStatus.failed
    assert run.worker_id is None
    assert run.worker_heartbeat_at is None
    assert "was not retried" in (run.last_error or "")
    assert receipt.status is PlaybookStepExecutionStatus.failed
    assert receipt.completed_at == recovered_at
    assert "outcome is unknown" in (receipt.error or "")


def test_cancellation_during_tool_preserves_completed_receipt(db: Session) -> None:
    engagement, playbook = _engagement_and_playbook(db)
    run = enqueue_run(
        db,
        engagement=engagement,
        playbook=playbook,
        scope_subset=["example.com"],
    )
    run.status = PlaybookRunStatus.running
    run.started_at = datetime.now(tz=UTC)
    db.commit()

    class CancellingExecutor(ReceiptExecutor):
        def run_step(
            self,
            *,
            tool_slug: str,
            args_template: Mapping[str, Any],
            scope_context: str,
        ) -> StepResult:
            # Model an analyst cancellation that commits while the external
            # call is in flight, before the main session writes its result.
            other = SessionLocal()
            try:
                cancel_run(other, run_id=run.id, reason="cancel race test")
                other.commit()
            finally:
                other.close()
            return super().run_step(
                tool_slug=tool_slug,
                args_template=args_template,
                scope_context=scope_context,
            )

    executor = CancellingExecutor()

    def progress_commit(_phase: str) -> bool:
        db.commit()
        return True

    result = execute_pending_run(
        db,
        run_id=run.id,
        executor=executor,
        progress_commit=progress_commit,
    )

    assert result.status is PlaybookRunStatus.cancelled
    assert len(executor.calls) == 1
    receipt = db.execute(
        select(PlaybookStepExecution).where(PlaybookStepExecution.playbook_run_id == run.id)
    ).scalar_one()
    assert receipt.status is PlaybookStepExecutionStatus.succeeded
    assert receipt.error is None
    db.execute(delete(CommandOutbox).where(CommandOutbox.engagement_id == engagement.id))
    db.commit()


def test_authoritative_plan_hash_is_persisted_and_stale_preview_rejected(
    db: Session,
) -> None:
    engagement, playbook = _engagement_and_playbook(db)
    user = User(
        id=uuid.uuid4(),
        email=f"planner-{uuid.uuid4().hex[:6]}@example.com",
        display_name="Plan reviewer",
        role=UserRole.user,
        is_active=True,
    )
    db.add_all(
        [
            user,
            ScopeItem(
                engagement_id=engagement.id,
                kind=ScopeKind.domain,
                value="example.com",
            ),
        ]
    )
    db.commit()
    client = TestClient(app)
    headers = {"X-User-Id": user.email}
    payload = {
        "playbook_slug": playbook.slug,
        "playbook_version": playbook.version,
        "scope_subset": ["example.com"],
        "executor": "internal",
    }

    preview = client.post(
        f"/engagements/{engagement.slug}/playbook-runs/plan",
        headers=headers,
        json=payload,
    )
    assert preview.status_code == 200, preview.text
    plan = preview.json()
    assert len(plan["plan_sha256"]) == 64
    assert plan["minimum_calls"] == 5
    assert plan["scope_subset"] == ["example.com"]
    assert {step["transport"] for step in plan["steps"]} == {"internal"}
    assert all(len(step["arguments_sha256"]) == 64 for step in plan["steps"])
    assert all("targets" not in step for step in plan["steps"])

    first_step = playbook.steps[0]
    original_args = dict(first_step.args_template or {})
    first_step.args_template = {**original_args, "changed_option": True}
    db.commit()
    changed = client.post(
        f"/engagements/{engagement.slug}/playbook-runs/plan",
        headers=headers,
        json=payload,
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["plan_sha256"] != plan["plan_sha256"]
    first_step.args_template = original_args
    db.commit()

    created = client.post(
        f"/engagements/{engagement.slug}/playbook-runs",
        headers=headers,
        json={**payload, "plan_sha256": plan["plan_sha256"]},
    )
    assert created.status_code == 202, created.text
    assert created.json()["plan_sha256"] == plan["plan_sha256"]
    run = db.get(PlaybookRun, uuid.UUID(created.json()["id"]))
    assert run is not None
    assert run.plan_snapshot == plan

    stale = client.post(
        f"/engagements/{engagement.slug}/playbook-runs",
        headers=headers,
        json={**payload, "plan_sha256": "0" * 64},
    )
    assert stale.status_code == 409
    assert "plan changed" in stale.text.lower()


def test_run_detail_exposes_receipts_and_fetches_evidence(db: Session) -> None:
    engagement, playbook = _engagement_and_playbook(db)
    user = User(
        id=uuid.uuid4(),
        email=f"receipt-{uuid.uuid4().hex[:6]}@example.com",
        display_name="Receipt reviewer",
        role=UserRole.user,
        is_active=True,
    )
    db.add(user)
    db.flush()
    run = start_run(
        db,
        engagement=engagement,
        playbook=playbook,
        scope_subset=["example.com"],
        executor=ReceiptExecutor(),
        requested_by=user.id,
    )
    db.commit()

    client = TestClient(app)
    headers = {"X-User-Id": user.email}
    detail = client.get(f"/playbook-runs/{run.id}", headers=headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["engagement_slug"] == engagement.slug
    rows = detail.json()["step_executions"]
    assert len(rows) == 5
    assert rows[0]["target"] == "example.com"
    assert rows[0]["evidence"]["redacted"] is True

    evidence = client.get(
        f"/evidence-artifacts/{rows[0]['evidence']['id']}",
        headers=headers,
    )
    assert evidence.status_code == 200, evidence.text
    body = evidence.json()
    assert body["playbook_run_id"] == str(run.id)
    assert body["target"] == "example.com"
    assert body["redacted"] is True

    # This test commits so TestClient's independent session can read the rows.
    # Remove pending delivery rows explicitly rather than leaking them to tests
    # that deliberately exercise global outbox recovery.
    db.execute(delete(CommandOutbox).where(CommandOutbox.engagement_id == engagement.id))
    db.commit()
