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


def engagement_id_from_outbound(stream_name: str) -> uuid.UUID:
    if not stream_name.startswith(_INBOUND_PREFIX) or not stream_name.endswith(
        _OUTBOUND_SUFFIX
    ):
        raise ValueError(f"not an outbound stream name: {stream_name!r}")
    raw = stream_name[len(_INBOUND_PREFIX) : -len(_OUTBOUND_SUFFIX)]
    return uuid.UUID(raw)


# Per-thread LLM choice cache. ``start_run`` writes the chosen
# (provider, model) here so the approval endpoint can include it in
# ``run.resume`` envelopes without re-deriving it. TTL is generous —
# runs are short-lived (minutes) and the key gets overwritten on each
# new run for the same thread anyway.
_RUN_MODEL_KEY = "run:model:{thread_id}"
_RUN_MODEL_TTL_SECONDS = 6 * 60 * 60  # 6h


def run_model_key(thread_id: uuid.UUID | str) -> str:
    return _RUN_MODEL_KEY.format(thread_id=thread_id)


def store_run_model(
    redis_client: object,
    thread_id: uuid.UUID | str,
    *,
    provider: str,
    model_name: str,
    acting_user_id: uuid.UUID | str | None = None,
    key_id: uuid.UUID | str | None = None,
) -> None:
    """HSET the (provider, name) for a thread; TTL'd so abandoned runs expire.

    ``acting_user_id`` is the kicking analyst's id — stashed so the approval
    endpoint can carry it forward on ``run.resume`` envelopes (the worker
    re-resolves the BYO key off this id, NOT off the approving analyst).
    ``key_id`` (v1.4.12) optionally pins a specific cached provider key for
    the run; omitted = MRU selection at resolve time.
    """
    client: object = redis_client
    key = run_model_key(thread_id)
    mapping: dict[str, str] = {"provider": provider, "name": model_name}
    if acting_user_id is not None:
        mapping["acting_user_id"] = str(acting_user_id)
    if key_id is not None:
        mapping["key_id"] = str(key_id)
    client.hset(key, mapping=mapping)  # type: ignore[attr-defined]
    client.expire(key, _RUN_MODEL_TTL_SECONDS)  # type: ignore[attr-defined]


def load_run_model(
    redis_client: object,
    thread_id: uuid.UUID | str,
) -> dict[str, str] | None:
    """HGETALL — returns ``None`` if the thread has no recorded model.

    Returned dict carries ``provider``, ``name``, and (if present)
    ``acting_user_id``. Callers that need to compose a ``run.resume``
    envelope pull ``acting_user_id`` from here.
    """
    raw = redis_client.hgetall(run_model_key(thread_id))  # type: ignore[attr-defined]
    if not raw:
        return None
    out: dict[str, str] = {"provider": raw["provider"], "name": raw["name"]}
    if "acting_user_id" in raw:
        out["acting_user_id"] = raw["acting_user_id"]
    if "key_id" in raw:
        out["key_id"] = raw["key_id"]
    return out
