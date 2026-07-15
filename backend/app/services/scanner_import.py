"""Shared preview/commit support for analyst-supplied scanner exports.

Preview and commit both parse the original bytes.  The browser sends back only
stable group selection keys plus the preview SHA-256; normalized finding data is
never accepted from the client.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, cast

from app.models import FindingPhase, ScopeItem, Severity
from app.services.finding_grouping import (
    canonical_import_group_key,
    import_item_dedup_key,
)
from app.services.scope_matcher import (
    ScopeMatch,
    evaluate_scope_candidates,
    extract_host,
    infer_scope_kind,
)

ScannerSource = Literal["nessus", "burp", "nmap"]
MAX_SCANNER_EXPORT_BYTES = 100 * 1024 * 1024  # v2.6.1: bumped 20 → 100 MB; real Burp Pro exports on prod-sized targets easily clear 30 MB.
MAX_SCANNER_ITEMS = 50_000
MAX_SCANNER_GROUPS = 5_000
MAX_SELECTION_FORM_BYTES = 1024 * 1024
MAX_PREVIEW_TARGETS_PER_GROUP = 100


class ScannerImportItem(Protocol):
    title: str
    severity: Severity
    phase: FindingPhase
    summary: str | None
    target: str
    source_tool: str
    details: dict[str, Any]
    group_key: str | None


class ScannerImportParser(Protocol):
    def __call__(
        self,
        raw: bytes,
        *,
        scope_items: list[ScopeItem] | None = None,
        **options: Any,
    ) -> Any: ...


@dataclass(frozen=True, slots=True)
class DuplicateIndex:
    """Existing persistence keys used to label preview groups."""

    group_dedup_keys: Mapping[str, frozenset[str]] = field(default_factory=dict)
    burp_serials: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class ScopeReasonCount:
    code: str
    count: int
    message: str


@dataclass(frozen=True, slots=True)
class PreviewGroup:
    selection_key: str
    title: str
    severity: Severity
    phase: FindingPhase
    item_count: int
    target_count: int
    targets: tuple[str, ...]
    targets_truncated: bool
    scope_decision: str
    scope_reasons: tuple[ScopeReasonCount, ...]
    in_scope_item_count: int
    out_of_scope_item_count: int
    duplicate_state: Literal["new", "partial", "existing"]
    duplicate_item_count: int
    default_selected: bool


@dataclass(frozen=True, slots=True)
class ScannerPreview:
    source: ScannerSource
    file_sha256: str
    total_source_rows: int
    groups: tuple[PreviewGroup, ...]
    counts: Mapping[str, int]
    parser_counts: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class PreparedCommit:
    source: ScannerSource
    file_sha256: str
    selected_group_count: int
    selected_item_count: int
    skipped_out_of_scope: int
    skipped_duplicate: int
    items: tuple[ScannerImportItem, ...]
    parser_counts: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class _EvaluatedItem:
    item: ScannerImportItem
    selection_key: str
    scope: ScopeMatch
    duplicate: bool


_SEVERITY_RANK = {
    Severity.info: 0,
    Severity.low: 1,
    Severity.medium: 2,
    Severity.high: 3,
    Severity.critical: 4,
}


def scanner_file_sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _parser_for(source: ScannerSource) -> tuple[ScannerImportParser, dict[str, Any]]:
    # Imports stay local so each legacy parser remains independently usable.
    if source == "nessus":
        from app.services.nessus_import import parse_nessus_xml

        return cast(ScannerImportParser, parse_nessus_xml), {"include_info": True}
    if source == "burp":
        from app.services.burp_import import parse_burp_xml

        return cast(ScannerImportParser, parse_burp_xml), {"include_info": True}
    if source == "nmap":
        from app.services.nmap_import import parse_nmap_xml

        return cast(ScannerImportParser, parse_nmap_xml), {}
    raise ValueError(f"unsupported scanner source: {source}")


def parse_scanner_export(
    source: ScannerSource,
    raw: bytes,
) -> tuple[list[ScannerImportItem], dict[str, int]]:
    """Parse all persistable rows without applying scope or Info filters."""
    parser, options = _parser_for(source)
    result = parser(raw, scope_items=[], **options)
    items = list(cast(Sequence[ScannerImportItem], result.items))
    if len(items) > MAX_SCANNER_ITEMS:
        raise ValueError(f"scanner export exceeds the {MAX_SCANNER_ITEMS:,}-item limit")
    for item in items:
        if item.group_key:
            item.group_key = canonical_import_group_key(source, item.group_key)

    count_names = (
        "total_items",
        "total_ports",
        "skipped_closed",
        "skipped_info",
        "skipped_out_of_scope",
    )
    counts = {
        name: value
        for name in count_names
        if isinstance((value := getattr(result, name, None)), int)
    }
    source_rows = counts.get("total_items", counts.get("total_ports", len(items)))
    if source_rows > MAX_SCANNER_ITEMS:
        raise ValueError(f"scanner export exceeds the {MAX_SCANNER_ITEMS:,}-item limit")
    return items, counts


def _selection_key(source: ScannerSource, item: ScannerImportItem) -> str:
    if item.group_key:
        return item.group_key
    serial = getattr(item, "burp_serial_number", None)
    canonical = json.dumps(
        {
            "source": source,
            "title": item.title,
            "target": item.target,
            "serial": serial,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical.encode()).hexdigest()[:24]
    return f"{source}:item:{digest}"


def _scope_candidates(source: ScannerSource, item: ScannerImportItem) -> list[str]:
    details = item.details or {}
    candidates: list[str] = []

    if source == "nessus":
        host_name = details.get("host_name")
        if host_name:
            candidates.append(str(host_name))
        props = details.get("host_properties")
        if isinstance(props, dict):
            candidates.extend(
                str(value) for key, value in props.items() if key in {"host-fqdn", "host-ip"}
            )
    elif source == "burp":
        host_ip = details.get("host_ip")
        if host_ip:
            candidates.append(str(host_ip))
    elif source == "nmap":
        addresses = details.get("addresses")
        if isinstance(addresses, dict):
            candidates.extend(str(value) for value in addresses.values())
        host = details.get("host")
        if host:
            candidates.append(str(host))

    candidates.append(item.target)
    target_host = extract_host(item.target)
    if target_host:
        candidates.append(target_host)
    # Preserve order for deterministic primary reason text while removing repeats.
    return list(dict.fromkeys(value.strip() for value in candidates if value and value.strip()))


def _evaluate_items(
    source: ScannerSource,
    items: Sequence[ScannerImportItem],
    scope_items: Sequence[ScopeItem],
    duplicate_index: DuplicateIndex,
) -> list[_EvaluatedItem]:
    evaluated: list[_EvaluatedItem] = []
    seen_by_group = {
        key: set(values) for key, values in duplicate_index.group_dedup_keys.items()
    }
    seen_burp_serials = set(duplicate_index.burp_serials)
    for item in items:
        selection_key = _selection_key(source, item)
        candidates = _scope_candidates(source, item)
        scope = evaluate_scope_candidates(
            [(value, infer_scope_kind(value)) for value in candidates],
            scope_items,
            empty_scope_allowed=True,
        )
        serial = getattr(item, "burp_serial_number", None)
        dedup_record = {"target": item.target, "burp_serial_number": serial}
        dedup_key = import_item_dedup_key(item.source_tool, dedup_record)
        group_seen = seen_by_group.setdefault(selection_key, set())
        duplicate = dedup_key in group_seen or (
            source == "burp" and serial and serial in seen_burp_serials
        )
        # A rejected row must not claim the identity and suppress a later
        # in-scope representation of the same scanner observation.
        if scope.allowed:
            group_seen.add(dedup_key)
            if source == "burp" and serial:
                seen_burp_serials.add(serial)
        evaluated.append(
            _EvaluatedItem(
                item=item,
                selection_key=selection_key,
                scope=scope,
                duplicate=bool(duplicate),
            )
        )
    return evaluated


def _group_preview(
    rows: Sequence[_EvaluatedItem],
    *,
    include_info_by_default: bool,
) -> PreviewGroup:
    first = rows[0]
    reasons = Counter(row.scope.reason_code for row in rows)
    reason_messages: dict[str, str] = {}
    for row in rows:
        reason_messages.setdefault(row.scope.reason_code, row.scope.reason)
    reason_counts = tuple(
        ScopeReasonCount(code=code, count=count, message=reason_messages[code])
        for code, count in sorted(reasons.items())
    )
    scope_decision = reason_counts[0].code if len(reason_counts) == 1 else "mixed"
    duplicate_count = sum(row.duplicate for row in rows)
    allowed_count = sum(row.scope.allowed for row in rows)
    new_allowed_count = sum(row.scope.allowed and not row.duplicate for row in rows)
    if duplicate_count == 0:
        duplicate_state: Literal["new", "partial", "existing"] = "new"
    elif duplicate_count == len(rows):
        duplicate_state = "existing"
    else:
        duplicate_state = "partial"
    severity = max((row.item.severity for row in rows), key=_SEVERITY_RANK.__getitem__)
    all_targets = sorted({row.item.target for row in rows})
    preview_targets = tuple(all_targets[:MAX_PREVIEW_TARGETS_PER_GROUP])

    return PreviewGroup(
        selection_key=first.selection_key,
        title=first.item.title,
        severity=severity,
        phase=first.item.phase,
        item_count=len(rows),
        target_count=len(all_targets),
        targets=preview_targets,
        targets_truncated=len(preview_targets) < len(all_targets),
        scope_decision=scope_decision,
        scope_reasons=reason_counts,
        in_scope_item_count=allowed_count,
        out_of_scope_item_count=len(rows) - allowed_count,
        duplicate_state=duplicate_state,
        duplicate_item_count=duplicate_count,
        default_selected=(
            new_allowed_count > 0
            and (severity is not Severity.info or include_info_by_default)
        ),
    )


def build_scanner_preview(
    source: ScannerSource,
    raw: bytes,
    *,
    scope_items: Sequence[ScopeItem],
    duplicate_index: DuplicateIndex | None = None,
    include_info_by_default: bool = False,
) -> ScannerPreview:
    items, parser_counts = parse_scanner_export(source, raw)
    evaluated = _evaluate_items(
        source,
        items,
        scope_items,
        duplicate_index or DuplicateIndex(),
    )
    grouped: dict[str, list[_EvaluatedItem]] = {}
    for row in evaluated:
        grouped.setdefault(row.selection_key, []).append(row)
    if len(grouped) > MAX_SCANNER_GROUPS:
        raise ValueError(f"scanner export exceeds the {MAX_SCANNER_GROUPS:,}-group limit")
    groups = tuple(
        _group_preview(rows, include_info_by_default=include_info_by_default)
        for _, rows in sorted(grouped.items())
    )
    total_source_rows = parser_counts.get(
        "total_items", parser_counts.get("total_ports", len(items))
    )
    counts = {
        "groups": len(groups),
        "items": len(items),
        "default_selected_groups": sum(group.default_selected for group in groups),
        "informational_groups": sum(group.severity is Severity.info for group in groups),
        "out_of_scope_items": sum(not row.scope.allowed for row in evaluated),
        "duplicate_items": sum(row.duplicate for row in evaluated),
    }
    return ScannerPreview(
        source=source,
        file_sha256=scanner_file_sha256(raw),
        total_source_rows=total_source_rows,
        groups=groups,
        counts=counts,
        parser_counts=parser_counts,
    )


def prepare_scanner_commit(
    source: ScannerSource,
    raw: bytes,
    *,
    expected_sha256: str,
    selected_group_keys: set[str],
    scope_items: Sequence[ScopeItem],
    duplicate_index: DuplicateIndex | None = None,
) -> PreparedCommit:
    actual_sha256 = scanner_file_sha256(raw)
    if actual_sha256.lower() != expected_sha256.strip().lower():
        raise ValueError("uploaded scanner file does not match the preview SHA-256")

    items, parser_counts = parse_scanner_export(source, raw)
    evaluated = _evaluate_items(
        source,
        items,
        scope_items,
        duplicate_index or DuplicateIndex(),
    )
    available_keys = {row.selection_key for row in evaluated}
    unknown = selected_group_keys - available_keys
    if unknown:
        raise ValueError(f"unknown scanner preview selection key(s): {', '.join(sorted(unknown))}")

    selected = [row for row in evaluated if row.selection_key in selected_group_keys]
    commit_rows = [row for row in selected if row.scope.allowed and not row.duplicate]
    return PreparedCommit(
        source=source,
        file_sha256=actual_sha256,
        selected_group_count=len(selected_group_keys),
        selected_item_count=len(commit_rows),
        skipped_out_of_scope=sum(not row.scope.allowed for row in selected),
        skipped_duplicate=sum(row.duplicate for row in selected if row.scope.allowed),
        items=tuple(row.item for row in commit_rows),
        parser_counts=parser_counts,
    )
