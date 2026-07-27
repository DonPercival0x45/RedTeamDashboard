"""Shared conservative redaction for durable text and security logs."""

from __future__ import annotations

import re

_SENSITIVE_KEY = (
    r"api[_-]?key|apikey|authorization|cookie|credential|password|"
    r"private[_-]?key|secret|token"
)
_SENSITIVE_ASSIGNMENT = re.compile(
    rf"(?i)\b({_SENSITIVE_KEY})(\s*[=:]\s*|\s+)"
    r"(bearer\s+[^\s,;&]+|basic\s+[^\s,;&]+|\"[^\"]*\"|'[^']*'|[^\s,;&]+)"
)
_QUOTED_JSON_SECRET = re.compile(
    rf"(?i)([\"'](?:{_SENSITIVE_KEY})[\"']\s*:\s*)"
    r"([\"'][^\"']*[\"']|[^,}\]\s]+)"
)
_URI_PASSWORD = re.compile(r"(?i)(://[^:/\s]+:)([^@/\s]+)(@)")
_BEARER_VALUE = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]+")


def redact_sensitive_text(
    value: str | None,
    *,
    max_chars: int | None = None,
) -> str | None:
    """Redact common credential shapes without claiming perfect DLP."""
    if not value:
        return None
    text = str(value)
    redacted = text if max_chars is None else text[:max_chars]
    redacted = _QUOTED_JSON_SECRET.sub(
        lambda match: f'{match.group(1)}"[REDACTED]"',
        redacted,
    )
    redacted = _SENSITIVE_ASSIGNMENT.sub(
        lambda match: f"{match.group(1)}=[REDACTED]",
        redacted,
    )
    redacted = _URI_PASSWORD.sub(r"\1[REDACTED]\3", redacted)
    return _BEARER_VALUE.sub(
        lambda match: f"{match.group(1)} [REDACTED]",
        redacted,
    )
