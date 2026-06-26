from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, uuid7


class Authorization(Base, TimestampMixin):
    """A standing per-(Project, tool) approval — a "session grant".

    While active (``revoked_at`` is NULL), the gate auto-approves in-scope calls
    to ``tool_name`` for this Project instead of interrupting for a human;
    each such auto-approval is still written to the audit log carrying this
    row's id. Created when an operator approves a pending interrupt with
    "remember for this session", and lives until revoked or the Project is
    flushed (FK cascade).

    A partial unique index keeps at most one *active* grant per (Project,
    tool); revoking sets ``revoked_at`` rather than deleting, so the grant
    history survives.
    """

    __tablename__ = "authorizations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tool_name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    granted_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    note: Mapped[str | None] = mapped_column(String(500))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
