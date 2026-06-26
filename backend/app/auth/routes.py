from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentAPIKey, CurrentUser, DbSession, RedisClient, RequireScope, hash_api_key
from app.models import (
    ActorType,
    APIKey,
    APIKeyScope,
    Approval,
    ApprovalStatus,
    AuditLog,
    Authorization,
    UserProviderKey,
)
from app.runs.events import encode_command
from app.runs.streams import inbound_stream, load_run_model
from app.schemas.api_key import APIKeyCreate, APIKeyMintResponse, APIKeyRead
from app.schemas.approval import ApprovalDecision, ApprovalRead
from app.schemas.authorization import AuthorizationRead
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


# ---------------------------------------------------------------------------
# api_keys router
# ---------------------------------------------------------------------------

"""API key management surface.

- ``POST   /api-keys``        — mint a new scoped key (admin only)
- ``GET    /api-keys``        — list keys (admin only); ``?active=true`` filters
- ``POST   /api-keys/{id}/revoke`` — revoke (idempotent; admin only)

Plaintext keys are returned once, by ``POST /api-keys`` only — the DB holds the
SHA-256 hash, so there is no way to recover a lost key (mint a replacement and
revoke the old one). Every mint and revoke writes an entry to ``audit_log``.
"""

api_keys_router = APIRouter()

# Prefix the random portion with ``xr_`` so a leaked key is easy to spot in
# logs and the user knows what they're looking at when they paste one in.
_KEY_PREFIX = "xr_"


def _generate_key() -> str:
    """Return a fresh 32-byte URL-safe random token, prefixed for identification."""
    return _KEY_PREFIX + secrets.token_urlsafe(32)


@api_keys_router.get(
    "/api-keys/me",
    response_model=APIKeyRead,
)
def my_api_key(api_key: CurrentAPIKey) -> APIKey:
    """Return the metadata of the calling key.

    Lets clients (e.g. the viewer) learn their own scope so they can render
    UI conditionally instead of trial-and-erroring 403s. No additional scope
    is required beyond a valid X-API-Key.
    """
    return api_key


@api_keys_router.post(
    "/api-keys",
    response_model=APIKeyMintResponse,
    status_code=201,
)
def mint_api_key(
    body: APIKeyCreate,
    session: DbSession,
    user: CurrentUser,
    _admin: Annotated[APIKey, Depends(RequireScope(APIKeyScope.admin))],
) -> APIKeyMintResponse:
    raw = _generate_key()
    row = APIKey(
        name=body.name,
        key_hash=hash_api_key(raw),
        scope=body.scope,
        created_by=user.id,
    )
    session.add(row)
    # Flush so ``row.id`` is materialized for the audit payload without
    # paying for a separate commit.
    session.flush()
    session.add(
        AuditLog(
            project_id=None,
            actor_type=ActorType.user,
            actor_id=str(user.id),
            event_type="api_key.created",
            payload={
                "api_key_id": str(row.id),
                "name": body.name,
                "scope": body.scope.value,
            },
        )
    )
    session.commit()
    session.refresh(row)

    return APIKeyMintResponse(
        id=row.id,
        name=row.name,
        scope=row.scope,
        created_by=row.created_by,
        revoked_at=row.revoked_at,
        last_used_at=row.last_used_at,
        created_at=row.created_at,
        key=raw,
    )


@api_keys_router.get(
    "/api-keys",
    response_model=list[APIKeyRead],
)
def list_api_keys(
    session: DbSession,
    _admin: Annotated[APIKey, Depends(RequireScope(APIKeyScope.admin))],
    active: Annotated[bool | None, Query()] = None,
) -> list[APIKey]:
    stmt = select(APIKey)
    if active is True:
        stmt = stmt.where(APIKey.revoked_at.is_(None))
    elif active is False:
        stmt = stmt.where(APIKey.revoked_at.is_not(None))
    stmt = stmt.order_by(APIKey.created_at.desc())
    return list(session.execute(stmt).scalars())


