from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, uuid7


class EntityReviewDisposition(enum.StrEnum):
    kept = "kept"
    excluded = "excluded"


class EntityReview(Base, TimestampMixin):
    """Durable analyst disposition for one canonical derived-entity identity."""

    __tablename__ = "entity_reviews"
    __table_args__ = (
        UniqueConstraint(
            "engagement_id",
            "entity_type",
            "normalized_value",
            name="uq_entity_review_identity",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    engagement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("engagements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    entity_type: Mapped[str] = mapped_column(String(80), nullable=False)
    normalized_value: Mapped[str] = mapped_column(String(500), nullable=False)
    display_value: Mapped[str] = mapped_column(String(500), nullable=False)
    disposition: Mapped[EntityReviewDisposition] = mapped_column(
        Enum(EntityReviewDisposition, name="entity_review_disposition"),
        nullable=False,
        index=True,
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    reviewed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    row_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class EntityReviewScopeLink(Base, TimestampMixin):
    """Active ownership of an exact exclusion created by entity review."""

    __tablename__ = "entity_review_scope_links"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    entity_review_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("entity_reviews.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    scope_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("scope_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    released_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    release_reason: Mapped[str | None] = mapped_column(Text)


class EntityReviewFindingLink(Base, TimestampMixin):
    """Reversible attribution for a Finding exclusion caused by entity review."""

    __tablename__ = "entity_review_finding_links"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    entity_review_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("entity_reviews.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    finding_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("findings.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    previous_exclusion: Mapped[str | None] = mapped_column(String(32))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    released_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    release_reason: Mapped[str | None] = mapped_column(Text)
