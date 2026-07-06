"""Nessus-style ingest grouping for findings (v1.4.0).

Every tool wrapper's output is folded into ONE finding row per
``(engagement_id, group_key)`` — repeated runs of subfinder against the
same apex domain hit the same row, portscan hits on the same host land
inside one row, etc. The per-hit records live inside ``details['items']``
as a deduped array; the row's summary title stays constant.

This module owns:

- **The category vocabulary** (:func:`compute_group_key`) — the source
  of truth for how each tool categorizes its output. Adding a new tool
  = adding it here, nothing else changes.
- **The projection** (:func:`extract_items`) — turns a tool's raw
  ``data`` blob into the per-hit records that fold into ``items[]``.
  Some tools (subfinder, crt_sh) already emit one call = many hits
  inside a list; some (portscan) fan out to N calls upstream and each
  call carries one hit.
- **The dedup key** (:func:`item_dedup_key`) — the natural identity of
  a hit inside a group. Re-running subfinder is a no-op for already-
  seen subdomains; the row's ``updated_at`` still bumps but items[]
  doesn't grow.
- **The upsert** (:func:`upsert_grouped_finding`) — Postgres-flavoured
  INSERT with an ``ON CONFLICT DO UPDATE`` that merges items and lifts
  severity. Caller commits.

The old un-grouped path still works: any tool that doesn't have an
entry in the vocab returns ``None`` from :func:`compute_group_key`, and
the caller falls back to a plain per-hit ``INSERT`` — the pre-v1.4.0
behavior.
"""
from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Finding,
    FindingPhase,
    FindingStatus,
    Severity,
)

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Severity ordering (kept private so callers don't grow their own copy)
# ---------------------------------------------------------------------------

_SEV_RANK: dict[Severity, int] = {
    Severity.info: 0,
    Severity.low: 1,
    Severity.medium: 2,
    Severity.high: 3,
    Severity.critical: 4,
}


def _max_severity(a: Severity, b: Severity) -> Severity:
    return a if _SEV_RANK[a] >= _SEV_RANK[b] else b


# ---------------------------------------------------------------------------
# Category vocabulary
# ---------------------------------------------------------------------------


def _apex_of(domain: str) -> str:
    """Return the apex (last two labels) for a domain, lowercased.

    Cheap heuristic — good enough for the grouping key. For real eTLD+1
    resolution we'd need a Public Suffix List, but for group_key
    stability across ``www.example.com`` / ``example.com`` this suffices.
    A single-label input (``localhost``) returns itself.
    """
    parts = [p for p in domain.strip().lower().rstrip(".").split(".") if p]
    if len(parts) <= 2:
        return ".".join(parts)
    return ".".join(parts[-2:])


def _httpx_bucket(status: int | None) -> str:
    if status is None:
        return "unreachable"
    if status >= 500:
        return "server-error"
    if status >= 400:
        return "client-error"
    if status >= 300:
        return "redirect"
    if status >= 200:
        return "live"
    return "informational"


def compute_group_key(
    tool: str | None,
    args: Mapping[str, Any] | None,
    data: Mapping[str, Any] | None,
) -> str | None:
    """Return the group key for this hit, or ``None`` if the tool opts out
    of grouping (falls back to per-hit rows, the old behavior).

    Each tool's key is deterministic given its (tool, args, data) so the
    same hit always folds into the same row. The apex-domain helper
    keeps ``www.example.com`` and ``mail.example.com`` grouped under a
    single ``subfinder:example.com`` when a re-run enumerates a
    different sub-tree.
    """
    if not tool:
        return None
    args = args or {}
    data = data or {}

    if tool == "subfinder":
        domain = str(data.get("domain") or args.get("domain") or "").strip().lower()
        if not domain:
            return None
        return f"subfinder:{_apex_of(domain)}"

    if tool == "crt_sh":
        domain = str(data.get("domain") or args.get("domain") or "").strip().lower()
        if not domain:
            return None
        return f"crt_sh:{_apex_of(domain)}"

    if tool == "dns_lookup":
        domain = str(data.get("domain") or args.get("domain") or "").strip().lower()
        if not domain:
            return None
        return f"dns:{domain}"

    if tool == "whois_lookup":
        domain = str(data.get("domain") or args.get("domain") or "").strip().lower()
        if not domain:
            return None
        return f"whois:{_apex_of(domain)}"

    if tool == "reverse_dns":
        ip = str(data.get("ip") or args.get("ip") or "").strip()
        if not ip:
            return None
        return f"reverse_dns:{ip}"

    if tool == "httpx_probe":
        # One row per response bucket per apex — 200s under one row,
        # 4xx/5xx broken out. Lets the analyst scan the reachable
        # surface without a wall of one-row-per-URL noise.
        url = str(data.get("url") or data.get("final_url") or args.get("url") or "")
        host = url.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0].lower()
        bucket = _httpx_bucket(
            data.get("status") if isinstance(data.get("status"), int) else None
        )
        apex = _apex_of(host) if host else "unknown"
        return f"httpx:{apex}:{bucket}"

    if tool == "portscan":
        # One row per host — every open port on 10.0.0.5 shares one row.
        host = str(data.get("host") or data.get("target") or "").strip()
        if not host:
            return None
        return f"portscan:{host}"

    if tool == "subnet_sweep":
        # subnet_sweep fans out per-host findings that structurally
        # mirror portscan's, so they SHARE the portscan group key.
        # A per-host CIDR run then a follow-up portscan on the same
        # host lands in one row.
        host = str(data.get("host") or data.get("target") or "").strip()
        if not host:
            return None
        return f"portscan:{host}"

    if tool == "service_detect":
        host = str(data.get("host") or data.get("target") or "").strip()
        if not host:
            return None
        return f"service_detect:{host}"

    # Importer-side keys — set by the callers of these tools, not derived.
    # Nessus stamps ``nessus:{plugin_id}``; Burp stamps ``burp:{issue_name}``.
    # Both pass through the ``args['group_key']`` escape hatch below.

    caller_supplied = args.get("group_key") if isinstance(args, Mapping) else None
    if isinstance(caller_supplied, str) and caller_supplied.strip():
        return caller_supplied.strip()

    return None


