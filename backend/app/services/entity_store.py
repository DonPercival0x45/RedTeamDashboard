"""Stored-entities persistence — Phase 10.

UPSERT helper for the ``entities`` table. Duck-typed against any item
shape that exposes ``type`` + ``value`` + ``properties``, so the
Maltego parser (``maltego_import.ParsedEntity``) and any future
importer (Dehashed JSON, etc.) feed the same persistence path.

Why UPSERT instead of bulk INSERT: analysts re-export Maltego graphs
as they add transforms. The natural identity is ``(engagement_id,
type, value)``; on a re-import we want to merge new property data into
the existing row rather than create duplicates. Postgres ``ON CONFLICT
... DO UPDATE`` with a JSONB concatenation (``properties || EXCLUDED``)
gives us merge semantics in one statement per row.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models import Engagement, Entity, EntityGroup, EntityGroupMember
from app.services.entity_identity import normalize_entity_type, normalize_entity_value

_SOURCE_HISTORY_KEY = "_rtd_source_history"


class EntityIdentityConflict(ValueError):
    """Existing legacy variants require an analyst grouping decision."""

    def __init__(self, entity_type: str, value: str, entity_ids: list[str]) -> None:
        self.entity_type = entity_type
        self.value = value
        self.entity_ids = entity_ids
        super().__init__(
            f"multiple stored entities match {entity_type}:{value}; group or suppress them first"
        )


def find_semantic_entity(
    session: Session,
    *,
    engagement_id: Any,
    entity_type: object,
    value: object,
    candidates: list[Entity] | None = None,
) -> tuple[Entity | None, str, str]:
    """Resolve an exact or single conservative semantic match.

    Exact raw matches win so legacy duplicates do not block re-importing the
    already-canonical row. When only format variants exist, a single row is
    safely reused; multiple variants require an explicit analyst decision.
    """
    type_value = normalize_entity_type(entity_type)
    raw_value = str(value or "").strip()
    value_str = normalize_entity_value(type_value, raw_value)
    available = candidates
    if available is None:
        available = list(
            session.execute(
                select(Entity).where(Entity.engagement_id == engagement_id)
            ).scalars()
        )
    type_candidates = [
        row for row in available if normalize_entity_type(row.type) == type_value
    ]
    matches = [
        row
        for row in type_candidates
        if normalize_entity_value(type_value, row.value) == value_str
    ]
    exact_matches = [row for row in matches if row.value == raw_value]
    if len(exact_matches) == 1:
        exact = exact_matches[0]
        canonical_id = session.execute(
            select(EntityGroup.canonical_entity_id)
            .join(EntityGroupMember, EntityGroupMember.group_id == EntityGroup.id)
            .where(EntityGroupMember.entity_id == exact.id)
        ).scalar_one_or_none()
        if canonical_id is not None and canonical_id != exact.id:
            canonical = session.get(Entity, canonical_id)
            if canonical is not None:
                return canonical, type_value, value_str
        return exact, type_value, value_str
    if len(matches) == 1:
        return matches[0], type_value, value_str
    if len(matches) > 1:
        memberships = session.execute(
            select(
                EntityGroupMember.entity_id,
                EntityGroupMember.group_id,
                EntityGroup.canonical_entity_id,
            )
            .join(EntityGroup, EntityGroup.id == EntityGroupMember.group_id)
            .where(EntityGroupMember.entity_id.in_([row.id for row in matches]))
        ).all()
        group_ids = {group_id for _, group_id, _ in memberships}
        if len(memberships) == len(matches) and len(group_ids) == 1:
            canonical_id = memberships[0].canonical_entity_id
            canonical = next((row for row in matches if row.id == canonical_id), None)
            if canonical is not None:
                return canonical, type_value, value_str
        raise EntityIdentityConflict(type_value, value_str, [str(row.id) for row in matches])
    return None, type_value, value_str

logger = structlog.get_logger(__name__)


def persist_entities(
    session: Session,
    *,
    engagement: Engagement,
    items: list[Any],
    source_tool: str,
    source_attribution: str | None = None,
) -> tuple[int, int]:
    """UPSERT a list of parsed entities, merging properties on conflict.

    Items are duck-typed: each must expose ``type``, ``value``,
    ``properties``. Returns ``(inserted_count, merged_count)``. Caller
    commits.

    Merge semantics: on ``(engagement_id, type, value)`` conflict we
    concatenate the JSONB properties (``existing || incoming``), so
    later imports override matching keys but preserve prior ones. The
    ``updated_at`` column gets bumped too.
    """
    if not items:
        return 0, 0

    inserted = 0
    merged = 0
    now = datetime.now(tz=UTC)
    # Load once so large imports do not rescan the engagement for every item.
    identity_candidates = list(
        session.execute(
            select(Entity).where(Entity.engagement_id == engagement.id)
        ).scalars()
    )

    for item in items:
        existing, type_value, value_str = find_semantic_entity(
            session,
            engagement_id=engagement.id,
            entity_type=item.type,
            value=item.value,
            candidates=identity_candidates,
        )
        if not type_value or not value_str:
            continue
        props = dict(getattr(item, "properties", {}) or {})
        existing_id = existing.id if existing else None
        if existing is not None:
            history = list((existing.properties or {}).get(_SOURCE_HISTORY_KEY, []))
            for entry in (
                {
                    "source_tool": existing.source_tool,
                    "source_attribution": existing.source_attribution,
                },
                {
                    "source_tool": source_tool,
                    "source_attribution": source_attribution,
                },
            ):
                if entry not in history:
                    history.append(entry)
            props[_SOURCE_HISTORY_KEY] = history
        # A single legacy representation is reused without rewriting its raw
        # value. New rows use the conservative canonical representation.
        persisted_type = existing.type if existing else type_value
        persisted_value = existing.value if existing else value_str

        stmt = pg_insert(Entity).values(
            engagement_id=engagement.id,
            type=persisted_type,
            value=persisted_value,
            properties=props,
            source_tool=source_tool,
            source_attribution=source_attribution,
            created_at=now,
            updated_at=now,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_entities_engagement_type_value",
            set_={
                # JSONB concat — incoming keys override existing on collision,
                # but prior keys not in the new payload are preserved. Source
                # columns stay latest-compatible while the reserved history key
                # retains prior attribution instead of erasing provenance.
                "properties": Entity.properties.op("||")(stmt.excluded.properties),
                "source_tool": stmt.excluded.source_tool,
                "source_attribution": stmt.excluded.source_attribution,
                "row_version": Entity.row_version + 1,
                "updated_at": now,
            },
        )
        persisted = session.execute(stmt.returning(Entity)).scalar_one()

        if existing_id is None:
            identity_candidates.append(persisted)
            inserted += 1
        else:
            merged += 1

    session.flush()
    logger.info(
        "entity_store.persisted",
        engagement_id=str(engagement.id),
        source_tool=source_tool,
        inserted=inserted,
        merged=merged,
    )
    return inserted, merged


def list_stored_entities(
    session: Session,
    *,
    engagement: Engagement,
    type_filter: str | None = None,
    query: str | None = None,
    include_suppressed: bool = False,
) -> list[Entity]:
    """Read-side query — stored entities for the engagement, optionally
    filtered by ``type`` (exact) and ``query`` (case-insensitive substring
    on ``value``). Ordered newest first."""
    stmt = select(Entity).where(Entity.engagement_id == engagement.id)
    if not include_suppressed:
        stmt = stmt.where(Entity.suppressed_at.is_(None))
    if type_filter:
        stmt = stmt.where(Entity.type == type_filter)
    if query:
        stmt = stmt.where(Entity.value.ilike(f"%{query}%"))
    stmt = stmt.order_by(Entity.created_at.desc())
    return list(session.execute(stmt).scalars())
