"""HTTP surface for ephemeral BYO provider keys.

Endpoints (all scoped to the acting user — no admin path)::

    GET    /me/provider-keys                   -> list (masked)
    POST   /me/provider-keys                   -> create one
    POST   /me/provider-keys/import            -> bulk upload from a JSON blob
    GET    /me/provider-keys/{id}              -> read one (masked)
    PATCH  /me/provider-keys/{id}              -> rename / rotate / re-target
    DELETE /me/provider-keys/{id}              -> remove one
    DELETE /me/provider-keys                   -> remove ALL (sign-out flow)

The plaintext API key NEVER leaves the backend in a response. The only
way to view the plaintext is to re-upload.

**Ephemeral storage** (locked 2026-06-29): entries live in a per-user
Redis hash with a sliding TTL. They evaporate on TTL expiry, sign-out,
Redis restart, or explicit delete. Re-upload on every new analyst session.
"""
from __future__ import annotations

import uuid
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Response, status

from app.api.deps import CurrentNonGuestUser, DbSession, RedisClient
from app.db.base import uuid7
from app.models import ActorType, AuditLog
from app.schemas.provider_key import (
    ProviderKeyEntry,
    ProviderKeyImport,
    ProviderKeyImportErrorRow,
    ProviderKeyImportResult,
    ProviderKeyRead,
    ProviderKeyUpdate,
)
from app.services import ephemeral_provider_key as keys
from app.services.secret_box import last4

logger = structlog.get_logger(__name__)

router = APIRouter()


# ── helpers ──────────────────────────────────────────────────────────────


def _entry_to_read(entry: dict[str, Any]) -> ProviderKeyRead:
    """Project a Redis entry into the read shape (masked — no plaintext)."""
    return ProviderKeyRead.model_validate(
        {
            "id": entry["id"],
            "user_id": entry["user_id"],
            "kind": entry.get("kind", "model_provider"),
            "name": entry["name"],
            "provider": entry["provider"],
            "is_local": bool(entry.get("is_local", False)),
            "models": list(entry.get("models") or []),
            "endpoint": entry.get("endpoint"),
            "key_last4": entry.get("key_last4"),
            "extra": dict(entry.get("extra") or {}),
            "created_at": entry["created_at"],
            "updated_at": entry["updated_at"],
        }
    )


def _new_entry(user_id: uuid.UUID, body: ProviderKeyEntry) -> dict[str, Any]:
    return {
        "id": str(uuid7()),
        "user_id": str(user_id),
        "kind": body.kind.value,
        "name": body.name.strip(),
        "provider": body.provider.strip().lower(),
        "is_local": bool(body.is_local),
        "models": list(body.models),
        "endpoint": body.endpoint.strip() if body.endpoint else None,
        "api_key": body.api_key,
        "key_last4": last4(body.api_key) if body.api_key else None,
        "extra": dict(body.extra),
    }


