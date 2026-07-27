from __future__ import annotations

import hashlib
import ipaddress
import json
from collections.abc import Iterable
from dataclasses import dataclass

from app.models import Finding, ScopeItem, ScopeKind
from app.services import scope_matcher
from app.services.entities import extract_finding_entities
from app.services.entity_identity import entity_identity_key

MAX_REVIEW_TARGETS = 1_000
MAX_CASCADE_DISCOVERIES = 500
MAX_CASCADE_DEPTH = 3
ENTITY_REVIEW_SCOPE_SOURCE = "entity_review"


@dataclass(frozen=True, slots=True)
class ReviewTarget:
    entity_type: str
    value: str

    @property
    def key(self) -> tuple[str, str]:
        return entity_identity_key(self.entity_type, self.value)


@dataclass(frozen=True, slots=True)
class ImpactEntity:
    entity_type: str
    value: str
    depth: int
    reason: str
    scope_kind: ScopeKind | None
    exact_include_ids: tuple[str, ...]
    exact_exclusion_ids: tuple[str, ...]
    managed_exclusion_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ImpactFinding:
    finding_id: str
    title: str
    target: str | None
    source_tool: str | None
    current_exclusion: str | None
    parent_type: str
    parent_value: str
    depth: int


@dataclass(frozen=True, slots=True)
class ReviewImpact:
    entities: tuple[ImpactEntity, ...]
    findings: tuple[ImpactFinding, ...]
    truncated: bool
    digest: str

    @property
    def finding_ids(self) -> tuple[str, ...]:
        return tuple(row.finding_id for row in self.findings)


def scope_target(entity_type: str, value: str) -> tuple[ScopeKind, str] | None:
    kind, normalized = entity_identity_key(entity_type, value)
    if not normalized:
        return None
    if kind == "ip":
        return ScopeKind.ip, normalized
    if kind == "cidr":
        return ScopeKind.cidr, normalized
    if kind == "host":
        try:
            return ScopeKind.ip, ipaddress.ip_address(normalized).compressed
        except ValueError:
            return ScopeKind.domain, normalized
    if kind in {"domain", "subdomain"}:
        return ScopeKind.domain, normalized
    if kind == "url":
        return ScopeKind.url, normalized
    if kind == "email":
        return ScopeKind.email, normalized
    return None


def _exact_scope_value(kind: ScopeKind, value: str) -> str:
    if kind is ScopeKind.domain:
        return scope_matcher.normalize_domain(value)
    if kind is ScopeKind.url:
        return scope_matcher.normalize_url(value) or value.strip()
    if kind is ScopeKind.email:
        return scope_matcher.normalize_email(value) or value.strip()
    if kind is ScopeKind.ip:
        try:
            return ipaddress.ip_address(value.strip()).compressed
        except ValueError:
            return value.strip()
    if kind is ScopeKind.cidr:
        try:
            return str(ipaddress.ip_network(value.strip(), strict=False))
        except ValueError:
            return value.strip()
    return value.strip()


def exact_scope_rules(
    scope_items: Iterable[ScopeItem], kind: ScopeKind, value: str
) -> list[ScopeItem]:
    wanted = _exact_scope_value(kind, value)
    return [
        item
        for item in scope_items
        if item.kind is kind and _exact_scope_value(item.kind, item.value) == wanted
    ]


def _target_key(finding: Finding) -> tuple[str, str] | None:
    target = (finding.target or "").strip()
    if not target:
        return None
    # The extractor emits a usable finding target before entities from details.
    extracted = extract_finding_entities(finding)
    return entity_identity_key(*extracted[0]) if extracted else None