@api_keys_router.post(
    "/api-keys/{api_key_id}/revoke",
    response_model=APIKeyRead,
)
def revoke_api_key(
    api_key_id: UUID,
    session: DbSession,
    user: CurrentUser,
    _admin: Annotated[APIKey, Depends(RequireScope(APIKeyScope.admin))],
) -> APIKey:
    row = session.get(APIKey, api_key_id)
    if row is None:
        raise HTTPException(status_code=404, detail="API key not found")
    if row.revoked_at is not None:
        return row  # idempotent

    row.revoked_at = datetime.now(tz=UTC)
    row.revoked_by = user.id
    session.add(
        AuditLog(
            project_id=None,
            actor_type=ActorType.user,
            actor_id=str(user.id),
            event_type="api_key.revoked",
            payload={
                "api_key_id": str(row.id),
                "name": row.name,
                "scope": row.scope.value,
            },
        )
    )
    session.commit()
    session.refresh(row)
    return row


# ---------------------------------------------------------------------------
# approvals router
# ---------------------------------------------------------------------------

"""Approvals HTTP surface.

- ``GET  /engagements/{eid}/approvals?status=pending`` — list rows for an Project
- ``GET  /approvals/{id}``                            — fetch one
- ``POST /approvals/{id}/decision``                   — decide a pending approval

The decision endpoint updates the row in-place and pushes a ``run.resume``
envelope onto ``runs:{project_id}:in`` so the worker can resume the paused
LangGraph thread with ``Command(resume=...)``.
"""

approvals_router = APIRouter()


@approvals_router.get(
    "/engagements/{project_id}/approvals",
    response_model=list[ApprovalRead],
)
def list_approvals(
    project_id: UUID,
    session: DbSession,
    status: Annotated[ApprovalStatus | None, Query()] = None,
) -> list[Approval]:
    stmt = select(Approval).where(Approval.project_id == project_id)
    if status is not None:
        stmt = stmt.where(Approval.status == status)
    stmt = stmt.order_by(Approval.created_at.desc())
    return list(session.execute(stmt).scalars())


@approvals_router.get("/approvals/{approval_id}", response_model=ApprovalRead)
def get_approval(approval_id: UUID, session: DbSession) -> Approval:
    approval = session.get(Approval, approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="approval not found")
    return approval


@approvals_router.post(
    "/approvals/{approval_id}/decision",
    response_model=ApprovalRead,
)
def decide_approval(
    approval_id: UUID,
    body: ApprovalDecision,
    session: DbSession,
    redis_client: RedisClient,
    user: CurrentUser,
) -> Approval:
    approval = session.get(Approval, approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="approval not found")
    if approval.status is not ApprovalStatus.pending:
        raise HTTPException(
            status_code=409,
            detail=f"approval is {approval.status.value}, not pending",
        )

    if body.approved:
        approval.status = (
            ApprovalStatus.edited if body.edited_args else ApprovalStatus.approved
        )
    else:
        approval.status = ApprovalStatus.denied
    approval.decided_by = user.id
    approval.decided_at = datetime.now(tz=UTC)

    decision_args: dict[str, object] = {"approved": body.approved}
    if body.edited_args:
        decision_args["edited_args"] = body.edited_args
    if body.reason:
        decision_args["reason"] = body.reason
    approval.decision_args = decision_args

    # Approving "for the session" grants a standing per-(Project, tool)
    # authorization so future in-scope calls to this tool auto-run. Reuse an
    # existing active grant rather than duplicating it.
    if body.approved and body.remember_for_session:
        grant = session.execute(
            select(Authorization).where(
                Authorization.project_id == approval.project_id,
                Authorization.tool_name == approval.tool_name,
                Authorization.revoked_at.is_(None),
            )
        ).scalar_one_or_none()
        if grant is None:
            grant = Authorization(
                project_id=approval.project_id,
                tool_name=approval.tool_name,
                granted_by=user.id,
                note=f"granted while approving a {approval.tool_name} call",
            )
            session.add(grant)
            session.flush()  # assign grant.id
            session.add(
                AuditLog(
                    project_id=approval.project_id,
                    actor_type=ActorType.user,
                    actor_id=str(user.id),
                    event_type="authorization.granted",
                    payload={
                        "authorization_id": str(grant.id),
                        "tool": approval.tool_name,
                        "via_approval_id": str(approval.id),
                    },
                )
            )
        approval.authorization_id = grant.id

    session.add(
        AuditLog(
            project_id=approval.project_id,
            actor_type=ActorType.user,
            actor_id=str(user.id),
            event_type="approval.decided",
            payload={
                "approval_id": str(approval.id),
                "thread_id": approval.thread_id,
                "tool": approval.tool_name,
                "status": approval.status.value,
                "approved": body.approved,
                **({
                    "edited_args": body.edited_args} if body.edited_args else {}),
                **({"reason": body.reason} if body.reason else {}),
            },
        )
    )
    session.commit()
    session.refresh(approval)

    resume_payload: dict[str, object] = {
        "type": "run.resume",
        "thread_id": approval.thread_id,
        "approved": body.approved,
    }
    if body.edited_args:
        resume_payload["edited_args"] = body.edited_args
    if body.reason:
        resume_payload["reason"] = body.reason

    # Carry the original run's model choice forward so the worker uses the
    # same LLM on resume. Missing only if the cache TTL expired (>6h since
    # run.start) — in that case the worker falls back to env defaults.
    cached_model = load_run_model(redis_client, approval.thread_id)
    if cached_model is not None:
        resume_payload["model"] = cached_model

    redis_client.xadd(
        inbound_stream(approval.project_id),
        encode_command(resume_payload),
    )

    return approval


