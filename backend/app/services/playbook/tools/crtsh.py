"""crt.sh certificate transparency stub — A3b placeholder.

Real impl hits ``https://crt.sh/?q=<domain>&output=json`` and unions the
resulting subdomains. Stubbed here so the seam ships in A3b; the HTTP call
lands with the outbound-networking policy decision (some deployments block
egress to crt.sh; we may proxy via a known-good gateway).
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
                "crt.sh stub — real cert-transparency scan lands with the "
                "egress-policy pick (follow-up to A3b)"
            ),
            "domain": domain,
        },
    )
