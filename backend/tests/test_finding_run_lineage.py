"""Run→finding lineage: the FindingOrigin link table + filter.

Proves:
  - record_finding_origins writes an origin row linking a finding to its run
    (thread_id) + source tool;
  - re-recording the same (finding, thread, tool) is idempotent (unique
    constraint, ON CONFLICT DO NOTHING);
  - a finding can carry multiple origins (grouped findings fold items from
    multiple runs) — one row per distinct origin;
  - GET /runs/{thread_id}/findings returns exactly the findings that run
    produced.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import (
    Engagement,
    EngagementStatus,
    EngagementWorkState,
    Finding,
    FindingOrigin,
    FindingPhase,
    FindingStatus,
    Severity,
)

HDR = {"X-User-Id": "lineage@example.com"}


@pytest.fixture()
def client() -> TestClient:
    from app.main import app

    return TestClient(app)


@pytest.fixture()
def engagement(db: Session) -> Iterator[Engagement]:
    row = Engagement(
        name="Lineage",
        slug=f"lineage-{uuid.uuid4().hex[:8]}",
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


def _finding(db: Session, engagement: Engagement) -> Finding:
    row = Finding(
        engagement_id=engagement.id,
        title="DNS records for example.test",
        severity=Severity.info,
        phase=FindingPhase.osint,
        status=FindingStatus.validated,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_records_origin_and_filters_by_thread(
    db: Session, engagement: Engagement, client: TestClient
) -> None:
    from app.models.finding import record_finding_origins

    finding = _finding(db, engagement)
    thread = uuid.uuid4()

    n = record_finding_origins(
        db, finding_ids=[finding.id], thread_id=thread, source_tool="dns_lookup"
    )
    db.commit()
    assert n == 1

    origins = list(
        db.execute(
            select(FindingOrigin).where(FindingOrigin.thread_id == thread)
        ).scalars()
    )
    assert len(origins) == 1
    assert origins[0].finding_id == finding.id
    assert origins[0].source_tool == "dns_lookup"

    # Filter API returns exactly the findings this run produced.
    response = client.get(f"/runs/{thread}/findings", headers=HDR)
    assert response.status_code == 200
    rows = response.json()
    assert [row["id"] for row in rows] == [str(finding.id)]


def test_idempotent_re_record_is_a_noop(db: Session, engagement: Engagement) -> None:
    from app.models.finding import record_finding_origins

    finding = _finding(db, engagement)
    thread = uuid.uuid4()

    record_finding_origins(
        db, finding_ids=[finding.id], thread_id=thread, source_tool="dns_lookup"
    )
    db.commit()
    # Re-process the same run — must not duplicate.
    record_finding_origins(
        db, finding_ids=[finding.id], thread_id=thread, source_tool="dns_lookup"
    )
    db.commit()

    origins = list(
        db.execute(
            select(FindingOrigin).where(FindingOrigin.thread_id == thread)
        ).scalars()
    )
    assert len(origins) == 1


def test_finding_preserves_multiple_origins(
    db: Session, engagement: Engagement
) -> None:
    from app.models.finding import record_finding_origins

    # A grouped finding can fold items from multiple runs.
    finding = _finding(db, engagement)
    thread_a = uuid.uuid4()
    thread_b = uuid.uuid4()

    record_finding_origins(
        db, finding_ids=[finding.id], thread_id=thread_a, source_tool="subfinder"
    )
    db.commit()
    record_finding_origins(
        db, finding_ids=[finding.id], thread_id=thread_b, source_tool="crt_sh"
    )
    db.commit()

    origins = list(
        db.execute(
            select(FindingOrigin).where(FindingOrigin.finding_id == finding.id)
        ).scalars()
    )
    assert len(origins) == 2
    assert {row.thread_id for row in origins} == {thread_a, thread_b}
