"""Tests for the AI summary-rewrite guardrail (roadmap #1).

The fabrication risk is the load-bearing concern: the rewrite must
refine the analyst's OWN draft, not invent technical details. The LLM
call itself is integration-dependent, so these tests pin the prompt
contract instead — the system prompt forbids introducing facts, and the
user prompt carries the draft verbatim.
"""
from __future__ import annotations

from app.services.triage import (
    _REWRITE_SYSTEM_PROMPT,
    _build_rewrite_user_prompt,
)


def test_rewrite_system_prompt_forbids_introducing_facts() -> None:
    p = _REWRITE_SYSTEM_PROMPT
    # Must explicitly tell the model NOT to add technical details.
    assert "NOT" in p or "only" in p.lower()
    for forbidden in ("CVE", "tool", "port"):
        # the banned-additions list calls these out by name
        assert forbidden in p, f"guardrail should mention {forbidden!r}"


def test_rewrite_user_prompt_carries_draft_verbatim() -> None:
    draft = "Weak TLS config on mail.example.com allows downgrade."
    prompt = _build_rewrite_user_prompt(draft)
    assert draft in prompt


def test_triage_and_rewrite_prompts_differ() -> None:
    # Triage generates from scratch; rewrite refines a draft. Their system
    # prompts must be distinct so rewrite carries the no-invent constraint.
    from app.services.triage import _SYSTEM_PROMPT

    assert _REWRITE_SYSTEM_PROMPT != _SYSTEM_PROMPT
