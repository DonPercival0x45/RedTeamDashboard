"""Subfinder stub — A3b placeholder.

Real subdomain enumeration lands in a follow-up: subfinder is a Go binary,
needs to be shipped in the backend image (or wrapped via a hosted API).
For A3b the seam is what matters — the ``run`` signature is real; the impl
is a canned no-op so the OSINT playbook can execute end-to-end today.

Returns an executable placeholder with zero findings and a note so the
coverage record carries visible provenance ("we ran a stub"). The explicit
``stub`` marker prevents this placeholder from satisfying baseline; real
wiring will populate findings once we pick the data source.
"""
from __future__ import annotations

from typing import Any

from app.services.playbook.executor import StepResult


def run(scope_context: str, args: dict[str, Any]) -> StepResult:
    domain = args.get("domain") or scope_context
    return StepResult(
        ok=True,
        findings_total=0,
        stub=True,
        data={
            "note": (
                "subfinder stub — real subdomain enumeration lands with the "
                "data-source pick (follow-up to A3b)"
            ),
            "domain": domain,
        },
    )
