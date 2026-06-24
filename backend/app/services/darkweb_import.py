"""DarkWeb data importers — Phase 10.

A pluggable home for breach / leak / paste / credential-dump data
imports. Dehashed (JSON + CSV) is the first concrete source; future
sources (HIBP exports, IntelX dumps, internal corpora) slot in here
following the same shape: parse the upload, return a list of
``ParsedEntity`` rows, feed them through
``entity_store.persist_entities``.

Charter posture (§16 RESOLVED): breach data is "heavy" / sensitive.
Analysts run the search on their own infra (Dehashed account, etc.)
and upload the export. We do not call out to Dehashed or other
sources — no plaintext credential leaves the analyst's tenant.

Data model decision (user-locked): one ``Entity`` per breach record,
not per populated field. Type is ``"breach_record"`` so the entity
type tells you what shape the row holds; the actual breach metadata
(email, password, hash, phone, name, database_name, etc.) lives in
``properties`` JSONB. UPSERT identity is composite —
``value = "<email-or-username>@<database_name>"`` — so the same email
across multiple breaches yields distinct rows but a re-import of the
same breach record merges cleanly.
"""
from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field
from typing import Any

from app.services.maltego_import import ParsedEntity  # reuse the shape

# Sensitive properties stay in the row but the parser surfaces a
# count-of-distinct-databases summary on the result for the analyst.

_DEHASHED_ENTITY_TYPE = "breach_record"


@dataclass
class ParseResult:
    """Mirrors maltego_import.ParseResult so the API renderers can be
    shape-compatible."""

    items: list[ParsedEntity]
    skipped_no_identifier: int
    skipped_malformed: int
    total_rows: int
    databases: list[str] = field(default_factory=list)  # distinct database_name's


# ---------------------------------------------------------------------------
# Entry → ParsedEntity
# ---------------------------------------------------------------------------


def _strip_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _composite_value(entry: dict[str, Any]) -> str | None:
    """Build the UPSERT identity for a Dehashed entry.

    Preference order:
      1. ``email@database_name``  (most common in Dehashed)
      2. ``username@database_name``
      3. ``email``                 (no database tag — best effort)
      4. ``username``
      5. Dehashed entry ``id`` if all else fails (still unique)
      6. ``None`` — caller bumps skipped_no_identifier.

    The composite preserves per-breach distinction: the same email in
    LinkedIn-2012 and Adobe-2013 yields two rows, not one merged
    soup. ``ON CONFLICT`` UPSERTs only fire when the same record is
    re-imported (same email + same database_name).
    """
    email = _strip_or_none(entry.get("email"))
    username = _strip_or_none(entry.get("username"))
    database_name = _strip_or_none(entry.get("database_name"))
    identifier = email or username
    if identifier and database_name:
        return f"{identifier}@{database_name}"
    if identifier:
        return identifier
    entry_id = _strip_or_none(entry.get("id"))
    if entry_id:
        return f"dehashed:{entry_id}"
    return None


def _entry_to_entity(
    entry: dict[str, Any], *, source_attribution: str | None
) -> ParsedEntity | None:
    """Build a ParsedEntity from one Dehashed record. Returns None when
    the record has no usable identifier (caller counts toward
    skipped_no_identifier)."""
    if not isinstance(entry, dict):
        return None
    value = _composite_value(entry)
    if value is None:
        return None
    # Properties carry every populated field from the source verbatim
    # plus the source attribution for UI rendering.
    properties: dict[str, Any] = {
        k: v for k, v in entry.items() if v not in (None, "", [])
    }
    if source_attribution:
        properties["_source_attribution"] = source_attribution
    return ParsedEntity(
        type=_DEHASHED_ENTITY_TYPE,
        value=value,
        properties=properties,
        maltego_type="",
    )


# ---------------------------------------------------------------------------
# JSON parser
# ---------------------------------------------------------------------------


def parse_dehashed_json(
    payload: bytes, *, source_attribution: str | None = None
) -> ParseResult:
    """Parse a Dehashed JSON export.

    Accepts two shapes:
      - The standard API response: ``{"entries": [...], "balance": ...}``.
      - A bare array of entries: ``[{...}, {...}]``.

    Raises ``ValueError`` on malformed JSON or unexpected top-level shape.
    """
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid Dehashed JSON: {exc}") from exc

    if isinstance(data, dict):
        entries = data.get("entries")
        if not isinstance(entries, list):
            raise ValueError(
                "Dehashed JSON missing top-level 'entries' array"
            )
    elif isinstance(data, list):
        entries = data
    else:
        raise ValueError(
            f"unexpected top-level JSON shape {type(data).__name__}; "
            "expected object with 'entries' or bare array"
        )

    return _entries_to_result(entries, source_attribution=source_attribution)


# ---------------------------------------------------------------------------
# CSV parser
# ---------------------------------------------------------------------------


def parse_dehashed_csv(
    payload: bytes, *, source_attribution: str | None = None
) -> ParseResult:
    """Parse a Dehashed CSV export.

    Standard ``csv.DictReader`` over UTF-8 (with BOM tolerance). Each
    row becomes one entry dict — the column names ride along verbatim
    so the same ``_entry_to_entity`` path serves both formats.

    Raises ``ValueError`` on malformed CSV (missing header / no rows).
    """
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(f"invalid CSV encoding (expected UTF-8): {exc}") from exc

    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise ValueError("Dehashed CSV missing header row")

    entries: list[dict[str, Any]] = []
    malformed = 0
    for row in reader:
        if not isinstance(row, dict):
            malformed += 1
            continue
        entries.append(dict(row))

    result = _entries_to_result(entries, source_attribution=source_attribution)
    # csv.DictReader doesn't expose its own malformed counter; we carry
    # the row-shape failure count separately so the API can surface it.
    result.skipped_malformed += malformed
    return result


# ---------------------------------------------------------------------------
# Shared entries → result
# ---------------------------------------------------------------------------


def _entries_to_result(
    entries: list[Any], *, source_attribution: str | None
) -> ParseResult:
    items: list[ParsedEntity] = []
    skipped_no_identifier = 0
    skipped_malformed = 0
    databases: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            skipped_malformed += 1
            continue
        parsed = _entry_to_entity(entry, source_attribution=source_attribution)
        if parsed is None:
            skipped_no_identifier += 1
            continue
        items.append(parsed)
        db_name = _strip_or_none(entry.get("database_name"))
        if db_name:
            databases.add(db_name)
    return ParseResult(
        items=items,
        skipped_no_identifier=skipped_no_identifier,
        skipped_malformed=skipped_malformed,
        total_rows=len(entries),
        databases=sorted(databases),
    )
