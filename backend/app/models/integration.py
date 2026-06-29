"""External-system integrations (Discord first; Slack/Teams later).

Single-tenant by design — at most one row per ``type`` (enforced via the
unique constraint on the ``type`` column in migration 0020). The
``config`` JSONB carries whatever fields the integration needs:

- Discord: ``{webhook_url, bot_token, channel_id, last_seen_message_id}``

Admin-only surface — managed from ``/settings/feedback``.
"""
from __future__ import annotations

import enum
import uuid
from typing import Any

from sqlalchemy import Boolean, Enum, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, uuid7


class IntegrationType(enum.StrEnum):
    discord = "discord"


class Integration(Base, TimestampMixin):
    __tablename__ = "integrations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    type: Mapped[IntegrationType] = mapped_column(
        Enum(IntegrationType, name="integration_type"),
        nullable=False,
        unique=True,
    )
    config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
