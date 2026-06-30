#!/usr/bin/env python3
"""Translate raw commit subjects into non-technical release notes.

Run by the ``release.yml`` workflow once per tag. Reads commit subjects on
stdin (one per line, between the previous tag and this one), then calls
the Anthropic API to rewrite them as a short, analyst-readable
"What's Changed" markdown block with three sections:

  * Features Added
  * Features Removed
  * Bugs Fixed

If ``ANTHROPIC_API_KEY`` is not set, the ``anthropic`` SDK isn't
installed, or the API call fails, the script writes an empty string to
stdout and exits 0 — so the release pipeline never fails on the
release-notes step. The hand-written body in ``release.yml`` and the
auto-generated PR list from ``softprops/action-gh-release`` still ship.
"""

from __future__ import annotations

import os
import sys

PROMPT = """You are writing user-facing release notes for an authorized
security-engagement management dashboard. The reader is a security analyst
using the dashboard, not an engineer building it — describe behavior
changes, not implementation. Avoid jargon like "refactor", "ruff", "alembic",
"migration", "schema". Keep each bullet under 14 words. Be concrete:
say what the analyst can now do, what's gone, or what's fixed.

Output ONLY markdown with this exact shape (omit empty sections entirely):

## What's Changed in {tag}

### Features Added
- bullet
- bullet

### Features Removed
- bullet

### Bugs Fixed
- bullet

Rules:
- Skip a section entirely if there are no qualifying items — do not write
  "none".
- Group multiple commits about the same feature into ONE bullet.
- Don't mention commit hashes, PR numbers, or contributor names.
- Don't mention internal tooling (CI, ruff, tests, Docker, Alembic).
- If a commit is purely a chore (version bump, dep bump, formatting),
  drop it.

Raw commit subjects between {prev} and {tag}:

{commits}
"""

MODEL = os.environ.get("RELEASE_NOTES_MODEL", "claude-opus-4-7")


def _log(msg: str) -> None:
    sys.stderr.write(f"[release-notes] {msg}\n")


def main() -> int:
    commits = sys.stdin.read().strip()
    if not commits:
        _log("no commits on stdin — nothing to summarize")
        return 0

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        _log("ANTHROPIC_API_KEY not set — skipping LLM rewrite")
        return 0

    try:
        from anthropic import Anthropic
    except ImportError:
        _log("anthropic SDK not installed — skipping")
        return 0

    tag = os.environ.get("RELEASE_TAG", "this release")
    prev = os.environ.get("PREVIOUS_TAG", "") or "the previous release"

    client = Anthropic(api_key=api_key)
    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            messages=[
                {
                    "role": "user",
                    "content": PROMPT.format(
                        prev=prev,
                        tag=tag,
                        commits=commits,
                    ),
                },
            ],
        )
    except Exception as exc:
        _log(f"anthropic call failed ({type(exc).__name__}): {exc}")
        return 0

    out: list[str] = []
    for block in resp.content:
        text = getattr(block, "text", "")
        if text:
            out.append(text)
    body = "".join(out).strip()
    if body:
        sys.stdout.write(body + "\n\n")
        _log(f"emitted {len(body)} chars of release notes")
    else:
        _log("LLM returned empty body — skipping")
    return 0


if __name__ == "__main__":
    sys.exit(main())
