"""HTTP surface for external-system integrations (Discord today).

Endpoints (all admin-gated)::

    GET    /integrations                  -> list all (masked)
    GET    /integrations/{type}           -> read one (masked)
    PUT    /integrations/{type}           -> upsert
    DELETE /integrations/{type}           -> remove
"""
from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import select

from app.api.deps import CurrentAdminUser, DbSession
from app.models import (
    ActorType,
    AuditLog,
    Integration,
    IntegrationType,
)
from app.schemas.integration import (
    IntegrationRead,
    IntegrationUpsert,
    mask_config,
)
from app.services import integrations as integration_svc

logger = structlog.get_logger(__name__)

router = APIRouter()


def _to_read(row: Integration) -> IntegrationRead:
    return IntegrationRead(
        id=row.id,
        type=row.type,
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
    session: DbSession, admin: CurrentAdminUser
) -> list[IntegrationRead]:
    rows = list(session.execute(select(Integration)).scalars())
    return [_to_read(r) for r in rows]


@router.get(
    "/integrations/{integration_type}", response_model=IntegrationRead
)
def get_integration(
    integration_type: IntegrationType,
    session: DbSession,
    admin: CurrentAdminUser,
) -> IntegrationRead:
    row = integration_svc.get_by_type(session, integration_type)
    if row is None:
        raise HTTPException(status_code=404, detail="integration not configured")
    return _to_read(row)


@router.put(
    "/integrations/{integration_type}", response_model=IntegrationRead
)
def upsert_integration(
    integration_type: IntegrationType,
    body: IntegrationUpsert,
    session: DbSession,
    admin: CurrentAdminUser,
) -> IntegrationRead:
    if body.type != integration_type:
        raise HTTPException(
            status_code=400,
            detail=f"path type {integration_type.value} doesn't match body type {body.type.value}",
        )
    row = integration_svc.upsert(
        session,
        integration_type=integration_type,
        enabled=body.enabled,
        config=body.config,
        actor_user_id=admin.id,
    )
    _audit(
        session,
        admin.id,
        "integration.upserted",
        {
            "type": integration_type.value,
            "enabled": row.enabled,
            "config_keys": sorted(row.config.keys()),
        },
    )
    session.commit()
    session.refresh(row)
    return _to_read(row)


@router.delete(
    "/integrations/{integration_type}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_integration(
    integration_type: IntegrationType,
    session: DbSession,
    admin: CurrentAdminUser,
) -> Response:
    removed = integration_svc.delete(session, integration_type)
    if not removed:
        raise HTTPException(status_code=404, detail="integration not configured")
    _audit(
        session,
        admin.id,
        "integration.deleted",
        {"type": integration_type.value},
    )
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
