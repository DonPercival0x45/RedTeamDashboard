"""WHOIS registration metadata tool — python-whois wrapper.

Satisfies ``osint.domain.whois`` / ``recon.passive.whois``. The library uses
the system whois toolchain under the hood; failures typically mean the TLD
isn't supported or the domain doesn't exist. We count the presence of
registrar / registrant fields as findings so a run against a non-existent
domain doesn't inflate the FindingsSummary.
"""
from __future__ import annotations

from typing import Any

from app.services.playbook.executor import StepResult

_INTERESTING_KEYS = (
    "registrar",
    "registrant",
    "registrant_name",
    "registrant_organization",
    "creation_date",
    "updated_date",
    "expiration_date",
    "name_servers",
    "emails",
)


def run(scope_context: str, args: dict[str, Any]) -> StepResult:
    """Look up WHOIS for ``args['domain']``.

    Returns ``findings_total`` = the count of non-empty fields we found on
    the record. A domain with no WHOIS response (parked, TLD unsupported)
    is a step *success* with zero findings — the technique ran; there's
    just nothing to report.
    """
    try:
        import whois  # type: ignore[import-not-found]
    except ImportError:
        return StepResult(ok=False, error="python-whois not installed")

    domain = args.get("domain") or scope_context
    if not domain:
        return StepResult(ok=False, error="no domain in args/scope_context")

    try:
        record = whois.whois(domain)
    except Exception as exc:  # noqa: BLE001 - upstream errors are opaque
        return StepResult(ok=False, error=f"whois lookup failed: {exc}")

    if record is None:
        return StepResult(ok=True, findings_total=0, data={"raw": None})

    populated = 0
    payload: dict[str, Any] = {}
    for key in _INTERESTING_KEYS:
        value = record.get(key) if hasattr(record, "get") else getattr(record, key, None)
        if value:
            populated += 1
            payload[key] = str(value) if not isinstance(value, list) else [str(v) for v in value]

    return StepResult(
        ok=True,
        findings_new=populated,
        findings_total=populated,
        data={"record": payload},
    )