def build_review_impact(
    *,
    findings: list[Finding],
    scope_items: list[ScopeItem],
    targets: list[ReviewTarget],
    action: str,
    cascade: bool,
) -> ReviewImpact:
    roots: dict[tuple[str, str], ReviewTarget] = {}
    for target in targets:
        key = target.key
        if key[1]:
            roots.setdefault(key, target)
    affected: dict[tuple[str, str], tuple[str, str, int, str]] = {
        key: (target.entity_type, target.value, 0, "Selected by analyst")
        for key, target in roots.items()
    }
    findings_by_target: dict[tuple[str, str], list[Finding]] = {}
    for finding in findings:
        key = _target_key(finding)
        if key is not None:
            findings_by_target.setdefault(key, []).append(finding)

    related: dict[str, ImpactFinding] = {}
    frontier = set(roots)
    truncated = False
    max_entities = len(affected) + MAX_CASCADE_DISCOVERIES
    for depth in range(MAX_CASCADE_DEPTH + 1):
        next_frontier: set[tuple[str, str]] = set()
        for parent in sorted(frontier):
            parent_type, parent_value, parent_depth, _ = affected[parent]
            for finding in findings_by_target.get(parent, []):
                related[str(finding.id)] = ImpactFinding(
                    finding_id=str(finding.id),
                    title=finding.title,
                    target=finding.target,
                    source_tool=finding.source_tool,
                    current_exclusion=(finding.exclusion.value if finding.exclusion else None),
                    parent_type=parent_type,
                    parent_value=parent_value,
                    depth=parent_depth,
                )
                if not cascade or depth >= MAX_CASCADE_DEPTH:
                    continue
                for entity_type, value in extract_finding_entities(finding):
                    key = entity_identity_key(entity_type, value)
                    if not key[1] or key in affected:
                        continue
                    if len(affected) >= max_entities:
                        truncated = True
                        break
                    affected[key] = (
                        entity_type,
                        value,
                        depth + 1,
                        f"Discovered by {finding.source_tool or 'finding evidence'} "
                        f"while assessing {parent_value}",
                    )
                    next_frontier.add(key)
                if truncated:
                    break
            if truncated:
                break
        if not cascade or truncated or not next_frontier:
            break
        frontier = next_frontier

    impact_entities: list[ImpactEntity] = []
    for entity_type, value, depth, reason in affected.values():
        target = scope_target(entity_type, value)
        rules = exact_scope_rules(scope_items, *target) if target else []
        impact_entities.append(
            ImpactEntity(
                entity_type=entity_type,
                value=value,
                depth=depth,
                reason=reason,
                scope_kind=target[0] if target else None,
                exact_include_ids=tuple(
                    sorted(str(row.id) for row in rules if not row.is_exclusion)
                ),
                exact_exclusion_ids=tuple(sorted(str(row.id) for row in rules if row.is_exclusion)),
                managed_exclusion_ids=tuple(
                    sorted(
                        str(row.id)
                        for row in rules
                        if row.is_exclusion and row.source == ENTITY_REVIEW_SCOPE_SOURCE
                    )
                ),
            )
        )
    impact_entities.sort(
        key=lambda row: (
            row.depth,
            entity_identity_key(row.entity_type, row.value),
        )
    )
    impact_findings = sorted(related.values(), key=lambda row: row.finding_id)
    digest_payload = {
        "action": action,
        "cascade": cascade,
        "truncated": truncated,
        "entities": [
            {
                "type": row.entity_type,
                "value": row.value,
                "depth": row.depth,
                "scope_kind": row.scope_kind.value if row.scope_kind else None,
                "includes": row.exact_include_ids,
                "exclusions": row.exact_exclusion_ids,
                "managed_exclusions": row.managed_exclusion_ids,
            }
            for row in impact_entities
        ],
        "findings": [
            {
                "id": row.finding_id,
                "target": row.target,
                "exclusion": row.current_exclusion,
                "parent_type": row.parent_type,
                "parent_value": row.parent_value,
            }
            for row in impact_findings
        ],
    }
    digest = hashlib.sha256(
        json.dumps(digest_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return ReviewImpact(
        entities=tuple(impact_entities),
        findings=tuple(impact_findings),
        truncated=truncated,
        digest=digest,
    )
