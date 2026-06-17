"""HTTP surface for analyst-uploaded BYO provider keys.

Endpoints (all scoped to the acting user — no admin path)::

    GET    /me/provider-keys                   -> list (masked)
    POST   /me/provider-keys                   -> create one
    POST   /me/provider-keys/import            -> bulk upload from a JSON blob
    GET    /me/provider-keys/{id}              -> read one (masked)
    PATCH  /me/provider-keys/{id}              -> rename / rotate / re-target
    DELETE /me/provider-keys/{id}              -> remove

The encrypted key NEVER leaves the backend in a response. The only way to
view the plaintext is to re-upload — by design, so a compromised viewer
session can't exfiltrate the key.
"""
from __future__ import annotations

import uuid
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentUser, DbSession
from app.models import (
    ActorType,
    AuditLog,
    UserProviderKey,
)
from app.schemas.provider_key import (
    ProviderKeyEntry,
    ProviderKeyImport,
    ProviderKeyImportErrorRow,
    ProviderKeyImportResult,
    ProviderKeyRead,
    ProviderKeyUpdate,
)
from app.services.secret_box import encrypt, last4

logger = structlog.get_logger(__name__)

router = APIRouter()


def _row_to_read(row: UserProviderKey) -> ProviderKeyRead:
    return ProviderKeyRead.model_validate(row)


def _build_row(
    user_id: uuid.UUID, entry: ProviderKeyEntry
) -> UserProviderKey:
    """Construct a UserProviderKey row from a parsed entry. Encrypts the key
    plaintext immediately so it never lingers as a string field on the row."""
    encrypted = encrypt(entry.api_key) if entry.api_key else None
    tail = last4(entry.api_key) if entry.api_key else None
    return UserProviderKey(
        user_id=user_id,
        kind=entry.kind,
        name=entry.name.strip(),
        provider=entry.provider.strip().lower(),
        is_local=entry.is_local,
        models=list(entry.models),
        endpoint=entry.endpoint.strip() if entry.endpoint else None,
        encrypted_key=encrypted,
        key_last4=tail,
        extra=dict(entry.extra),
    )


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


# ── list / read ──────────────────────────────────────────────────────────


@router.get("/me/provider-keys", response_model=list[ProviderKeyRead])
def list_my_provider_keys(
    session: DbSession, user: CurrentUser
) -> list[UserProviderKey]:
    rows = list(
        session.execute(
            select(UserProviderKey)
            .where(UserProviderKey.user_id == user.id)
            .order_by(UserProviderKey.created_at)
        ).scalars()
    )
    return rows


@router.get("/me/provider-keys/{key_id}", response_model=ProviderKeyRead)
def get_my_provider_key(
    key_id: uuid.UUID, session: DbSession, user: CurrentUser
) -> UserProviderKey:
    row = session.get(UserProviderKey, key_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="provider key not found")
    return row


# ── create one ───────────────────────────────────────────────────────────


@router.post(
    "/me/provider-keys",
    response_model=ProviderKeyRead,
    status_code=status.HTTP_201_CREATED,
)
def create_my_provider_key(
    body: ProviderKeyEntry, session: DbSession, user: CurrentUser
) -> UserProviderKey:
    row = _build_row(user.id, body)
    session.add(row)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"a provider key named '{body.name}' already exists",
        ) from exc
    _audit(
        session,
        user.id,
        "provider_key.created",
        {
            "id": str(row.id),
            "name": row.name,
            "provider": row.provider,
            "kind": row.kind.value,
            "is_local": row.is_local,
        },
    )
    session.commit()
    session.refresh(row)
    return row


# ── bulk import ──────────────────────────────────────────────────────────


@router.post(
    "/me/provider-keys/import", response_model=ProviderKeyImportResult
)
def import_my_provider_keys(
    body: ProviderKeyImport, session: DbSession, user: CurrentUser
) -> ProviderKeyImportResult:
    """Bulk-upload a list of provider entries.

    Each entry is validated independently — bad rows go to ``errors`` while
    the rest still import. Existing names (per the unique constraint) land
    in ``duplicates``; this slice does NOT overwrite — the analyst rotates
    via PATCH or deletes + re-imports.
    """
    existing_names = {
        n
        for n in session.execute(
            select(UserProviderKey.name).where(UserProviderKey.user_id == user.id)
        ).scalars()
    }

    created: list[UserProviderKey] = []
    duplicates: list[ProviderKeyImportErrorRow] = []
    errors: list[ProviderKeyImportErrorRow] = []

    for index, entry in enumerate(body.providers):
        if entry.name.strip() in existing_names:
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
            row = _build_row(user.id, entry)
            session.add(row)
            session.flush()
            existing_names.add(row.name)
            created.append(row)
        except Exception as exc:  # noqa: BLE001 — any persist failure is reported
            session.rollback()
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
    session.commit()
    for r in created:
        session.refresh(r)

    return ProviderKeyImportResult(
        created=[_row_to_read(r) for r in created],
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
    user: CurrentUser,
) -> UserProviderKey:
    row = session.get(UserProviderKey, key_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="provider key not found")

    rotated = False
    if body.name is not None:
        row.name = body.name.strip()
    if body.models is not None:
        row.models = list(body.models)
    if body.endpoint is not None:
        row.endpoint = body.endpoint.strip() or None
    if body.api_key is not None:
        row.encrypted_key = encrypt(body.api_key)
        row.key_last4 = last4(body.api_key)
        rotated = True
    if body.extra is not None:
        row.extra = dict(body.extra)

    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail="another provider key already uses that name",
        ) from exc

    _audit(
        session,
        user.id,
        "provider_key.updated",
        {
            "id": str(row.id),
            "rotated": rotated,
            "fields": sorted(body.model_dump(exclude_unset=True)),
        },
    )
    session.commit()
    session.refresh(row)
    return row


@router.delete(
    "/me/provider-keys/{key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_my_provider_key(
    key_id: uuid.UUID, session: DbSession, user: CurrentUser
) -> Response:
    row = session.get(UserProviderKey, key_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="provider key not found")
    name = row.name
    provider = row.provider
    session.delete(row)
    _audit(
        session,
        user.id,
        "provider_key.deleted",
        {"id": str(key_id), "name": name, "provider": provider},
    )
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
