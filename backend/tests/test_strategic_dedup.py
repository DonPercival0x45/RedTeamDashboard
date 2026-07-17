"""Strategic-agent suggestion dedup guardrails.

The strategic watcher fires once per finding.created and proposes follow-up
tasks. Two guards stop it from stacking duplicate suggestions the analyst has
to keep dismissing:

  1. The proposal_key is now (tool, target, kind) — NOT finding_id — so the
     same follow-up work (e.g. "Resolve cwa.example") dedupes across the
     multiple findings that can reference one target, instead of one open
     suggestion per source finding.
  2. A DISMISSED suggestion with the same key blocks re-creation — the
     analyst already said no to this exact work.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.agents.strategic import StrategicAgent, _ProposedTask, _StrategicProposal
from app.models import (
    Engagement,
    EngagementStatus,
    EngagementWorkState,
    Finding,
    FindingPhase,
    FindingStatus,
    Severity,
    Suggestion,
    SuggestionStatus,
    TaskKind,
)


@pytest.fixture()
def engagement(db: Session):
    row = Engagement(
        name="Dedup",
        slug=f"dedup-{uuid.uuid4().hex[:8]}",
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


def _finding(db: Session, engagement: Engagement, target: str) -> Finding:
    f = Finding(
        engagement_id=engagement.id,
        title=f"finding-{target}",
        target=target,
        severity=Severity.info,
        status=FindingStatus.validated,
        phase=FindingPhase.osint,
    )
    db.add(f)
    db.commit()
    db.refresh(f)
    return f


def _proposal(target: str = "cwa.example") -> _StrategicProposal:
    return _StrategicProposal(
        summary="s",
        tasks=[
            _ProposedTask(
                title=f"Resolve {target}",
                rationale="r",
                tool="dns_lookup",
                target=target,
                kind=TaskKind.enum,
            )
        ],
    )


def _open_count(db: Session, engagement: Engagement) -> int:
    return len(
        db.execute(
            select(Suggestion).where(
                Suggestion.engagement_id == engagement.id,
                Suggestion.status == SuggestionStatus.open,
            )
        ).scalars().all()
    )


def test_same_work_across_findings_dedupes(db: Session, engagement: Engagement) -> None:
    """Two findings for the same target -> ONE suggestion, not one per finding."""
    agent = StrategicAgent()
    fa = _finding(db, engagement, "cwa.example")
    fb = _finding(db, engagement, "cwa.example")  # second finding, same target

    first = agent._persist_suggestions(
        db, engagement_id=engagement.id, finding_id=fa.id, proposal=_proposal()
    )
    assert len(first) == 1

    # Same (tool, target, kind) from a different finding must NOT create a
    # second suggestion — this is the duplicate-stacking the guardrail fixes.
    second = agent._persist_suggestions(
        db, engagement_id=engagement.id, finding_id=fb.id, proposal=_proposal()
    )
    assert second == []
    assert _open_count(db, engagement) == 1


def test_dismissed_suggestion_blocks_recreation(db: Session, engagement: Engagement) -> None:
    """An analyst-dismissed suggestion must not be re-created on the next finding."""
    agent = StrategicAgent()
    fa = _finding(db, engagement, "cwa.example")
    created = agent._persist_suggestions(
        db, engagement_id=engagement.id, finding_id=fa.id, proposal=_proposal()
    )
    assert len(created) == 1

    created[0].status = SuggestionStatus.dismissed
    db.commit()

    # A new finding arrives; the analyst already rejected this exact work.
    fb = _finding(db, engagement, "cwa.example")
    again = agent._persist_suggestions(
        db, engagement_id=engagement.id, finding_id=fb.id, proposal=_proposal()
    )
    assert again == []
    assert _open_count(db, engagement) == 0


def test_distinct_work_still_proposed(db: Session, engagement: Engagement) -> None:
    """Guardrail only blocks the SAME work — different targets still get proposed."""
    agent = StrategicAgent()
    fa = _finding(db, engagement, "cwa.example")
    agent._persist_suggestions(
        db, engagement_id=engagement.id, finding_id=fa.id, proposal=_proposal("cwa.example")
    )

    fb = _finding(db, engagement, "vpn.example")
    second = agent._persist_suggestions(
        db, engagement_id=engagement.id, finding_id=fb.id, proposal=_proposal("vpn.example")
    )
    assert len(second) == 1  # distinct target -> legitimately proposed
    assert _open_count(db, engagement) == 2
