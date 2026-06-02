"""Serializable scope-item view used by the gate and graph state.

SQLAlchemy ORM rows aren't msgpack-serializable, so the LangGraph checkpointer
rejects them when they appear in state. ``ScopeSnapshot`` is the immutable,
primitive-only view the runtime carries instead. Production code converts at
the DB boundary (``ScopeSnapshot.from_scope_item``).
"""
from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.models import ScopeItem, ScopeKind


@dataclass(frozen=True, slots=True)
class ScopeSnapshot:
    id: uuid.UUID
    kind: ScopeKind
    value: str
    is_exclusion: bool

    @classmethod
    def from_scope_item(cls, item: ScopeItem) -> ScopeSnapshot:
        return cls(
            id=item.id,
            kind=item.kind,
            value=item.value,
            is_exclusion=item.is_exclusion,
        )

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> ScopeSnapshot:
        raw_id = data["id"]
        raw_kind = data["kind"]
        return cls(
            id=raw_id if isinstance(raw_id, uuid.UUID) else uuid.UUID(str(raw_id)),
            kind=raw_kind if isinstance(raw_kind, ScopeKind) else ScopeKind(raw_kind),
            value=data["value"],
            is_exclusion=bool(data["is_exclusion"]),
        )


def normalize_scope_items(raw: Any) -> list[ScopeSnapshot]:
    """Coerce a state list (which may be ScopeSnapshot or dict after a
    checkpoint round-trip) into ScopeSnapshot instances."""
    if not raw:
        return []
    out: list[ScopeSnapshot] = []
    for item in raw:
        if isinstance(item, ScopeSnapshot):
            out.append(item)
        elif isinstance(item, Mapping):
            out.append(ScopeSnapshot.from_mapping(item))
        else:  # e.g. a detached ScopeItem at runtime — duck-type it
            out.append(
                ScopeSnapshot(
                    id=item.id,
                    kind=item.kind,
                    value=item.value,
                    is_exclusion=item.is_exclusion,
                )
            )
    return out
