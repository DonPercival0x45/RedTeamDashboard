"""Run lifecycle primitives shared by worker and API layers.

Two seams:
- ``streams`` — Redis Streams naming helpers (per-Project).
- ``events`` — outbound event vocabulary (5 types) and envelope encoding.
"""
from app.runs.events import (
    EVENT_TYPES,
    INBOUND_TYPES,
    decode_envelope,
    encode_event,
)
from app.runs.streams import (
    CONSUMER_GROUP,
    engagement_id_from_inbound,
    inbound_stream,
    outbound_stream,
)

__all__ = [
    "CONSUMER_GROUP",
    "EVENT_TYPES",
    "INBOUND_TYPES",
    "decode_envelope",
    "encode_event",
    "engagement_id_from_inbound",
    "inbound_stream",
    "outbound_stream",
]
