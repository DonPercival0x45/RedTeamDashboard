"""Worker-owned finding lineage and terminal Task truth."""
from __future__ import annotations

import uuid
from collections.abc import Iterator
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models import (
    Engagement,
    FindingOrigin,
    OwnerEligibility,
    Task,
    TaskKind,
    TaskStatus,
    WorkItem,
    WorkItemExecutor,
    WorkItemFinding,
    WorkItemFindingRelationship,
    WorkItemPriority,
    WorkItemStatus,
)
from app.worker.runner import RunRunner


@pytest.fixture()
def engagement(db: Session) -> Iterator[Engagement]:
    row = Engagement(
        name="Worker truth",
        slug=f"worker-truth-{uuid.uuid4().hex[:8]}",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    try:
        yield row
    finally:
        db.execute(text("SELECT flush_engagement(:id)"), {"id": row.id})
        db.commit()


def _runner() -> RunRunner:
    return RunRunner(
        graph=object(), redis_client=MagicMock(), session_factory=SessionLocal
    )


def test_worker_records_ungrouped_origin(
    db: Session, engagement: Engagement
) -> None:
    thread_id = uuid.uuid4()
    row = _runner()._persist_finding(
        engagement.id,
        str(thread_id),
        {
            "tool": "custom_observer",
            "target": "example.test",
            "title": "Observed example.test",
            "data": {"value": "seen"},
        },
    )

    origin = db.execute(
        select(FindingOrigin).where(FindingOrigin.finding_id == row.id)
    ).scalar_one()
    assert origin.thread_id == thread_id
    assert origin.source_tool == "custom_observer"


def test_grouped_finding_retains_multiple_run_origins(
    db: Session, engagement: Engagement
) -> None:
    runner = _runner()
    thread_a = uuid.uuid4()
    thread_b = uuid.uuid4()
    payload = {
        "tool": "subfinder",
        "args": {"domain": "example.test"},
        "target": "example.test",
        "data": {"subdomains": ["www.example.test"]},
    }

    first = runner._persist_finding(engagement.id, str(thread_a), payload)
    second = runner._persist_finding(engagement.id, str(thread_b), payload)

    assert first.id == second.id
    origins = list(
        db.execute(
            select(FindingOrigin).where(FindingOrigin.finding_id == first.id)
        ).scalars()
    )
    assert {origin.thread_id for origin in origins} == {thread_a, thread_b}


def test_terminal_completion_updates_task_and_links_work_item(
    db: Session, engagement: Engagement
) -> None:
    runner = _runner()
    thread_id = uuid.uuid4()
    finding = runner._persist_finding(
        engagement.id,
        str(thread_id),
        {"tool": "custom_observer", "title": "Produced row"},
    )
    work_item = WorkItem(
        engagement_id=engagement.id,
        title="Collect evidence",
        acceptance_criteria=[],
        status=WorkItemStatus.in_progress,
        priority=WorkItemPriority.medium,
        executor_type=WorkItemExecutor.tactical,
    )
    db.add(work_item)
    db.flush()
    task = Task(
        engagement_id=engagement.id,
        work_item_id=work_item.id,
        title="Collect evidence",
        kind=TaskKind.enum,
        owner_eligibility=OwnerEligibility.agent,
        status=TaskStatus.running,
        payload={"tool": "custom_observer", "target": "example.test"},
        run_id=thread_id,
    )
    db.add(task)
    db.commit()

    runner._finalize_task_for_run(str(thread_id), succeeded=True)
    db.expire_all()

    persisted = db.get(Task, task.id)
    assert persisted is not None
    assert persisted.status == TaskStatus.completed
    assert persisted.completed_at is not None
    link = db.execute(
        select(WorkItemFinding).where(
            WorkItemFinding.work_item_id == work_item.id,
            WorkItemFinding.finding_id == finding.id,
            WorkItemFinding.relationship
            == WorkItemFindingRelationship.produced_by,
        )
    ).scalar_one()
    assert link.finding_id == finding.id

    # Redelivered terminal events remain idempotent.
    runner._finalize_task_for_run(str(thread_id), succeeded=True)
    assert db.execute(
        select(WorkItemFinding).where(
            WorkItemFinding.work_item_id == work_item.id,
            WorkItemFinding.finding_id == finding.id,
        )
    ).scalars().all() == [link]


def test_terminal_error_marks_task_failed_and_links_partial_findings(
    db: Session, engagement: Engagement
) -> None:
    thread_id = uuid.uuid4()
    runner = _runner()
    finding = runner._persist_finding(
        engagement.id,
        str(thread_id),
        {"tool": "custom_observer", "title": "Partial result"},
    )
    work_item = WorkItem(
        engagement_id=engagement.id,
        title="Failing work",
        acceptance_criteria=[],
        status=WorkItemStatus.in_progress,
        priority=WorkItemPriority.medium,
        executor_type=WorkItemExecutor.tactical,
    )
    db.add(work_item)
    db.flush()
    task = Task(
        engagement_id=engagement.id,
        work_item_id=work_item.id,
        title="Failing run",
        kind=TaskKind.scan,
        owner_eligibility=OwnerEligibility.agent,
        status=TaskStatus.running,
        payload={"tool": "portscan", "target": "example.test"},
        run_id=thread_id,
    )
    db.add(task)
    db.commit()

    runner._finalize_task_for_run(str(thread_id), succeeded=False)
    db.expire_all()
    persisted = db.get(Task, task.id)
    assert persisted is not None
    assert persisted.status == TaskStatus.failed
    assert persisted.completed_at is not None
    link = db.execute(
        select(WorkItemFinding).where(
            WorkItemFinding.work_item_id == work_item.id,
            WorkItemFinding.finding_id == finding.id,
            WorkItemFinding.relationship == WorkItemFindingRelationship.produced_by,
        )
    ).scalar_one()
    assert link.finding_id == finding.id
