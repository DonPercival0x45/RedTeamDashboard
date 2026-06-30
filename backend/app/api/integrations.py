"""HTTP surface for the v0.9.0 Integrations tab.

Pre-v0.9 routed under ``/integrations/{type}`` because rows were unique
by type. v0.9 keys by row id so multi-row-per-type works (two Discord
webhooks, one for feedback and one for status_alerts, etc):

    GET    /integrations              -> list (masked)
    POST   /integrations              -> create
    GET    /integrations/{id}         -> read (masked)
    PATCH  /integrations/{id}         -> update
    DELETE /integrations/{id}         -> remove

All admin-gated. Audit log captures created / updated / deleted.
"""
from __future__ import annotations

import uuid
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Response, status

from app.api.deps import CurrentAdminUser, DbSession
from app.models import (
    ActorType,
    AuditLog,
    Integration,
)
from app.schemas.integration import (
    IntegrationCreate,
    IntegrationRead,
    IntegrationUpdate,
    mask_config,
)
from app.services import integrations as integration_svc

logger = structlog.get_logger(__name__)

router = APIRouter()


def _to_read(row: Integration) -> IntegrationRead:
    return IntegrationRead(
        id=row.id,
        type=row.type,
        purpose=row.purpose,
        name=row.name,
        display_name=row.display_name,
        logo_url=row.logo_url,
        enabled=row.enabled,
        config=mask_config(dict(row.config or {})),
        created_by_user_id=row.created_by_user_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _audit(
    session: DbSession,
    user_id: Any,
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


@router.get("/integrations", response_model=list[IntegrationRead])
def list_integrations(
    session: DbSession, _admin: CurrentAdminUser
) -> list[IntegrationRead]:
    rows = integration_svc.list_all(session)
    return [_to_read(r) for r in rows]


@router.post(
    "/integrations",
    response_model=IntegrationRead,
    status_code=status.HTTP_201_CREATED,
)
def create_integration(
    body: IntegrationCreate,
    session: DbSession,
    admin: CurrentAdminUser,
) -> IntegrationRead:
    row = integration_svc.create(
        session,
        integration_type=body.type,
        purpose=body.purpose,
        name=body.name,
        display_name=body.display_name,
        logo_url=body.logo_url,
        enabled=body.enabled,
        config=body.config,
        actor_user_id=admin.id,
    )
    _audit(
        session,
        admin.id,
        "integration.created",
        {
            "id": str(row.id),
            "type": row.type,
            "purpose": row.purpose.value,
            "enabled": row.enabled,
            "config_keys": sorted(row.config.keys()),
        },
    )
    session.commit()
    session.refresh(row)
    return _to_read(row)


@router.get(
    "/integrations/{integration_id}", response_model=IntegrationRead
)
def get_integration(
    integration_id: uuid.UUID,
    session: DbSession,
    _admin: CurrentAdminUser,
) -> IntegrationRead:
    row = integration_svc.get_by_id(session, integration_id)
    if row is None:
        raise HTTPException(status_code=404, detail="integration not found")
    return _to_read(row)


@router.patch(
    "/integrations/{integration_id}", response_model=IntegrationRead
)
def update_integration(
    integration_id: uuid.UUID,
    body: IntegrationUpdate,
    session: DbSession,
    admin: CurrentAdminUser,
) -> IntegrationRead:
    row = integration_svc.get_by_id(session, integration_id)
    if row is None:
        raise HTTPException(status_code=404, detail="integration not found")
    integration_svc.update(
        session,
        row,
        purpose=body.purpose,
        name=body.name,
        display_name=body.display_name,
        logo_url=body.logo_url,
        enabled=body.enabled,
        config=body.config,
    )
    _audit(
        session,
        admin.id,
        "integration.updated",
        {
            "id": str(row.id),
            "type": row.type,
            "purpose": row.purpose.value,
            "enabled": row.enabled,
            "config_keys": sorted(row.config.keys()),
        },
    )
    session.commit()
    session.refresh(row)
    return _to_read(row)


@router.delete(
    "/integrations/{integration_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_integration(
    integration_id: uuid.UUID,
    session: DbSession,
    admin: CurrentAdminUser,
) -> Response:
    row = integration_svc.get_by_id(session, integration_id)
    if row is None:
        raise HTTPException(status_code=404, detail="integration not found")
    captured = {
        "id": str(row.id),
        "type": row.type,
        "purpose": row.purpose.value,
    }
    integration_svc.delete(session, integration_id)
    _audit(session, admin.id, "integration.deleted", captured)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
