"""Breach corpus lookup stub — A3b placeholder.

Real impl integrates a breach-data provider (HaveIBeenPwned domain search,
DeHashed, IntelX, …) — each needs an analyst BYO key + rate-limit handling
+ per-provider result normalization. Stubbed for A3b so the OSINT playbook
runs end-to-end; the provider pick + key wiring lands as a follow-up.
"""
from __future__ import annotations

from typing import Any

from app.services.playbook.executor import StepResult


def run(scope_context: str, args: dict[str, Any]) -> StepResult:
    domain = args.get("domain") or scope_context
    return StepResult(
        ok=True,
        findings_total=0,
        data={
            "note": (
                "breach-lookup stub — real corpus lookup lands with the "
                "provider pick + BYO-key wiring (follow-up to A3b)"
            ),
            "domain": domain,
        },
    )
