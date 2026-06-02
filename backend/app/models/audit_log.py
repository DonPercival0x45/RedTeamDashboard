from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Enum, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, uuid7


class ActorType(enum.StrEnum):
    user = "user"
    agent = "agent"
    system = "system"


class AuditLog(Base):
    """Append-only log of authorization-relevant events.

    Immutability is enforced at the DB layer via a BEFORE UPDATE/DELETE trigger
    (see 0001_initial migration). The trigger respects a session-local bypass
    flag set only inside the SECURITY DEFINER flush_engagement() helper.
    """

    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    engagement_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("engagements.id", ondelete="CASCADE"),
        index=True,
    )
    actor_type: Mapped[ActorType] = mapped_column(
        Enum(ActorType, name="actor_type"), nullable=False
    )
    actor_id: Mapped[str | None] = mapped_column(String(200), index=True)
    event_type: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