def _audit(
    session: DbSession,
    user_id: uuid.UUID,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    session.add(
        AuditLog(
            engagement_id=None,
            actor_type=ActorType.user,
            actor_id=str(user_id),
            event_type=event_type,
            payload=payload,
        )
    )
    session.commit()


def _names_for_user(redis: RedisClient, user_id: uuid.UUID) -> set[str]:
    return {
        e["name"]
        for e in keys.list_all(redis, user_id=user_id)
        if "name" in e
    }


# ── list / read ──────────────────────────────────────────────────────────


@router.get("/me/provider-keys", response_model=list[ProviderKeyRead])
def list_my_provider_keys(
    redis: RedisClient, user: CurrentNonGuestUser
) -> list[ProviderKeyRead]:
    return [
        _entry_to_read(e) for e in keys.list_all(redis, user_id=user.id)
    ]


@router.get(
    "/me/provider-keys/{key_id}", response_model=ProviderKeyRead
)
def get_my_provider_key(
    key_id: uuid.UUID, redis: RedisClient, user: CurrentNonGuestUser
) -> ProviderKeyRead:
    entry = keys.get_one(redis, user_id=user.id, key_id=key_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="provider key not found")
    return _entry_to_read(entry)


# ── create one ───────────────────────────────────────────────────────────


@router.post(
    "/me/provider-keys",
    response_model=ProviderKeyRead,
    status_code=status.HTTP_201_CREATED,
)
def create_my_provider_key(
    body: ProviderKeyEntry,
    session: DbSession,
    redis: RedisClient,
    user: CurrentNonGuestUser,
) -> ProviderKeyRead:
    if body.name.strip() in _names_for_user(redis, user.id):
        raise HTTPException(
            status_code=409,
            detail=f"a provider key named '{body.name}' already exists",
        )
    entry = _new_entry(user.id, body)
    stored = keys.store(redis, user_id=user.id, entry=entry)
    _audit(
        session,
        user.id,
        "provider_key.created",
        {
            "id": stored["id"],
            "name": stored["name"],
            "provider": stored["provider"],
            "kind": stored["kind"],
            "is_local": stored["is_local"],
        },
    )
    return _entry_to_read(stored)


# ── bulk import ──────────────────────────────────────────────────────────


@router.post(
    "/me/provider-keys/import", response_model=ProviderKeyImportResult
)
def import_my_provider_keys(
    body: ProviderKeyImport,
    session: DbSession,
    redis: RedisClient,
    user: CurrentNonGuestUser,
) -> ProviderKeyImportResult:
    existing = _names_for_user(redis, user.id)

    created: list[dict[str, Any]] = []
    duplicates: list[ProviderKeyImportErrorRow] = []
    errors: list[ProviderKeyImportErrorRow] = []

    for index, entry in enumerate(body.providers):
        name = entry.name.strip()
        if name in existing:
            duplicates.append(
                ProviderKeyImportErrorRow(
                    index=index,
                    name=entry.name,
                    reason=(
                        "a provider key with this name already exists; "
                        "delete or PATCH to rotate"
                    ),
                )
            )
            continue
        try:
            stored = keys.store(
                redis,
                user_id=user.id,
                entry=_new_entry(user.id, entry),
            )
            existing.add(name)
            created.append(stored)
        except Exception as exc:  # noqa: BLE001 — any failure → report
            logger.warning(
                "provider_key.import_row_failed",
                index=index,
                name=entry.name,
                error=str(exc),
            )
            errors.append(
                ProviderKeyImportErrorRow(
                    index=index,
                    name=entry.name,
                    reason=str(exc)[:500],
                )
            )

    if created:
        _audit(
            session,
            user.id,
            "provider_key.imported",
            {
                "created_count": len(created),
                "duplicate_count": len(duplicates),
                "error_count": len(errors),
            },
        )

    return ProviderKeyImportResult(
        created=[_entry_to_read(e) for e in created],
        errors=errors,
        duplicates=duplicates,
    )


# ── update / delete ──────────────────────────────────────────────────────


@router.patch(
    "/me/provider-keys/{key_id}", response_model=ProviderKeyRead
)
def update_my_provider_key(
    key_id: uuid.UUID,
    body: ProviderKeyUpdate,
    session: DbSession,
    redis: RedisClient,
    user: CurrentNonGuestUser,
) -> ProviderKeyRead:
    entry = keys.get_one(redis, user_id=user.id, key_id=key_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="provider key not found")

    rotated = False
    if body.name is not None:
        new_name = body.name.strip()
        if new_name != entry.get("name") and new_name in _names_for_user(
            redis, user.id
        ):
            raise HTTPException(
                status_code=409,
                detail="another provider key already uses that name",
            )
        entry["name"] = new_name
    if body.models is not None:
        entry["models"] = list(body.models)
    if body.endpoint is not None:
        entry["endpoint"] = body.endpoint.strip() or None
    if body.api_key is not None:
        entry["api_key"] = body.api_key
        entry["key_last4"] = last4(body.api_key)
        rotated = True
    if body.extra is not None:
        entry["extra"] = dict(body.extra)

    stored = keys.store(redis, user_id=user.id, entry=entry)
    _audit(
        session,
        user.id,
        "provider_key.updated",
        {
            "id": stored["id"],
            "rotated": rotated,
            "fields": sorted(body.model_dump(exclude_unset=True)),
        },
    )
    return _entry_to_read(stored)


@router.delete(
    "/me/provider-keys/{key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_my_provider_key(
    key_id: uuid.UUID,
    session: DbSession,
    redis: RedisClient,
    user: CurrentNonGuestUser,
) -> Response:
    entry = keys.get_one(redis, user_id=user.id, key_id=key_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="provider key not found")
    keys.delete(redis, user_id=user.id, key_id=key_id)
    _audit(
        session,
        user.id,
        "provider_key.deleted",
        {
            "id": str(key_id),
            "name": entry.get("name"),
            "provider": entry.get("provider"),
        },
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/me/provider-keys",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_all_my_provider_keys(
    session: DbSession,
    redis: RedisClient,
    user: CurrentNonGuestUser,
) -> Response:
    """Wipe every cached key for the acting user. Called by the frontend
    on sign-out so a tab close doesn't leave plaintext keys reachable
    until TTL expiry."""
    count = keys.delete_all(redis, user_id=user.id)
    if count:
        _audit(
            session,
            user.id,
            "provider_key.flushed_all",
            {"removed_count": count},
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
