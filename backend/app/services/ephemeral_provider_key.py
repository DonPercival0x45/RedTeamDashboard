"""Redis-backed ephemeral BYO key store.

Replaces the persistent ``user_provider_keys`` table. Locked 2026-06-29
after discussion with Kendall: keys never live at rest. The analyst
uploads at session start; the entry sits in Redis under a per-user hash
with a sliding TTL (refreshed on every read or write). On TTL expiry,
sign-out, or Redis restart, the keys are gone — only the analyst who
uploaded them ever sees their plaintext.

Storage shape::

    HSET provider_keys:<user_id> <key_id> '<json blob>'
    EXPIRE provider_keys:<user_id> <ttl_seconds>

The JSON blob carries the same fields the old SQL row had::

    {
        "id":         "<uuid>",
        "kind":       "model_provider" | "mcp_server",
        "name":       "My Anthropic",
        "provider":   "anthropic",
        "is_local":   false,
        "models":     ["claude-opus-4-7"],
        "endpoint":   "https://...",        # nullable
        "api_key":    "sk-ant-...",         # NEVER returned over the API
        "key_last4":  "...XYZ",
        "extra":      {},
        "created_at": "2026-06-29T...",
        "updated_at": "2026-06-29T..."
    }

The plaintext API key sits unencrypted in Redis. That's deliberate —
encrypting it would require a master key colocated with the cipher,
which is no better than plaintext for an attacker who already has
process-level access to the backend. The defense is ephemerality + TTL,
not encryption-at-rest.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import redis as redis_lib

from app.core.config import settings


# Public exception kept under the old name so call sites in agents / API
# layers don't need to be rewritten beyond the import path.
class NoProviderKeyError(Exception):
    """The acting user has no ephemeral key cached for the requested provider.

    Raised by :func:`resolve_for_user`. The HTTP layer maps this to a
    400 with a pointer to ``/settings/keys``; the worker layer surfaces
    it as ``run.errored`` so the analyst sees a clear "your session
    expired, re-upload your key" message.
    """

    def __init__(self, *, user_id: uuid.UUID, provider: str) -> None:
        self.user_id = user_id
        self.provider = provider
        super().__init__(
            f"no ephemeral provider key cached for '{provider}' on user "
            f"{user_id} — re-upload at /settings/keys"
        )


@dataclass(frozen=True, slots=True)
class ResolvedProviderKey:
    """Outcome of a successful resolution. ``api_key`` is plaintext (or
    ``None`` for local providers); ``endpoint`` is the provider-specific
    base URL (or ``None`` to use the SDK default)."""

    row_id: uuid.UUID
    name: str
    provider: str
    is_local: bool
    api_key: str | None
    endpoint: str | None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _key(user_id: uuid.UUID) -> str:
    return f"provider_keys:{user_id}"


def _ttl() -> int:
    return settings.provider_key_ttl_seconds


def _touch_ttl(redis: redis_lib.Redis, user_id: uuid.UUID) -> None:
    """Refresh the sliding TTL on the per-user hash. Safe no-op if the
    hash doesn't exist (EXPIRE on a missing key just returns 0).

    v1.25.0: when the configured TTL is <= 0, this becomes a no-op and
    the key persists until an explicit delete / rotate. Also strips any
    residual TTL on the hash so a redeploy that flips the config takes
    effect immediately for keys already cached under the old regime.
    """
    ttl = _ttl()
    if ttl > 0:
        redis.expire(_key(user_id), ttl)
    else:
        # PERSIST removes any existing TTL; no-op if none was set.
        redis.persist(_key(user_id))


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _entry_to_dict(entry: dict[str, Any]) -> dict[str, Any]:
    """Defensive copy + normalize types we care about (UUID, datetime)."""
    out = dict(entry)
    if isinstance(out.get("id"), uuid.UUID):
        out["id"] = str(out["id"])
    return out


# ---------------------------------------------------------------------------
# Public store API
# ---------------------------------------------------------------------------


def store(
    redis: redis_lib.Redis,
    *,
    user_id: uuid.UUID,
    entry: dict[str, Any],
) -> dict[str, Any]:
    """Persist (or overwrite) one entry under the user's hash.

    Refreshes the TTL on every write. ``entry`` must carry a stable
    ``id`` (UUID or string-UUID) — caller mints it via ``uuid7``.
    Returns the stored entry (round-tripped through JSON so timestamps
    are normalized).
    """
    normalized = _entry_to_dict(entry)
    if "id" not in normalized:
        raise ValueError("entry missing 'id'")
    if "created_at" not in normalized:
        normalized["created_at"] = _now_iso()
    normalized["updated_at"] = _now_iso()

    redis.hset(_key(user_id), normalized["id"], json.dumps(normalized))
    _touch_ttl(redis, user_id)
    return normalized


def list_all(
    redis: redis_lib.Redis, *, user_id: uuid.UUID
) -> list[dict[str, Any]]:
    """Return every entry under the user's hash. Refreshes TTL."""
    raw = redis.hgetall(_key(user_id)) or {}
    out: list[dict[str, Any]] = []
    for value in raw.values():
        try:
            out.append(json.loads(value))
        except json.JSONDecodeError:
            # Malformed row — skip rather than blow up the list call. A
            # future audit could surface these as warnings.
            continue
    if out:
        _touch_ttl(redis, user_id)
    # Stable order by created_at so the UI doesn't shuffle on every refetch.
    out.sort(key=lambda e: e.get("created_at") or "")
    return out


