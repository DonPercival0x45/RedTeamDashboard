"""Inbound command and outbound event vocabulary.

Inbound (consumed off ``runs:{eid}:in``):
- ``run.start``  { thread_id, prompt }
- ``run.resume`` { thread_id, approved, edited_args?, reason? }

Outbound (published to ``runs:{eid}:events``):
- ``run.started``       { thread_id, prompt, run_slug? }
- ``approval.pending``  { thread_id, tool, args, risk, scope, tool_call_id }
- ``tool.denied``       { thread_id, tool, args, reason, scope }
- ``tool.auto_approved``{ thread_id, tool, args, risk, authorization_id }
- ``tool.executed``     { thread_id, tool, args, ok, elapsed_ms,
                          findings_emitted, error, data_preview }
- ``llm.responded``     { thread_id, tokens_in, tokens_out, elapsed_ms,
                          tool_call_count, tool_calls, content_preview }
- ``finding.created``   { thread_id, tool, args, data, target, severity, title, finding_id }
- ``run.completed``     { thread_id }
- ``run.errored``       { thread_id, error }

Wire format on Redis is a single ``data`` field containing the JSON-encoded
envelope. Keeping it in one field means consumers don't need to know the
schema to read messages — they just decode JSON.
"""
from __future__ import annotations

import json
from typing import Any

INBOUND_TYPES = frozenset({"run.start", "run.resume"})
EVENT_TYPES = frozenset(
    {
        "run.started",
        "approval.pending",
        "tool.denied",
        "tool.auto_approved",
        # v1.4.3: observability trace events. tool.executed carries the
        # exact command the agent ran; llm.responded carries token
        # usage + tool_calls + a response preview so a silent
        # 0-finding run tells the analyst why.
        "tool.executed",
        "llm.responded",
        "finding.created",
        "finding.updated",
        "run.completed",
        "run.errored",
    }
)


def encode_event(payload: dict[str, Any]) -> dict[str, str]:
    if payload.get("type") not in EVENT_TYPES:
        raise ValueError(f"unknown event type: {payload.get('type')!r}")
    return {"data": json.dumps(payload, default=str)}


def encode_command(payload: dict[str, Any]) -> dict[str, str]:
    if payload.get("type") not in INBOUND_TYPES:
        raise ValueError(f"unknown inbound type: {payload.get('type')!r}")
    return {"data": json.dumps(payload, default=str)}


def decode_envelope(fields: dict[str, Any]) -> dict[str, Any]:
    raw = fields.get("data") if "data" in fields else fields.get(b"data")
    if raw is None:
        raise ValueError("envelope missing 'data' field")
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("envelope payload is not a JSON object")
    return payload
