"""Tests for the tool-invocation entity pipeline (v0.16.0).

Covers:
  - ``_build_entities`` groups discovered entities by type, dedupes,
    preserves order, and passes through any free-form type.
  - ``build_args_env`` now serialises ``entities`` into the
    ``RTD_ARGS_JSON`` payload the sandbox delivers to the entrypoint
    (additive — existing tools that ignore it are unaffected).
"""
from __future__ import annotations

import base64
import json
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy.orm import Session

from app.models import Engagement, Entity, Finding
from app.services.sandbox_runner import SandboxRequest, build_args_env
from app.services.tool_invocation import _build_entities


@pytest.fixture()
def engagement(db: Session) -> Iterator[Engagement]:
    eng = Engagement(
        name=f"entity-pipeline-{uuid.uuid4().hex[:8]}",
        slug=f"entity-pipeline-{uuid.uuid4().hex[:8]}",
    )
    db.add(eng)
    db.commit()
    db.refresh(eng)
    try:
        yield eng
    finally:
        # entities FK is ON DELETE CASCADE, but delete them explicitly
        # so this is robust even if the constraint isn't enforced.
        db.query(Entity).filter(Entity.engagement_id == eng.id).delete(
            synchronize_session=False
        )
        db.query(Finding).filter(Finding.engagement_id == eng.id).delete(
            synchronize_session=False
        )
        db.query(Engagement).filter(Engagement.id == eng.id).delete(
            synchronize_session=False
        )
        db.commit()


def _entity(engagement_id, type_: str, value: str) -> Entity:
    return Entity(
        engagement_id=engagement_id,
        type=type_,
        value=value,
        source_tool="test",
    )


def _finding(engagement_id, target: str | None, details: dict) -> Finding:
    return Finding(
        engagement_id=engagement_id,
        title=f"finding-{target or details}",
        target=target,
        details=details,
    )


def test_build_entities_groups_dedupes_and_preserves_order(
    db: Session, engagement: Engagement
) -> None:
    db.add_all(
        [
            _entity(engagement.id, "email", "alice@contoso.com"),
            _entity(engagement.id, "email", "bob@contoso.com"),
            _entity(engagement.id, "email", "alice@contoso.com"),  # dup
            _entity(engagement.id, "host", "mail.contoso.com"),
            _entity(engagement.id, "ip", "203.0.113.10"),
            # free-form / unknown type must still flow through under its key
            _entity(engagement.id, "person", "John Smith"),
        ]
    )
    db.commit()

    grouped = _build_entities(db, engagement)

    assert grouped["email"] == ["alice@contoso.com", "bob@contoso.com"]
    assert grouped["host"] == ["mail.contoso.com"]
    assert grouped["ip"] == ["203.0.113.10"]
    assert grouped["person"] == ["John Smith"]
    # a type with no rows isn't fabricated as an empty list
    assert "domain" not in grouped


def test_build_entities_empty_when_none(db: Session, engagement: Engagement) -> None:
    assert _build_entities(db, engagement) == {}


def test_build_entities_merges_derived_and_stored(
    db: Session, engagement: Engagement
) -> None:
    """The entity payload mirrors the entities view: emails derived from
    findings (the primary source) merged with stored (imported) entities,
    deduped across both."""
    # derived: an email disclosed in a finding's details (where
    # extract_entities looks for emails — a bare email *target* is
    # classified as a host, not an email).
    db.add_all(
        [
            _finding(
                engagement.id,
                target=None,
                details={"contact": "derived@contoso.com"},
            ),
            # a second finding repeating the same email — must dedupe
            _finding(
                engagement.id,
                target="mail.contoso.com",
                details={"note": "also reached derived@contoso.com"},
            ),
        ]
    )
    # stored: a Maltego-imported email, plus the derived one again (dedupe)
    db.add_all(
        [
            _entity(engagement.id, "email", "stored@contoso.com"),
            _entity(engagement.id, "email", "derived@contoso.com"),
        ]
    )
    db.commit()

    grouped = _build_entities(db, engagement)

    # derived appears once (deduped across findings + stored), stored once
    assert grouped["email"] == ["derived@contoso.com", "stored@contoso.com"]
    # the mail.contoso.com target was classified as a host (derived)
    assert grouped["host"] == ["mail.contoso.com"]


def test_build_args_env_serialises_entities_into_payload() -> None:
    """The sandbox must deliver entities to the entrypoint via
    RTD_ARGS_JSON, additive to scope/args."""
    entities = {
        "email": ["alice@contoso.com", "bob@contoso.com"],
        "host": ["mail.contoso.com"],
    }
    req = SandboxRequest(
        tool_id="t",
        tool_name="embrey-m365-enum",
        tool_version=1,
        tool_kind="python",
        entrypoint="main.py",
        source_bytes=b"",
        args={"candidates": "x@contoso.com"},
        scope={"engagement_slug": "demo", "domains": ["contoso.com"]},
        entities=entities,
    )

    blob = build_args_env(req)
    payload = json.loads(base64.b64decode(blob).decode("utf-8"))

    assert payload["entities"] == entities
    # additive — scope + args still present for tools that predate entities
    assert payload["scope"]["domains"] == ["contoso.com"]
    assert payload["args"]["candidates"] == "x@contoso.com"
