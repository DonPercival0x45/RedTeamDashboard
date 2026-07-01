"""Shell source heuristic check (v0.13.0, layer 1 of the shell lane gate).

Mirrors :mod:`app.services.tool_ast_check` for the Python lane. Where
Python gets a proper AST allow-list, shell only gets regexes and a few
grammar tricks — bash has no round-trip parser worth carrying in this
codebase. Good enough to catch the load-bearing footguns: remote-
code-download-and-execute, permissive filesystem changes, unquoted
variable expansion in exec position, netcat listeners, base64-obfuscated
payloads.

Returns a structured result the admin approve UI renders the same way it
renders the AST check.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# Each pattern is a (name, regex, hint) tuple. ``name`` is the label
# shown in the admin UI. ``hint`` is a one-liner explaining why it
# tripped the check.
_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "curl-pipe-shell",
        re.compile(r"(?:curl|wget)\b[^\n]*\|\s*(?:bash|sh|zsh|ksh)\b"),
        "Downloads a remote script and executes it in the sandbox. Instead "
        "fetch the content, review, and run it as a separate step.",
    ),
    (
        "chmod-permissive",
        re.compile(r"chmod\s+(?:777|-R\s+777|\+rwx|a\+w)\b"),
        "Grants world-writable / world-executable permissions. Tools should "
        "not need this — sandbox filesystem is ephemeral.",
    ),
    (
        "eval-command-sub",
        re.compile(r"eval\s+(?:\$\(|`)"),
        "eval of a command substitution is a shell injection vector when "
        "any part of the substituted string is derived from tool arguments.",
    ),
    (
        "rm-rf-root",
        re.compile(r"rm\s+(?:-[a-zA-Z]*r[a-zA-Z]*f?|-[a-zA-Z]*f[a-zA-Z]*r?)\s+(/|/\*)\b"),
        "Recursively removes / or /*. Destructive; never appropriate for "
        "an analyst-lane tool.",
    ),
    (
        "dd-of-device",
        re.compile(r"\bdd\b[^\n]*of=/dev/(?:sd|nvme|hd|xvd)"),
        "Writes raw bytes to a block device. Destructive; container "
        "sandbox does not expose host devices in the first place, but "
        "the intent alone is a hard fail.",
    ),
    (
        "nc-listener",
        re.compile(r"\b(?:nc|ncat|netcat)\b[^\n]*\s-l\b"),
        "Opens a listening socket. Almost always a backdoor / reverse "
        "shell shape; tools should be one-shot outbound.",
    ),
    (
        "base64-piped-to-shell",
        re.compile(
            r"(?:echo|printf|cat)\s+[^\n]*\|\s*base64\s+(?:-d|--decode)\b[^\n]*\|\s*(?:bash|sh)\b"
        ),
        "Decodes a base64 blob and pipes it to a shell — canonical "
        "obfuscation of a downloaded payload.",
    ),
    (
        "unquoted-star-exec",
        re.compile(r"\b(?:eval|exec|source|\.)\s+\$[@*]\b"),
        "Expands unquoted \\$@ or \\$* directly into exec position. Any "
        "space or metachar in an arg becomes command injection.",
    ),
    (
        "setuid-chown-root",
        re.compile(r"\b(?:chown|chgrp)\s+(?:-R\s+)?(?:root|0):"),
        "Changes ownership to root. Not appropriate in a non-privileged "
        "sandbox container.",
    ),
    (
        "sudo-invocation",
        re.compile(r"\bsudo\b"),
        "sudo has no effect in the sandbox (no daemon; USER is fixed at "
        "build time). Presence usually signals the tool was authored "
        "for a different environment and won't behave as expected.",
    ),
)


@dataclass
class ShellCheckMatch:
    pattern: str
    line: int
    snippet: str
    hint: str


@dataclass
class ShellCheckResult:
    ok: bool
    matches: list[ShellCheckMatch] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "matches": [
                {
                    "pattern": m.pattern,
                    "line": m.line,
                    "snippet": m.snippet,
                    "hint": m.hint,
                }
                for m in self.matches
            ],
        }


def check_shell_source(source: str) -> ShellCheckResult:
    """Return a structured verdict on a shell script's safety.

    ``ok=True`` only when NO banned pattern matches. Comments (``# …``)
    are stripped before matching so a tool that documents its choices
    doesn't false-trip on prose examples.
    """
    matches: list[ShellCheckMatch] = []
    for lineno, raw_line in enumerate(source.splitlines(), start=1):
        stripped = _strip_shell_comment(raw_line)
        for name, pattern, hint in _PATTERNS:
            m = pattern.search(stripped)
            if m:
                matches.append(
                    ShellCheckMatch(
                        pattern=name,
                        line=lineno,
                        snippet=raw_line.strip()[:200],
                        hint=hint,
                    )
                )
    return ShellCheckResult(ok=not matches, matches=matches)


def _strip_shell_comment(line: str) -> str:
    """Drop everything after an unquoted ``#`` on a line. Naive — a
    single-quoted or double-quoted string with a ``#`` inside still gets
    truncated. Good enough for a hygiene check; false negatives here
    only mean a legit tool trips a warning, not the reverse."""
    in_single = False
    in_double = False
    out: list[str] = []
    for ch in line:
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            break
        out.append(ch)
    return "".join(out)
