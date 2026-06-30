"""Immutable per-finding narrative summary history (v0.7.0).

Every Save Summary click on the Findings slide-over inserts one row here.
``findings.summary`` is kept as a denormalized cache of the latest body
so downstream consumers (Report tab, JSON export, MCP server) can keep
reading "the current summary" without joining. The slide-over shows the
full timeline; the cache stays in sync via the same write path.

Immutable on purpose: no edit / no soft-delete. Treat each entry as the
audit-record of what was disclosed about a finding at a point in time.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, uuid7


class FindingSummary(Base):
    __tablename__ = "finding_summaries"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    finding_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("findings.id", ondelete="CASCADE"),
        nullable=False,
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    author_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
