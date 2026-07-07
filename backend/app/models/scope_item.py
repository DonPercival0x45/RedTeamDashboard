from __future__ import annotations

import enum
import uuid

from sqlalchemy import Boolean, Enum, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, uuid7


class ScopeKind(enum.StrEnum):
    domain = "domain"
    cidr = "cidr"
    ip = "ip"
    url = "url"


class ScopeItem(Base, TimestampMixin):
    __tablename__ = "scope_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    engagement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("engagements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[ScopeKind] = mapped_column(Enum(ScopeKind, name="scope_kind"), nullable=False)
    value: Mapped[str] = mapped_column(String(500), nullable=False)
    is_exclusion: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    note: Mapped[str | None] = mapped_column(String(500))
    # v1.4.13: provenance (roadmap #5). ``defined`` = original client-
    # provided scope; ``found`` = added from findings / discovered
    # mid-engagement (UI tints green). Defaults to ``defined`` so legacy
    # rows and the importer stay client-scope.
    source: Mapped[str] = mapped_column(String(20), default="defined", nullable=False)
