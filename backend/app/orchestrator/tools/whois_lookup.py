"""WHOIS lookup via python-whois.

python-whois returns mixed types (str, datetime, list, None) — we serialize
everything to JSON-friendly strings so the result drops cleanly into the
worker's finding emission path.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

import whois

from app.orchestrator.tools.runtime import ToolResult


def _serialize(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        cleaned = [_serialize(v) for v in value if v is not None]
        return cleaned or None
    return str(value)


def whois_lookup_impl(args: Mapping[str, Any]) -> ToolResult:
    domain = str(args.get("domain") or "").strip().rstrip(".")
    if not domain:
        return ToolResult(ok=False, error="missing or empty 'domain' arg")

    try:
        record = whois.whois(domain)
    except Exception as exc:  # noqa: BLE001 — python-whois raises broad types
        return ToolResult(ok=False, error=f"whois failed: {exc}")

    return ToolResult(
        ok=True,
        data={
            "domain": domain,
            "registrar": _serialize(record.registrar),
            "creation_date": _serialize(record.creation_date),
            "expiration_date": _serialize(record.expiration_date),
            "updated_date": _serialize(getattr(record, "updated_date", None)),
            "name_servers": _serialize(record.name_servers),
            "status": _serialize(record.status),
            "emails": _serialize(record.emails),
            "registrant_country": _serialize(
                getattr(record, "country", None)
            ),
        },
    )