# ---------------------------------------------------------------------------
# authorizations router
# ---------------------------------------------------------------------------

"""Session authorizations HTTP surface.

- ``GET  /engagements/{project_id}/authorizations?active=true`` — list grants
- ``POST /authorizations/{id}/revoke``                             — revoke one

Grants are *created* implicitly by approving an interrupt with
``remember_for_session`` (see ``app.api.approvals``); this module only lists and
revokes them. Revoking sets ``revoked_at`` (the grant history is preserved) and
takes effect immediately — the worker's authorizer queries live on each call.
"""

authorizations_router = APIRouter()


@authorizations_router.get(
    "/engagements/{project_id}/authorizations",
    response_model=list[AuthorizationRead],
)
def list_authorizations(
    project_id: UUID,
    session: DbSession,
    active: Annotated[bool | None, Query(description="Filter by active/revoked.")] = None,
) -> list[Authorization]:
    stmt = select(Authorization).where(Authorization.project_id == project_id)
    if active is True:
        stmt = stmt.where(Authorization.revoked_at.is_(None))
    elif active is False:
        stmt = stmt.where(Authorization.revoked_at.is_not(None))
    stmt = stmt.order_by(Authorization.created_at.desc())
    return list(session.execute(stmt).scalars())


@authorizations_router.post("/authorizations/{authorization_id}/revoke", response_model=AuthorizationRead)
def revoke_authorization(
    authorization_id: UUID,
    session: DbSession,
    user: CurrentUser,
) -> Authorization:
    grant = session.get(Authorization, authorization_id)
    if grant is None:
        raise HTTPException(status_code=404, detail="authorization not found")
    if grant.revoked_at is not None:
        return grant  # idempotent — already revoked

    grant.revoked_at = datetime.now(tz=UTC)
    grant.revoked_by = user.id
    session.add(
        AuditLog(
            project_id=grant.project_id,
            actor_type=ActorType.user,
            actor_id=str(user.id),
            event_type="authorization.revoked",
            payload={"authorization_id": str(grant.id), "tool": grant.tool_name},
        )
    )
    session.commit()
    session.refresh(grant)
    return grant


# ---------------------------------------------------------------------------
# provider_keys router
# ---------------------------------------------------------------------------

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

provider_keys_router = APIRouter()


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
            project_id=None,
            actor_type=ActorType.user,
            actor_id=str(user_id),
            event_type=event_type,
            payload=payload,
        )
    )


# ── list / read ──────────────────────────────────────────────────────────


@provider_keys_router.get("/me/provider-keys", response_model=list[ProviderKeyRead])
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


@provider_keys_router.get("/me/provider-keys/{key_id}", response_model=ProviderKeyRead)
def get_my_provider_key(
    key_id: uuid.UUID, session: DbSession, user: CurrentUser
) -> UserProviderKey:
    row = session.get(UserProviderKey, key_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="provider key not found")
    return row


# ── create one ───────────────────────────────────────────────────────────


@provider_keys_router.post(
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


@provider_keys_router.post(
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


@provider_keys_router.patch(
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


@provider_keys_router.delete(
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