def get_one(
    redis: redis_lib.Redis, *, user_id: uuid.UUID, key_id: uuid.UUID
) -> dict[str, Any] | None:
    raw = redis.hget(_key(user_id), str(key_id))
    if raw is None:
        return None
    _touch_ttl(redis, user_id)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def delete(
    redis: redis_lib.Redis, *, user_id: uuid.UUID, key_id: uuid.UUID
) -> bool:
    """Remove one entry. Returns True if a row was deleted, False if it
    wasn't present. Refreshes TTL when something remains in the hash."""
    removed = redis.hdel(_key(user_id), str(key_id))
    if redis.exists(_key(user_id)):
        _touch_ttl(redis, user_id)
    return bool(removed)


def delete_all(redis: redis_lib.Redis, *, user_id: uuid.UUID) -> int:
    """Drop every entry for the user (sign-out flow). Returns the number
    of fields that were present."""
    count = redis.hlen(_key(user_id))
    redis.delete(_key(user_id))
    return int(count or 0)


# ---------------------------------------------------------------------------
# Resolution (used by Strategic / Tactical / Planner / worker)
# ---------------------------------------------------------------------------


def resolve_for_user(
    redis: redis_lib.Redis,
    *,
    user_id: uuid.UUID,
    provider: str,
    key_id: uuid.UUID | None = None,
    allowed_kinds: tuple[str, ...] = ("model_provider",),
) -> ResolvedProviderKey:
    """Return the user's entry for ``provider``.

    Selection rule:
      * ``key_id`` given — that exact entry (must have a kind in
        ``allowed_kinds``, belong to ``user_id``, and match ``provider``).
        Lets the analyst pick a specific key for a run instead of always
        taking the MRU one (roadmap #3 — "keys for specific tasks").
      * else — the most-recently-updated entry for ``provider`` (MRU),
        the original behavior.

    ``allowed_kinds`` defaults to ``("model_provider",)`` — the historical
    behavior for LLM key lookup. Tool-secret callers (v2.24.4 worker
    ``_resolve_tool_secrets``) pass ``("model_provider", "other")`` because
    the ``/settings/keys`` QuickAdd auto-flips tool-secret entries
    (freeipapi / ipinfo / wigle) to ``kind=other``. The safety story stays
    intact: mcp_server entries are never accepted regardless of the
    caller, and the default (LLM path) still excludes ``other``.

    Raises :class:`NoProviderKeyError` if none cached (or the named
    ``key_id`` isn't a valid entry for this provider under the allowed
    kinds).
    """
    provider_norm = provider.strip().lower()

    if key_id is not None:
        entry = get_one(redis, user_id=user_id, key_id=key_id)
        if (
            entry is None
            or entry.get("kind") not in allowed_kinds
            or (entry.get("provider") or "").lower() != provider_norm
        ):
            raise NoProviderKeyError(user_id=user_id, provider=provider_norm)
        if not entry.get("api_key") and not entry.get("is_local"):
            raise NoProviderKeyError(user_id=user_id, provider=provider_norm)
        return ResolvedProviderKey(
            row_id=uuid.UUID(str(entry["id"])),
            name=str(entry.get("name") or ""),
            provider=str(entry.get("provider") or provider_norm),
            is_local=bool(entry.get("is_local")),
            api_key=entry.get("api_key"),
            endpoint=entry.get("endpoint"),
        )

    candidates: list[dict[str, Any]] = []
    for entry in list_all(redis, user_id=user_id):
        if entry.get("kind") not in allowed_kinds:
            continue
        if (entry.get("provider") or "").lower() != provider_norm:
            continue
        candidates.append(entry)
    if not candidates:
        raise NoProviderKeyError(user_id=user_id, provider=provider_norm)

    # MRU: latest updated_at wins.
    candidates.sort(key=lambda e: e.get("updated_at") or "", reverse=True)
    winner = candidates[0]

    if not winner.get("api_key") and not winner.get("is_local"):
        # Non-local entry with no plaintext shouldn't happen (the schema
        # validator catches it on upload), but defend.
        raise NoProviderKeyError(user_id=user_id, provider=provider_norm)

    return ResolvedProviderKey(
        row_id=uuid.UUID(str(winner["id"])),
        name=str(winner.get("name") or ""),
        provider=str(winner.get("provider") or provider_norm),
        is_local=bool(winner.get("is_local")),
        api_key=winner.get("api_key"),
        endpoint=winner.get("endpoint"),
    )
