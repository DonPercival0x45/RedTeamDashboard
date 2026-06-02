"""SQLAlchemy declarative base + shared column primitives."""
from __future__ import annotations

import uuid
from datetime import datetime

import uuid_utils
from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def uuid7() -> uuid.UUID:
    """Return a UUIDv7 as a stdlib uuid.UUID.

    Postgres 16 lacks native uuidv7(); generated app-side and stored as UUID.
    """
    return uuid.UUID(bytes=uuid_utils.uuid7().bytes)


class Base(DeclarativeBase):
    """All ORM models inherit from this so they share a single MetaData."""


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
