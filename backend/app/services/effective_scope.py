"""Backend-owned effective-scope projection for read models and selectors.

Execution authorization remains in :mod:`app.services.scope_matcher`. This
module only turns that canonical decision into a stable, explainable read
contract shared by Entities, Scope, and later projection surfaces.
"""

from __future__ import annotations

import enum
import uuid
from collections.abc import Iterable
from dataclasses import dataclass

from app.models import ScopeKind
from app.services import scope_matcher


class EffectiveScopeState(enum.StrEnum):
    included = "included"
    excluded = "excluded"
    unmatched = "unmatched"
    unsupported = "unsupported"


@dataclass(frozen=True, slots=True)
class EffectiveScopeDecision:
    state: EffectiveScopeState
    allowed: bool
    reason_code: str
    reason: str
    target: str | None = None
    target_kind: ScopeKind | None = None
    matched_include_id: uuid.UUID | None = None
    matched_exclusion_id: uuid.UUID | None = None


_ENTITY_KIND_LOOKUP: dict[str, tuple[ScopeKind, ...]] = {
    "ip": (ScopeKind.ip,),
    "cidr": (ScopeKind.cidr,),
    "domain": (ScopeKind.domain,),
    "subdomain": (ScopeKind.domain,),
    # Preserve the existing conservative dual identity. The canonical matcher
    # validates each interpretation and exclusions still win across candidates.
    "host": (ScopeKind.ip, ScopeKind.domain),
    "url": (ScopeKind.url,),
    "email": (ScopeKind.email,),
}


def entity_scope_candidates(
    entity_type: str,
    entity_value: str,
) -> tuple[tuple[str, ScopeKind], ...]:
    value = entity_value.strip()
    if not value:
        return ()
    kinds = _ENTITY_KIND_LOOKUP.get(entity_type.strip().lower(), ())
    return tuple((value, kind) for kind in kinds)


def _target_kind_for_match(
    candidates: tuple[tuple[str, ScopeKind], ...],
    items: list[scope_matcher.ScopeItemLike],
    match: scope_matcher.ScopeMatch,
) -> ScopeKind | None:
    matched_id = match.matched_exclusion_id or match.matched_include_id
    if matched_id is not None:
        item = next((row for row in items if row.id == matched_id), None)
        if item is not None:
            for value, kind in candidates:
                if scope_matcher.item_matches(value, kind, item):
                    return kind
    return candidates[0][1] if candidates else None


def project_candidates(
    candidates: Iterable[tuple[str, ScopeKind]],
    scope_items: Iterable[scope_matcher.ScopeItemLike],
    *,
    empty_scope_allowed: bool = False,
) -> EffectiveScopeDecision:
    candidate_tuple = tuple(
        (value.strip(), kind)
        for value, kind in candidates
        if isinstance(value, str) and value.strip()
    )
    items = list(scope_items)
    match = scope_matcher.evaluate_scope_candidates(
        candidate_tuple,
        items,
        empty_scope_allowed=empty_scope_allowed,
    )
    state = (
        EffectiveScopeState.included
        if match.allowed
        else EffectiveScopeState.excluded
        if match.matched_exclusion_id is not None
        else EffectiveScopeState.unmatched
    )
    return EffectiveScopeDecision(
        state=state,
        allowed=match.allowed,
        reason_code=match.reason_code,
        reason=match.reason,
        target=match.target,
        target_kind=_target_kind_for_match(candidate_tuple, items, match),
        matched_include_id=match.matched_include_id,
        matched_exclusion_id=match.matched_exclusion_id,
    )


def project_target(
    value: str,
    kind: ScopeKind,
    scope_items: Iterable[scope_matcher.ScopeItemLike],
    *,
    empty_scope_allowed: bool = False,
) -> EffectiveScopeDecision:
    return project_candidates(
        [(value, kind)],
        scope_items,
        empty_scope_allowed=empty_scope_allowed,
    )


def exact_scope_rule_ids_for_entity(
    entity_type: str,
    entity_value: str,
    scope_items: Iterable[scope_matcher.ScopeItemLike],
) -> tuple[tuple[uuid.UUID, ...], tuple[uuid.UUID, ...]]:
    """Return canonical exact include/exclusion IDs for mutation controls."""
    candidates = entity_scope_candidates(entity_type, entity_value)
    includes: set[uuid.UUID] = set()
    exclusions: set[uuid.UUID] = set()
    for item in scope_items:
        item_identity = scope_matcher.normalize_scope_value(item.value, item.kind)
        if item_identity is None:
            continue
        for value, kind in candidates:
            if item.kind is not kind:
                continue
            if scope_matcher.normalize_scope_value(value, kind) != item_identity:
                continue
            (exclusions if item.is_exclusion else includes).add(item.id)
            break
    return (
        tuple(sorted(includes, key=str)),
        tuple(sorted(exclusions, key=str)),
    )


def project_entity(
    entity_type: str,
    entity_value: str,
    scope_items: Iterable[scope_matcher.ScopeItemLike],
) -> EffectiveScopeDecision:
    candidates = entity_scope_candidates(entity_type, entity_value)
    if not candidates:
        return EffectiveScopeDecision(
            state=EffectiveScopeState.unsupported,
            allowed=False,
            reason_code="unsupported_entity_type",
            reason=f"entity type {entity_type!r} has no scope identity",
            target=entity_value.strip() or None,
        )
    return project_candidates(candidates, scope_items)


def project_scope_item(
    item: scope_matcher.ScopeItemLike,
    scope_items: Iterable[scope_matcher.ScopeItemLike],
) -> EffectiveScopeDecision:
    return project_target(item.value, item.kind, scope_items)
