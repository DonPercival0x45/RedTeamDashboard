"""Small Redis Streams pending-entry recovery helpers."""
from __future__ import annotations

import json
from typing import Any


def claim_stale(
    redis_client: Any,
    *,
    stream: str,
    group: str,
    consumer: str,
    min_idle_ms: int,
    count: int,
) -> list[tuple[str, dict[str, Any]]]:
    """XAUTOCLAIM one bounded batch, tolerating Redis response variants."""
    result = redis_client.xautoclaim(
        stream,
        group,
        consumer,
        min_idle_ms,
        start_id="0-0",
        count=count,
    )
    if not result or len(result) < 2:
        return []
    return list(result[1] or [])


def delivery_count(
    redis_client: Any, *, stream: str, group: str, message_id: str
) -> int:
    rows = redis_client.xpending_range(
        stream,
        group,
        min=message_id,
        max=message_id,
        count=1,
    )
    if not rows:
        return 1
    row = rows[0]
    return int(row.get("times_delivered", row.get(b"times_delivered", 1)))


def dead_letter(
    redis_client: Any,
    *,
    stream: str,
    group: str,
    message_id: str,
    fields: dict[str, Any],
    error: str,
    attempts: int,
) -> str:
    """Copy a poison entry to a group-specific DLQ, then ACK the source."""
    dlq = f"{stream}:dead:{group}"
    payload = {
        "source_stream": stream,
        "source_message_id": message_id,
        "consumer_group": group,
        "attempts": attempts,
        "error": error[:2000],
        "fields": {
            (key.decode("utf-8") if isinstance(key, bytes) else str(key)): (
                value.decode("utf-8") if isinstance(value, bytes) else value
            )
            for key, value in fields.items()
        },
    }
    dlq_id = redis_client.xadd(
        dlq, {"data": json.dumps(payload, default=str)}
    )
    redis_client.xack(stream, group, message_id)
    return str(dlq_id)
