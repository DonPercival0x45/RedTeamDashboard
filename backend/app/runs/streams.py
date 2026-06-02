"""Redis Streams name helpers.

Per-engagement streams give us natural isolation:
- inbound  ``runs:{engagement_id}:in``      — run.start / run.resume commands
- outbound ``runs:{engagement_id}:events``  — lifecycle events for the SSE feed

The consumer group name is shared across worker replicas so message delivery
fans out instead of duplicating.
"""
from __future__ import annotations

import uuid

CONSUMER_GROUP = "osint-workers"

_INBOUND_PREFIX = "runs:"
_INBOUND_SUFFIX = ":in"
_OUTBOUND_SUFFIX = ":events"


def inbound_stream(engagement_id: uuid.UUID | str) -> str:
    return f"{_INBOUND_PREFIX}{engagement_id}{_INBOUND_SUFFIX}"


def outbound_stream(engagement_id: uuid.UUID | str) -> str:
    return f"{_INBOUND_PREFIX}{engagement_id}{_OUTBOUND_SUFFIX}"


def engagement_id_from_inbound(stream_name: str) -> uuid.UUID:
    if not stream_name.startswith(_INBOUND_PREFIX) or not stream_name.endswith(
        _INBOUND_SUFFIX
    ):
        raise ValueError(f"not an inbound stream name: {stream_name!r}")
    raw = stream_name[len(_INBOUND_PREFIX) : -len(_INBOUND_SUFFIX)]
    return uuid.UUID(raw)
