from __future__ import annotations

import enum
import uuid

from sqlalchemy import Boolean, Enum, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, uuid7


class UserRole(enum.StrEnum):
    """RBAC tier (locked 2026-06-29).

    - ``admin`` — full access (settings, integrations, hard-delete,
      approve feedback)
    - ``user`` — start/stop engagements, submit feedback, run agents
    - ``guest`` — view-only (no mutations anywhere)
    """

    admin = "admin"
    user = "user"
    guest = "guest"


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    email: Mapped[str] = mapped_column(
        String(320), unique=True, nullable=False, index=True
    )
    display_name: Mapped[str | None] = mapped_column(String(200))
    entra_oid: Mapped[str | None] = mapped_column(
        String(64), unique=True, index=True
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role"),
        default=UserRole.user,
        nullable=False,
    )
    # v1.4.11: per-analyst default model (roadmap #3 / #12). Nullable —
    # users who never pick one keep the built-in default. Set from the
    # Keys settings page; the Start-a-run prompt pre-selects it.
    default_llm_provider: Mapped[str | None] = mapped_column(String(60))
    default_llm_model: Mapped[str | None] = mapped_column(String(128))

    # ``is_admin`` was the pre-RBAC boolean; keep a read-only property so
    # existing call sites don't all need rewriting in one go.
    @property
    def is_admin(self) -> bool:
        return self.role == UserRole.admin

    @property
    def is_guest(self) -> bool:
        return self.role == UserRole.guest
