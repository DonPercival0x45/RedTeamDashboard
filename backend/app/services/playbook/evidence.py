"""Redaction and bounded persistence for playbook execution evidence."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.playbook_execution import EvidenceArtifact, PlaybookStepExecution
from app.services.playbook.executor import StepResult
from app.services.redaction import redact_sensitive_text

MAX_ARGUMENT_BYTES = 32 * 1024
MAX_EVIDENCE_BYTES = 256 * 1024
MAX_ERROR_CHARS = 4000

_SENSITIVE_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "credential",
    "password",
    "private_key",
    "secret",
    "token",
)


def _is_sensitive_key(key: object) -> bool:
    normalized = str(key).strip().lower().replace("-", "_")
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def redact_json(value: Any, *, _depth: int = 0) -> Any:
    """Return a JSON-safe value with secret-like fields removed recursively."""
    if _depth >= 20:
        return "[maximum depth reached]"
    if isinstance(value, Mapping):
        return {
            str(key): (
                "[REDACTED]" if _is_sensitive_key(key) else redact_json(item, _depth=_depth + 1)
            )
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [redact_json(item, _depth=_depth + 1) for item in value]
    if isinstance(value, (bytes, bytearray)):
        return f"[{len(value)} bytes]"
    if isinstance(value, str):
        return redact_text(value, max_chars=None) or ""
    if value is None or isinstance(value, (int, float, bool)):
        return value
    return redact_sensitive_text(str(value)) or ""


def redact_text(
    value: str | None,
    *,
    max_chars: int | None = MAX_ERROR_CHARS,
) -> str | None:
    return redact_sensitive_text(value, max_chars=max_chars)


def bounded_json(
    value: Any,
    *,
    max_bytes: int,
) -> tuple[dict[str, Any], str, int, bool]:
    """Redact and bound JSON while retaining the full redacted digest/size."""
    redacted = redact_json(value)
    if not isinstance(redacted, dict):
        redacted = {"value": redacted}
    canonical = json.dumps(
        redacted,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()
    size = len(canonical)
    if size <= max_bytes:
        return redacted, digest, size, False

    # Store a readable prefix rather than an invalid partial JSON object. The
    # digest and size still identify the complete redacted output.
    preview = canonical[:max_bytes].decode("utf-8", errors="replace")
    return (
        {
            "_truncated": True,
            "_preview_json": preview,
            "_full_redacted_sha256": digest,
            "_full_redacted_size_bytes": size,
        },
        digest,
        size,
        True,
    )


def safe_arguments(value: Mapping[str, Any]) -> dict[str, Any]:
    payload, _digest, _size, _truncated = bounded_json(dict(value), max_bytes=MAX_ARGUMENT_BYTES)
    return payload


def persist_step_evidence(
    session: Session,
    *,
    engagement_id: uuid.UUID,
    run_id: uuid.UUID,
    execution: PlaybookStepExecution,
    tool_slug: str,
    target: str,
    result: StepResult,
    finding_id: uuid.UUID | None,
    captured_at: datetime | None = None,
) -> EvidenceArtifact:
    """Persist one redacted artifact without changing Finding semantics."""
    envelope = {
        "ok": result.ok,
        "stub": result.stub,
        "error": redact_text(result.error),
        "counts": {
            "findings_new": result.findings_new,
            "findings_unvalidated": result.findings_unvalidated,
            "findings_high_severity": result.findings_high_severity,
            "findings_total": result.findings_total,
        },
        "data": result.data,
    }
    payload, digest, size, truncated = bounded_json(envelope, max_bytes=MAX_EVIDENCE_BYTES)
    artifact = EvidenceArtifact(
        engagement_id=engagement_id,
        playbook_run_id=run_id,
        playbook_step_execution_id=execution.id,
        finding_id=finding_id,
        kind="tool_output",
        source_tool=tool_slug,
        target=target,
        payload=payload,
        sha256=digest,
        size_bytes=size,
        truncated=truncated,
        redacted=True,
        captured_at=captured_at or datetime.now(tz=UTC),
    )
    session.add(artifact)
    session.flush()
    return artifact