# ---------------------------------------------------------------------------
# Item extraction + dedup
# ---------------------------------------------------------------------------


def extract_items(
    tool: str | None,
    data: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """Project a tool's ``data`` blob into per-hit records.

    Most tools already carry a clean list (subfinder → subdomains,
    crt_sh → subdomains). Portscan-family tools emit one finding per
    open port, so ``data`` already IS one hit — we wrap it in a list.

    Unknown tools fall back to ``[data]`` — the whole blob becomes one
    item so nothing gets lost.
    """
    if not tool:
        return [dict(data)] if data else []
    data = data or {}

    if tool in ("subfinder", "crt_sh"):
        return [
            {"subdomain": s}
            for s in data.get("subdomains") or []
            if isinstance(s, str) and s.strip()
        ]

    if tool == "dns_lookup":
        # dns_lookup returns A / AAAA / CNAME records; each record is
        # its own item, keyed by (type, value).
        items: list[dict[str, Any]] = []
        for kind in ("a", "aaaa", "cname"):
            for value in data.get(kind) or []:
                if isinstance(value, str) and value.strip():
                    items.append({"type": kind.upper(), "value": value})
        return items

    if tool == "reverse_dns":
        return [
            {"hostname": h}
            for h in data.get("hostnames") or []
            if isinstance(h, str) and h.strip()
        ]

    if tool == "httpx_probe":
        return [
            {
                "url": data.get("url"),
                "final_url": data.get("final_url"),
                "status": data.get("status"),
                "title": data.get("title"),
                "server": data.get("server"),
            }
        ]

    if tool in ("portscan", "subnet_sweep"):
        # These fan out to N per-(host, port) findings upstream — each
        # arriving finding already carries a single port.
        return [
            {
                "port": data.get("port"),
                "service": data.get("service"),
                "host": data.get("host"),
            }
        ]

    if tool == "service_detect":
        return [dict(data)]

    if tool == "whois_lookup":
        # WHOIS is one blob per domain — collapse into one item.
        return [dict(data)]

    return [dict(data)]


def item_dedup_key(tool: str | None, item: Mapping[str, Any]) -> str:
    """Natural identity of an item inside its group. Used to skip re-adds
    on a second run of the same tool against the same target."""
    if tool in ("subfinder", "crt_sh"):
        return str(item.get("subdomain") or "").lower()
    if tool == "dns_lookup":
        return f"{item.get('type')}={item.get('value')}"
    if tool == "reverse_dns":
        return str(item.get("hostname") or "").lower()
    if tool == "httpx_probe":
        return str(item.get("url") or item.get("final_url") or "").lower()
    if tool in ("portscan", "subnet_sweep"):
        return f"{item.get('host')}:{item.get('port')}"
    if tool == "service_detect":
        return f"{item.get('host')}:{item.get('port')}"
    if tool == "whois_lookup":
        return str(item.get("domain") or "").lower()
    # Unknown tools — use JSON-of-item as the key. Cheap and correct.
    import json

    return json.dumps(item, sort_keys=True, default=str)


# ---------------------------------------------------------------------------
# Grouped titles
# ---------------------------------------------------------------------------


def group_title(tool: str | None, group_key: str, data: Mapping[str, Any] | None) -> str:
    """Human title for a grouped row. Kept constant so the row can be
    tracked across re-runs. Item count is rendered by the frontend."""
    data = data or {}
    if tool == "subfinder":
        # group_key is "subfinder:<apex>"
        apex = group_key.split(":", 1)[-1]
        return f"Subdomains discovered — {apex}"
    if tool == "crt_sh":
        apex = group_key.split(":", 1)[-1]
        return f"Certificate transparency hits — {apex}"
    if tool == "dns_lookup":
        domain = group_key.split(":", 1)[-1]
        return f"DNS records — {domain}"
    if tool == "whois_lookup":
        apex = group_key.split(":", 1)[-1]
        return f"WHOIS record — {apex}"
    if tool == "reverse_dns":
        ip = group_key.split(":", 1)[-1]
        return f"Reverse DNS — {ip}"
    if tool == "httpx_probe":
        # httpx:<apex>:<bucket>
        parts = group_key.split(":", 2)
        apex = parts[1] if len(parts) > 1 else "?"
        bucket = parts[2] if len(parts) > 2 else "?"
        return f"HTTP surface ({bucket}) — {apex}"
    if tool in ("portscan", "subnet_sweep"):
        host = group_key.split(":", 1)[-1]
        return f"Open ports — {host}"
    if tool == "service_detect":
        host = group_key.split(":", 1)[-1]
        return f"Service fingerprints — {host}"
    # Importer-supplied group_key — the caller's title wins unless we
    # can derive something better. Fall through to the caller's title.
    return group_key


# ---------------------------------------------------------------------------
# Upsert
# ---------------------------------------------------------------------------


def upsert_grouped_finding(
    session: Session,
    *,
    engagement_id: uuid.UUID,
    group_key: str,
    tool: str,
    thread_id: str | None,
    args: Mapping[str, Any],
    data: Mapping[str, Any],
    incoming_severity: Severity,
    default_title: str | None,
    phase: FindingPhase,
    status: FindingStatus,
    validated_by: uuid.UUID | None = None,
) -> tuple[Finding, int]:
    """Find or create the grouped row and merge this hit into it.

    Returns ``(row, added_count)`` where ``added_count`` is the number of
    NEW items appended (0 if every extracted item was already present).
    Caller commits.

    Item semantics: extract items from the incoming ``data`` via
    :func:`extract_items`, dedup against the existing ``details['items']``
    using :func:`item_dedup_key`, append what's new, bump severity if
    the incoming hit is higher, refresh ``details['last_seen_at']``.

    First-write path stamps ``details['first_seen_at']``, ``group_key``,
    ``source_tool``, a title from :func:`group_title` (or the caller's
    ``default_title`` fallback), and the initial ``items`` list.
    """
    now = datetime.now(tz=UTC).isoformat()
    incoming_items = extract_items(tool, data)

    row = session.execute(
        select(Finding).where(
            Finding.engagement_id == engagement_id,
            Finding.group_key == group_key,
            Finding.deleted_at.is_(None),
        )
    ).scalar_one_or_none()

    if row is None:
        # Fresh group. Dedup incoming items against themselves in case
        # the tool emitted the same subdomain twice in one call.
        seen: set[str] = set()
        deduped: list[dict[str, Any]] = []
        for item in incoming_items:
            key = item_dedup_key(tool, item)
            if key in seen:
                continue
            seen.add(key)
            item_with_meta = dict(item)
            item_with_meta.setdefault("first_seen_at", now)
            deduped.append(item_with_meta)

        title = group_title(tool, group_key, data) or default_title or f"{tool}: {group_key}"
        details: dict[str, Any] = {
            "thread_id": thread_id,
            "args": dict(args),
            "first_seen_at": now,
            "last_seen_at": now,
            "items": deduped,
            "grouped": True,
        }
        row = Finding(
            engagement_id=engagement_id,
            title=title,
            severity=incoming_severity,
            summary=None,
            details=details,
            source_tool=tool,
            target=_representative_target(tool, group_key, data),
            phase=phase,
            status=status,
            validated_at=datetime.now(tz=UTC)
            if status == FindingStatus.validated
            else None,
            validated_by=validated_by if status == FindingStatus.validated else None,
            group_key=group_key,
        )
        session.add(row)
        session.flush()
        logger.info(
            "finding_grouping.created",
            engagement_id=str(engagement_id),
            group_key=group_key,
            item_count=len(deduped),
        )
        return row, len(deduped)

    # Existing group — merge.
    details = dict(row.details or {})
    existing_items = list(details.get("items") or [])
    existing_keys = {item_dedup_key(tool, it) for it in existing_items if isinstance(it, dict)}

    added = 0
    for item in incoming_items:
        key = item_dedup_key(tool, item)
        if key in existing_keys:
            continue
        existing_keys.add(key)
        item_with_meta = dict(item)
        item_with_meta.setdefault("first_seen_at", now)
        existing_items.append(item_with_meta)
        added += 1

    details["items"] = existing_items
    details["last_seen_at"] = now
    details["grouped"] = True
    row.details = details

    if _SEV_RANK[incoming_severity] > _SEV_RANK[row.severity]:
        row.severity = incoming_severity

    logger.info(
        "finding_grouping.merged",
        engagement_id=str(engagement_id),
        group_key=group_key,
        added=added,
        total_items=len(existing_items),
    )
    return row, added


def upsert_grouped_import_item(
    session: Session,
    *,
    engagement_id: uuid.UUID,
    group_key: str,
    source_tool: str,
    item_title: str,
    item_severity: Severity,
    item_target: str | None,
    item_details: Mapping[str, Any],
    phase: FindingPhase,
    status: FindingStatus,
    validated_by: uuid.UUID | None = None,
) -> tuple[Finding, bool]:
    """Importer-side twin of :func:`upsert_grouped_finding`.

    Each ParsedItem from a Nessus/Burp/CSV import is naturally already
    ONE hit — the parser has already flattened the source format down
    to per-(host, plugin) or per-(URL, issue) rows. This helper folds
    them under the shared group_key without the extract_items dance.

    ``item`` shape written to ``details['items']``: the item's ``target``
    plus every key from ``item_details``, filtered to the fields worth
    surfacing to the analyst. Dedup key = ``{target}:{port}`` for
    Nessus-shaped data, ``{target}`` for Burp/generic, so re-importing
    the same export is a no-op for already-present rows.

    Returns ``(row, added)`` where ``added`` is True if the item was
    new to the group, False if it deduped against an existing entry.
    Caller commits.
    """
    now = datetime.now(tz=UTC).isoformat()

    row = session.execute(
        select(Finding).where(
            Finding.engagement_id == engagement_id,
            Finding.group_key == group_key,
            Finding.deleted_at.is_(None),
        )
    ).scalar_one_or_none()

    # Build the item record — target + selected detail fields.
    item_record: dict[str, Any] = {"target": item_target, "first_seen_at": now}
    for k, v in item_details.items():
        # Skip huge blobs (raw HTML bodies, full stack traces) inside
        # items[] — they'd blow up the row's JSONB size on a 500-row
        # import. The per-item info is a summary; the analyst reads
        # full detail in the source scanner if they need it.
        if k in ("host_properties", "request", "response"):
            continue
        item_record[k] = v

    dedup_key = _import_item_dedup_key(source_tool, item_record)

    if row is None:
        details: dict[str, Any] = {
            "args": {},
            "first_seen_at": now,
            "last_seen_at": now,
            "items": [item_record],
            "grouped": True,
            "import_source": source_tool,
        }
        row = Finding(
            engagement_id=engagement_id,
            title=item_title,
            severity=item_severity,
            summary=None,
            details=details,
            source_tool=source_tool,
            target=item_target,
            phase=phase,
            status=status,
            validated_at=datetime.now(tz=UTC)
            if status == FindingStatus.validated
            else None,
            validated_by=validated_by if status == FindingStatus.validated else None,
            group_key=group_key,
        )
        session.add(row)
        session.flush()
        logger.info(
            "finding_grouping.import_created",
            engagement_id=str(engagement_id),
            group_key=group_key,
            source=source_tool,
        )
        return row, True

    details = dict(row.details or {})
    existing_items = list(details.get("items") or [])
    existing_keys = {
        _import_item_dedup_key(source_tool, it)
        for it in existing_items
        if isinstance(it, dict)
    }

    if dedup_key in existing_keys:
        # Already-present hit — no-op except for last_seen_at bump.
        details["last_seen_at"] = now
        row.details = details
        return row, False

    existing_items.append(item_record)
    details["items"] = existing_items
    details["last_seen_at"] = now
    details["grouped"] = True
    row.details = details

    if _SEV_RANK[item_severity] > _SEV_RANK[row.severity]:
        row.severity = item_severity

    logger.info(
        "finding_grouping.import_merged",
        engagement_id=str(engagement_id),
        group_key=group_key,
        source=source_tool,
        total_items=len(existing_items),
    )
    return row, True


def _import_item_dedup_key(source_tool: str, item: Mapping[str, Any]) -> str:
    """Dedup key for the importer path. Different from the live-tool
    :func:`item_dedup_key` because import items carry more variance in
    their key structure — port may or may not be set."""
    if source_tool == "nessus":
        return f"{item.get('target')}:{item.get('port')}"
    if source_tool == "burp":
        # Burp's target = host + path so it's already unique per
        # observation site.
        return str(item.get("target") or "")
    return str(item.get("target") or "")


def _representative_target(
    tool: str | None, group_key: str, data: Mapping[str, Any]
) -> str | None:
    """Pick a stable target string for the grouped row's ``target``
    column so the search-bar filter still hits."""
    if tool in ("portscan", "subnet_sweep", "service_detect", "reverse_dns"):
        return str(data.get("host") or data.get("ip") or "").strip() or None
    if tool in ("subfinder", "crt_sh", "dns_lookup", "whois_lookup"):
        # The apex domain out of the group key.
        return group_key.split(":", 1)[-1]
    if tool == "httpx_probe":
        parts = group_key.split(":", 2)
        return parts[1] if len(parts) > 1 else None
    return None
