"""DNS record inventory tool — real dnspython query.

Satisfies ``osint.domain.dns`` / ``recon.passive.dns`` methodology nodes.
Queries the standard record types the seed methodologies ask for
(A / AAAA / MX / TXT — SPF/DMARC live inside TXT). Each successfully
returned answer contributes to ``findings_total``; DNS failures are not
findings (they mean the record type doesn't exist), so we count only
non-empty answers.

Deterministic: no LLM, no external key. The dnspython resolver default
uses the system DNS unless the caller pins ``nameservers``.
"""
from __future__ import annotations

import logging
from typing import Any

from app.services.playbook.executor import StepResult

logger = logging.getLogger(__name__)

_QUERY_TYPES = ("A", "AAAA", "MX", "TXT", "NS")


def run(scope_context: str, args: dict[str, Any]) -> StepResult:
    """Enumerate DNS records for the domain in ``args['domain']``.

    Returns:
        StepResult with ``findings_total`` = the count of DNS answers
        returned (across all record types), or ``ok=False`` when the
        resolver is entirely uncooperative (e.g. no nameservers). A
        domain with zero records is still ``ok=True`` (the technique
        ran; the answer was "nothing" — architecture-answers Q3's
        "satisfied includes a clean 'found nothing' result").
    """
    try:
        import dns.resolver  # type: ignore[import-untyped]
    except ImportError:
        return StepResult(ok=False, error="dnspython not installed")

    domain = args.get("domain") or scope_context
    if not domain:
        return StepResult(ok=False, error="no domain in args/scope_context")

    resolver = dns.resolver.Resolver()
    resolver.timeout = 3
    resolver.lifetime = 5

    total = 0
    records: dict[str, list[str]] = {}
    hard_error: str | None = None
    for qtype in _QUERY_TYPES:
        try:
            answers = resolver.resolve(domain, qtype)
        except dns.resolver.NoAnswer:
            records[qtype] = []
            continue
        except dns.resolver.NXDOMAIN:
            return StepResult(ok=False, error=f"NXDOMAIN {domain}")
        except (dns.exception.DNSException, OSError) as exc:
            # Per-type failures don't kill the run — record and continue.
            hard_error = f"{qtype}: {exc}"
            continue
        rendered = [r.to_text() for r in answers]
        records[qtype] = rendered
        total += len(rendered)

    if total == 0 and hard_error is not None:
        return StepResult(ok=False, error=hard_error)

    return StepResult(
        ok=True,
        findings_new=total,
        findings_total=total,
        data={"records": records},
    )
